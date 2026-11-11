package analysis

import (
	"context"
	"errors"

	"github.com/lazysnail1024/k8s-crd-toolkit/source"
	"github.com/lazysnail1024/k8s-crd-toolkit/yamlvalue"
	"github.com/lazysnail1024/k8s-crd-toolkit/yamlview"
	"github.com/lazysnail1024/zed-argo-workflows/compat"
)

func location(root yamlview.Node, path []string) (yamlview.Node, string) {
	node := root
	for _, part := range path {
		if node.Kind() == yamlview.Alias {
			return node, "derived"
		}
		if node.Kind() == yamlview.Mapping {
			next := member(node, part)
			if next.Kind() == yamlview.Empty {
				return node, "derived"
			}
			node = next
		} else if node.Kind() == yamlview.Sequence {
			next := at(node, []string{part})
			if next.Span() == node.Span() {
				return node, "ambiguous"
			}
			node = next
		} else {
			return node, "ambiguous"
		}
	}
	if node.Kind() == yamlview.Alias {
		return node, "derived"
	}
	return node, "exact"
}

// project keeps release ownership and logical-result admission in the plugin.
// Shared YAML projection owns strict decoding, aliases, tags and source sharing.
func project(ctx context.Context, snapshot source.Snapshot, doc yamlview.Document, version string) (any, error) {
	if err := ctx.Err(); err != nil {
		return nil, err
	}
	state, _ := ctx.Value(invocationKey{}).(*invocation)
	key := projectionIdentity{snapshotKey(snapshot), version, doc.Span()}
	if state != nil {
		if value, ok := state.projections[key]; ok {
			if err := admitProjection(ctx, key, value.graphNodes); err != nil {
				return nil, err
			}
			return value.value, nil
		}
	}
	projected, err := yamlvalue.Project(ctx, snapshot, doc, yamlvalue.Options{MaxNodes: 100000, MaxVisits: 100000, RejectExplicitTags: true})
	if err != nil {
		var failure *yamlvalue.Error
		if errors.As(err, &failure) && failure.Code == "INPUT_BUDGET_EXCEEDED" {
			if budget, ok := ctx.Value(expansionKey{}).(*expansionBudget); ok {
				budget.err = err
			}
		}
		return nil, err
	}
	value := projected.Value
	root, _ := value.(map[string]any)
	api, _ := root["apiVersion"].(string)
	kind, _ := root["kind"].(string)
	if owned(api, kind) {
		if _, err := compat.SchemaFile(version, api, kind); err != nil {
			// Schema-only kinds absent from older releases remain projectable so
			// validation can report the release boundary without template scopes.
			if !schemaOnlyKind(kind) || !errors.Is(err, compat.ErrSchemaUnavailable) {
				return nil, err
			}
		}
	}
	if err := admitProjection(ctx, key, projected.LogicalNodes); err != nil {
		return nil, err
	}
	if state != nil && ctx.Err() == nil {
		if _, ok := state.views[key.snapshotIdentity]; ok {
			state.projections[key] = projectedDocument{value: value, graphNodes: projected.LogicalNodes, shared: projected.Shared}
		}
	}
	return value, nil
}
