package analysis

import (
	"context"
	"crypto/sha256"
	"encoding/json"
	"github.com/lazysnail1024/k8s-crd-toolkit/source"
)

// ExternalFacts retains completed per-resource reference checks. Keys include
// the caller and the complete candidate sets for every external identity it
// reads. A new, deleted or ambiguous candidate therefore invalidates callers,
// while an unrelated resource change does not rebuild their binding checks.
// Retention is optional and bounded; it never changes admission or completeness.
type ExternalFacts struct{ checks map[[32]byte][]Diagnostic }
type externalFactsKey struct{}
type externalFactsState struct {
	prior *ExternalFacts
	used  map[[32]byte][]Diagnostic
	bytes int
}

func WithExternalFacts(ctx context.Context, previous *ExternalFacts) context.Context {
	return context.WithValue(ctx, externalFactsKey{}, &externalFactsState{prior: previous, used: map[[32]byte][]Diagnostic{}})
}
func CaptureExternalFacts(ctx context.Context) *ExternalFacts {
	state, _ := ctx.Value(externalFactsKey{}).(*externalFactsState)
	if state == nil || ctx.Err() != nil || len(state.used) == 0 {
		return nil
	}
	return &ExternalFacts{checks: state.used}
}
func externalFact(ctx context.Context, key [32]byte) ([]Diagnostic, bool) {
	state, _ := ctx.Value(externalFactsKey{}).(*externalFactsState)
	if state == nil {
		return nil, false
	}
	if found, ok := state.used[key]; ok {
		return found, true
	}
	if state.prior != nil {
		if found, ok := state.prior.checks[key]; ok {
			retainExternalFact(ctx, key, found)
			return found, true
		}
	}
	return nil, false
}
func retainExternalFact(ctx context.Context, key [32]byte, diagnostics []Diagnostic) {
	state, _ := ctx.Value(externalFactsKey{}).(*externalFactsState)
	if state == nil || ctx.Err() != nil || len(state.used) >= 20000 {
		return
	}
	if _, ok := state.used[key]; ok {
		return
	}
	size := 32
	for _, d := range diagnostics {
		size += len(d.URI) + len(d.Code) + len(d.Source) + len(d.Severity) + len(d.Message) + len(d.TargetID) + len(d.PositionKind) + 64
	}
	if size > 4*1024*1024-state.bytes {
		return
	}
	state.bytes += size
	state.used[key] = append([]Diagnostic(nil), diagnostics...)
}

type externalIdentity struct{ target, kind, namespace, name string }

func candidateIdentity(caller resource, ref workflowValue) (externalIdentity, bool) {
	name := workflowString(workflowMember(ref, "name"))
	cluster := false
	if value := workflowMember(ref, "clusterScope"); value.present {
		var ok bool
		cluster, ok = value.value.(bool)
		if !ok {
			return externalIdentity{}, false
		}
	}
	kind, namespace := "WorkflowTemplate", caller.namespace
	if cluster {
		kind, namespace = "ClusterWorkflowTemplate", ""
	} else if namespace == "" {
		return externalIdentity{}, false
	}
	return externalIdentity{caller.document.Target.ID, kind, namespace, name}, true
}
func externalCalls(caller resource, visit func(workflowValue, workflowValue, bool)) {
	if ref := workflowMember(caller.semanticSpec, "workflowTemplateRef"); ref.present {
		visit(ref, caller.semanticSpec, true)
	}
	hooks := func(n workflowValue) {
		for _, h := range workflowMember(n, "hooks").Members() {
			if ref := workflowMember(h.Value, "templateRef"); ref.present {
				visit(ref, h.Value, false)
			}
		}
	}
	hooks(caller.semanticSpec)
	for _, t := range workflowMember(caller.semanticSpec, "templates").Elements() {
		hooks(t)
		for _, task := range workflowMember(workflowMember(t, "dag"), "tasks").Elements() {
			if ref := workflowMember(task, "templateRef"); ref.present {
				visit(ref, task, false)
			}
			hooks(task)
		}
		for _, group := range workflowMember(t, "steps").Elements() {
			for _, step := range group.Elements() {
				if ref := workflowMember(step, "templateRef"); ref.present {
					visit(ref, step, false)
				}
				hooks(step)
			}
		}
	}
}
func resourceDigest(r resource, textHash [32]byte) [32]byte {
	// Text includes all documents in the source, so changes cannot reuse stale
	// source spans in another document from the same YAML stream.
	data, _ := json.Marshal(struct {
		URI                     source.URI
		Target, Version, Config string
		TextHash                [32]byte
		Span                    source.Span
	}{r.document.Snapshot.URI(), r.document.Target.ID, r.document.Target.Version, r.document.Target.ConfigHash, textHash, r.root.Span()})
	return sha256.Sum256(data)
}
