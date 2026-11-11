package analysis

import (
	"context"
	"encoding/json"
	"fmt"
	"reflect"
	"sort"

	"github.com/lazysnail1024/k8s-crd-toolkit/edit"
	"github.com/lazysnail1024/zed-argo-workflows/internal/workspace"
)

// SafeLocalPlan proves that an edit cannot affect another resource. Each side
// has its own logical-result budget; a failed local proof never consumes the
// budget of a subsequent full-scope proof.
func SafeLocalPlan(ctx context.Context, w workspace.Workspace, d workspace.Document, plan edit.Plan) error {
	next, err := edit.Apply(d.Snapshot, d.Snapshot.Revision()+1, plan)
	if err != nil {
		return err
	}
	if err := safePlan(ctx, d.Snapshot, d.Target.Version, plan); err != nil {
		return err
	}
	after := d
	after.Snapshot = next
	beforeBoundary, err := localEditBoundary(WithLogicalResult(ctx), w, d)
	if err != nil {
		return err
	}
	afterBoundary, err := localEditBoundary(WithLogicalResult(ctx), w, after)
	if err != nil {
		return err
	}
	if !reflect.DeepEqual(beforeBoundary, afterBoundary) {
		return fmt.Errorf("edit changes a resource identity, exported template or external call")
	}
	return ctx.Err()
}

// The boundary includes unmodeled nested references too. Their containing
// value must remain unchanged; a positive resolved-reference list alone cannot
// prove that a local edit leaves external callers and callees unaffected.
func localEditBoundary(ctx context.Context, w workspace.Workspace, d workspace.Document) ([]string, error) {
	view, err := parseView(ctx, d.Snapshot)
	if err != nil {
		return nil, err
	}
	result := []string{}
	for _, doc := range view.Documents() {
		value, err := project(ctx, d.Snapshot, doc, d.Target.Version)
		if err != nil {
			return nil, err
		}
		api, _ := queryMember(value, "apiVersion").(string)
		kind, _ := queryMember(value, "kind").(string)
		if !owned(api, kind) {
			continue
		}
		meta := queryMember(value, "metadata")
		identity := []any{api, kind, queryMember(meta, "name"), queryMember(meta, "generateName"), queryMember(meta, "namespace"), w.Config.DefaultNamespace}
		boundary := []any{identity}
		if kind == "WorkflowTemplate" || kind == "ClusterWorkflowTemplate" {
			boundary = append(boundary, queryMember(value, "spec"))
		}
		var calls func(any)
		calls = func(v any) {
			switch n := v.(type) {
			case map[string]any:
				if n["templateRef"] != nil || n["workflowTemplateRef"] != nil {
					boundary = append(boundary, n)
				}
				keys := make([]string, 0, len(n))
				for key := range n {
					keys = append(keys, key)
				}
				sort.Strings(keys)
				for _, key := range keys {
					calls(n[key])
				}
			case []any:
				for _, child := range n {
					calls(child)
				}
			}
		}
		calls(value)
		raw, err := json.Marshal(boundary)
		if err != nil {
			return nil, err
		}
		result = append(result, string(raw))
	}
	return result, ctx.Err()
}
