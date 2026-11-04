package analysis

import (
	"context"
	"fmt"

	"github.com/lazysnail1024/k8s-crd-toolkit/source"
	"github.com/lazysnail1024/k8s-crd-toolkit/yamlview"
)

// Each logical result owns its admission decisions. Immutable projection reuse
// retains only the deterministic cost, never a previous result's admission.
type expansionKey struct{}
type expansionBudget struct {
	nodes    int
	admitted map[projectionIdentity]bool
	err      error
}

func WithLogicalResult(ctx context.Context) context.Context {
	return context.WithValue(ctx, expansionKey{}, &expansionBudget{admitted: map[projectionIdentity]bool{}})
}

func ExpansionError(ctx context.Context) error {
	if b, ok := ctx.Value(expansionKey{}).(*expansionBudget); ok {
		return b.err
	}
	return nil
}

func admitProjection(ctx context.Context, key projectionIdentity, cost int) error {
	b, _ := ctx.Value(expansionKey{}).(*expansionBudget)
	if b == nil || cost == 0 || b.admitted[key] {
		return nil
	}
	if cost > 100000-b.nodes {
		b.err = fmt.Errorf("aggregate alias/merge expansion exceeds 100000 logical nodes")
		return b.err
	}
	b.nodes += cost
	b.admitted[key] = true
	return nil
}

func graphDocument(n yamlview.Node) bool {
	if n.Kind() == yamlview.Alias {
		return true
	}
	for _, m := range n.Members() {
		if m.Key.Raw() == "<<" || graphDocument(m.Value) {
			return true
		}
	}
	for _, e := range n.Elements() {
		if graphDocument(e) {
			return true
		}
	}
	return false
}

// Containers, mapping keys and scalar/null values each count once. Repeated
// expanded occurrences count separately; documents without a graph cost zero.
func logicalNodes(value any) int {
	count := 1
	switch v := value.(type) {
	case map[string]any:
		for _, child := range v {
			count += 1 + logicalNodes(child)
		}
	case []any:
		for _, child := range v {
			count += logicalNodes(child)
		}
	}
	return count
}

type projectedDocument struct {
	value      any
	graphNodes int
	shared     map[source.Span]bool
}

func sharedOrigins(ctx context.Context, snapshot source.Snapshot, doc yamlview.Document, version string) map[source.Span]bool {
	state, _ := ctx.Value(invocationKey{}).(*invocation)
	if state != nil {
		if value, ok := state.projections[projectionIdentity{snapshotKey(snapshot), version, doc.Span()}]; ok {
			return value.shared
		}
	}
	shared := map[source.Span]bool{}
	// Artificial completion snapshots intentionally bypass reusable facts.
	// Their source provenance still follows the same bounded preflight.
	_ = preflight(ctx, snapshot, doc, shared)
	return shared
}
func sharedOrigin(shared map[source.Span]bool, span source.Span) bool {
	for parent := range shared {
		if span.Start >= parent.Start && span.End <= parent.End {
			return true
		}
	}
	return false
}
