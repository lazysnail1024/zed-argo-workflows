package analysis

import (
	"context"
	"crypto/sha256"
	"encoding/json"
	"fmt"
	"strconv"
	"strings"

	"github.com/lazysnail1024/k8s-crd-toolkit/index"
	"github.com/lazysnail1024/zed-argo-workflows/internal/workspace"
)

// FileSummary is the persistent part of one successful parse. It owns no source
// snapshot, YAML node, projection, or invocation. Detailed domain analysis still
// uses the existing engine on a bounded set of current dependency inputs.
type FileSummary struct {
	Facts   index.Facts
	Symbols []DomainSymbol
	Owned   bool
}

// ResourceAlternatives supplies a bounded set for the existing safe quick-fix
// evaluator. Keys encode this product's kind/namespace/name identity; the shared
// index itself never interprets these fields.
func ResourceAlternatives(known *index.Index, scope index.Scope, missing index.Key) ([]index.Definition, error) {
	var wanted [3]string
	if json.Unmarshal([]byte(missing), &wanted) != nil {
		return nil, nil
	}
	prefix, _ := json.Marshal(wanted[:2])
	prefix[len(prefix)-1] = ','
	candidates, err := known.SymbolsWithPrefix(scope, index.Key(prefix), "", 1000)
	if err != nil {
		return nil, err
	}
	if candidates.Truncated {
		return nil, fmt.Errorf("resource repair candidates exceed 1000 matching entries")
	}
	return candidates.Items, nil
}

func resourceIndexKey(kind, namespace, name string) index.Key {
	encoded, _ := json.Marshal([3]string{kind, namespace, name})
	return index.Key(encoded)
}

// Summarize records unresolved resource identities too. Template signatures,
// arguments, outputs, inheritance and ownership participate in the semantic
// digest even when their resource name and reference target are unchanged.
func Summarize(ctx context.Context, header workspace.Workspace, document workspace.Document) (FileSummary, error) {
	header.Documents = []workspace.Document{document}
	header.IncompleteScopes = map[string]bool{}
	resources, complete, progress, _ := externalResources(ctx, header, false)
	if err := ctx.Err(); err != nil {
		return FileSummary{}, err
	}
	if !complete[document.Target.ID] {
		return FileSummary{}, fmt.Errorf("resource recognition or projection is incomplete")
	}
	result := FileSummary{Owned: len(resources) != 0 || len(progress[document.Snapshot.URI()].Resources) != 0}
	meaning := []any{document.Target.Version, header.Config.DefaultNamespace, header.Config.ReferencePolicy}
	for n, resource := range resources {
		namespace := resource.namespace
		if resource.kind == "ClusterWorkflowTemplate" {
			namespace = ""
		}
		meaning = append(meaning, []any{resource.kind, namespace, resource.name, resource.semanticSpec.value})
		if resource.name != "" {
			selection := member(member(resource.root, "metadata"), "name").Span()
			if selection.Start < resource.root.Span().Start || selection.End > resource.root.Span().End || selection.Start == selection.End {
				selection = resource.root.Span()
			}
			result.Facts.Symbols = append(result.Facts.Symbols, index.Symbol{ID: index.SymbolID("resource:" + strconv.Itoa(n)), Key: resourceIndexKey(resource.kind, namespace, resource.name), Name: strings.Clone(resource.name), Kind: strings.Clone(resource.kind), Span: resource.root.Span(), Selection: selection})
		}
		externalCalls(resource, func(ref, call workflowValue, whole bool) {
			identity, ok := candidateIdentity(resource, ref)
			if !ok || identity.name == "" || strings.Contains(identity.name, "{{") {
				return
			}
			key := resourceIndexKey(identity.kind, identity.namespace, identity.name)
			name := workflowMember(ref, "name")
			_, span, exact := exactName(name.node)
			if name.origin == "exact" && exact && !sharedOrigin(resource.shared, span) {
				result.Facts.References = append(result.Facts.References, index.Reference{Target: key, Span: span})
			} else {
				result.Facts.Dependencies = append(result.Facts.Dependencies, key)
			}
		})
	}
	encoded, err := json.Marshal(meaning)
	if err != nil {
		return FileSummary{}, err
	}
	result.Facts.SemanticDigest = sha256.Sum256(encoded)
	if len(resources) > 0 {
		result.Symbols, err = boundedDomainSymbols(ctx, document.Snapshot, document.Target.Version, 8*1024*1024)
		if err != nil {
			return FileSummary{}, err
		}
		for n := range result.Symbols {
			symbol := &result.Symbols[n]
			symbol.Name = strings.Clone(symbol.Name)
			symbol.Container = strings.Clone(symbol.Container)
			symbol.Location.URI = strings.Clone(symbol.Location.URI)
		}
	}
	if err := ctx.Err(); err != nil {
		return FileSummary{}, err
	}
	if err := ExpansionError(ctx); err != nil {
		return FileSummary{}, err
	}
	return result, nil
}
