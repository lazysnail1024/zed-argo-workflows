package analysis

import (
	"context"
	"github.com/lazysnail1024/k8s-crd-toolkit/source"
	"github.com/lazysnail1024/k8s-crd-toolkit/yamlview"
	"github.com/lazysnail1024/zed-argo-workflows/internal/assets"
)

type snapshotIdentity struct {
	uri      source.URI
	revision source.Revision
	text     string
}
type projectionIdentity struct {
	snapshotIdentity
	version string
	span    source.Span
}
type invocationKey struct{}
type invocation struct {
	imported    *Facts
	views       map[snapshotIdentity]yamlview.View
	sharedViews map[string]yamlview.View
	projections map[projectionIdentity]projectedDocument
	bytes       int
}

// WithInvocation reuses immutable parsing/projection results only inside one
// sequential analysis. Exact text is part of identity, including simulated edits.
func WithInvocation(ctx context.Context) context.Context {
	ctx = assets.WithDocumentReuse(ctx)
	if _, ok := ctx.Value(symbolQueryKey{}).(*symbolQueryBudget); !ok {
		ctx = context.WithValue(ctx, symbolQueryKey{}, &symbolQueryBudget{remaining: symbolQueryWorkLimit})
	}
	if _, ok := ctx.Value(expansionKey{}).(*expansionBudget); !ok {
		ctx = WithLogicalResult(ctx)
	}
	if _, ok := ctx.Value(invocationKey{}).(*invocation); ok {
		return ctx
	}
	return context.WithValue(ctx, invocationKey{}, &invocation{views: map[snapshotIdentity]yamlview.View{}, sharedViews: map[string]yamlview.View{}, projections: map[projectionIdentity]projectedDocument{}})
}
func snapshotKey(s source.Snapshot) snapshotIdentity {
	return snapshotIdentity{s.URI(), s.Revision(), s.Text()}
}
func parseView(ctx context.Context, s source.Snapshot) (yamlview.View, error) {
	if err := ctx.Err(); err != nil {
		return yamlview.View{}, err
	}
	state, _ := ctx.Value(invocationKey{}).(*invocation)
	key := snapshotKey(s)
	if state != nil {
		if v, ok := state.views[key]; ok {
			return v, nil
		}
		// Views contain immutable bytes and spans, not URI/revision/target state.
		// Reuse only byte-identical input; keep projection and fact identities
		// separate. Do not retain another identity key (and its text backing
		// storage) for this hit: the original 50 MiB cache budget still applies.
		if v, ok := state.sharedViews[key.text]; ok {
			return v, nil
		}
	}
	v, err := yamlview.Parse(ctx, s)
	if err == nil && ctx.Err() == nil && state != nil && state.bytes+len(s.Text()) <= 50*1024*1024 {
		state.views[key] = v
		state.sharedViews[key.text] = v
		state.bytes += len(s.Text())
	}
	return v, err
}
