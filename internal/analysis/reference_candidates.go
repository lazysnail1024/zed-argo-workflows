package analysis

import (
	"context"
	"encoding/json"

	"github.com/lazysnail1024/k8s-crd-toolkit/index"
	"github.com/lazysnail1024/k8s-crd-toolkit/source"
	"github.com/lazysnail1024/zed-argo-workflows/internal/workspace"
)

const referenceCandidateQueryLimit = 1000
const referenceCandidateDisplayLimit = 20

// ReferenceCandidate describes a same-name resource for inspection. Its
// location and identity are not an external binding or permission to edit.
type ReferenceCandidate struct {
	Kind      string   `json:"kind"`
	Name      string   `json:"name"`
	Namespace string   `json:"namespace"`
	Location  Location `json:"location"`
	*Refusal
}

// Complete concerns this bounded candidate enumeration only. ScopeComplete
// separately records discovery/analysis readiness; neither proves a binding.
type ReferenceCandidateExplanation struct {
	Label         string               `json:"label"`
	Selection     Location             `json:"selection"`
	ScopeComplete bool                 `json:"scopeComplete"`
	Truncated     bool                 `json:"truncated"`
	Complete      bool                 `json:"complete"`
	Items         []ReferenceCandidate `json:"items"`
	*Refusal
}

func referenceCandidateRefusal() *Refusal {
	return Refuse("TARGET_UNKNOWN", "The caller namespace is unknown; a same-name resource does not establish a binding.")
}

// QueryReferenceCandidates uses the current caller only to select the query.
// The index supplies detached same-kind/name rows across namespaces in exactly
// its target scope. Ordinary definition, reference and edit callers do not use
// this path, including when the index contains a single candidate.
func QueryReferenceCandidates(ctx context.Context, document workspace.Document, config workspace.Config, pos source.Position, known *index.Index) (*ReferenceCandidateExplanation, []index.Definition, error) {
	name, selection, err := unknownNamespaceReference(ctx, document, config, pos)
	if err != nil || name == "" {
		return nil, nil, err
	}
	result := &ReferenceCandidateExplanation{Label: "Same-name resource candidates; binding unproven", Selection: selection,
		Items: []ReferenceCandidate{}, Refusal: referenceCandidateRefusal()}
	prefix, err := json.Marshal([]string{"WorkflowTemplate"})
	if err != nil {
		return nil, nil, err
	}
	prefix[len(prefix)-1] = ','
	rows, err := queryIndexedSymbols(ctx, known, index.Scope(document.Target.ID), index.Key(prefix), name, referenceCandidateQueryLimit)
	if err != nil {
		return nil, nil, err
	}
	result.Complete, result.Truncated = rows.Complete && !rows.Truncated, rows.Truncated
	used := []index.Definition{}
	for _, row := range rows.Items {
		if err := ctx.Err(); err != nil {
			return nil, nil, err
		}
		var identity [3]string
		if json.Unmarshal([]byte(row.Symbol.Key), &identity) != nil {
			result.Complete = false
			continue
		}
		// The shared query is a substring search whose limit precedes this
		// exact-name filter. Preserve its truncation even after filtering.
		if identity[0] != "WorkflowTemplate" || identity[2] != name || row.Symbol.Name != name {
			continue
		}
		if len(used) == referenceCandidateDisplayLimit {
			result.Truncated, result.Complete = true, false
			continue
		}
		// Distinct declarations with the same key, including documents in one
		// file, remain separate rows in the index's stable source order.
		used = append(used, row)
	}
	return result, used, nil
}

func unknownNamespaceReference(ctx context.Context, document workspace.Document, config workspace.Config, pos source.Position) (string, Location, error) {
	offset, err := document.Snapshot.Offset(pos)
	if err != nil {
		return "", Location{}, err
	}
	w := workspace.Workspace{Config: config, Documents: []workspace.Document{document}}
	resources, _, _, _ := externalResources(ctx, w, true)
	name, selection := "", Location{}
	for _, caller := range resources {
		if caller.namespace != "" || offset < caller.root.Span().Start || offset >= caller.root.Span().End {
			continue
		}
		externalCalls(caller, func(ref, _ workflowValue, _ bool) {
			value := workflowMember(ref, "name")
			candidate, span, exact := exactName(value.node)
			if !exact || !exactResourceName(value) || len(candidate) > 253 || !resourceNamePattern.MatchString(candidate) || offset < span.Start || offset >= span.End {
				return
			}
			cluster := workflowMember(ref, "clusterScope")
			if cluster.present && (cluster.value != false || cluster.origin != "exact") {
				return
			}
			name, selection = candidate, spanLocation(document.Snapshot, span)
		})
	}
	return name, selection, ctx.Err()
}

// ReferenceCandidateLocation rechecks a detached index row against its exact
// current source before exposing UTF-16 positions. Inexact/derived declarations
// stay undisplayed and make enumeration incomplete in the LSP adapter.
func ReferenceCandidateLocation(ctx context.Context, document workspace.Document, config workspace.Config, row index.Definition) (ReferenceCandidate, bool, error) {
	if document.Snapshot.URI() != row.URI || document.Snapshot.Revision() != row.Revision {
		return ReferenceCandidate{}, false, Refuse("STALE_INPUT", "resource candidate source changed")
	}
	w := workspace.Workspace{Config: config, Documents: []workspace.Document{document}}
	resources, _, _, _ := externalResources(ctx, w, false)
	for _, resource := range resources {
		if resource.root.Span() != row.Symbol.Span {
			continue
		}
		if resource.kind != "WorkflowTemplate" || resource.name != row.Symbol.Name || resourceIndexKey(resource.kind, resource.namespace, resource.name) != row.Symbol.Key {
			return ReferenceCandidate{}, false, Refuse("STALE_INPUT", "resource candidate identity changed")
		}
		// Reuse the resource identity bounds before repeating names in a
		// message/action. Malformed YAML strings must not amplify wire output.
		if resource.namespace != "" && !staticResourceNamespace(resource.namespace) {
			return ReferenceCandidate{}, false, nil
		}
		name, span, exact := exactName(member(member(resource.root, "metadata"), "name"))
		if !exact || name != resource.name || sharedOrigin(resource.shared, span) {
			return ReferenceCandidate{}, false, nil
		}
		start, startErr := document.Snapshot.Position(span.Start)
		end, endErr := document.Snapshot.Position(span.End)
		if startErr != nil || endErr != nil {
			return ReferenceCandidate{}, false, Refuse("STALE_INPUT", "resource candidate position is unavailable")
		}
		location := Location{URI: string(row.URI), Range: Range{Position{start.Line, start.UTF16Column}, Position{end.Line, end.UTF16Column}}}
		return ReferenceCandidate{Kind: resource.kind, Name: name, Namespace: resource.namespace, Location: location, Refusal: referenceCandidateRefusal()}, true, ctx.Err()
	}
	if err := ctx.Err(); err != nil {
		return ReferenceCandidate{}, false, err
	}
	return ReferenceCandidate{}, false, Refuse("STALE_INPUT", "resource candidate declaration changed")
}
