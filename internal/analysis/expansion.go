package analysis

import (
	"context"
	"fmt"

	"github.com/lazysnail1024/k8s-crd-toolkit/source"
	"github.com/lazysnail1024/k8s-crd-toolkit/yamlvalue"
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

type projectedDocument struct {
	value      any
	graphNodes int
	shared     map[source.Span]bool
}

func sharedOrigins(ctx context.Context, snapshot source.Snapshot, doc yamlview.Document, version string) map[source.Span]bool {
	state, _ := ctx.Value(invocationKey{}).(*invocation)
	if state != nil {
		if value, ok := state.projection(projectionIdentity{snapshotKey(snapshot), version, doc.Span()}); ok {
			return value.shared
		}
	}
	// Artificial completion snapshots intentionally bypass reusable facts.
	projected, err := yamlvalue.Project(ctx, snapshot, doc, yamlvalue.Options{MaxNodes: 100000, MaxVisits: 100000, RejectExplicitTags: true})
	if err != nil {
		return nil
	}
	return projected.Shared
}
func sharedOrigin(shared map[source.Span]bool, span source.Span) bool {
	return yamlvalue.Shared(shared, span)
}
