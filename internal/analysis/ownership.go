package analysis

import (
	"context"
	"fmt"

	"github.com/lazysnail1024/k8s-crd-toolkit/source"
	"github.com/lazysnail1024/zed-argo-workflows/compat"
)

// OwnsSnapshot checks resource identities before editor diagnostics acquire a
// workspace or compile schemas. Nested examples, comments and block scalars do
// not confer ownership. A mixed stream is owned if any document is a Workflows
// resource; aliases and merges use the same bounded projection as analysis.
func OwnsSnapshot(ctx context.Context, snapshot source.Snapshot) (bool, error) {
	view, err := parseView(ctx, snapshot)
	if err != nil {
		return false, err
	}
	var unresolved error
	for _, doc := range view.Documents() {
		if err := ctx.Err(); err != nil {
			return false, err
		}
		root := doc.Root()
		if owned(stringValue(member(root, "apiVersion")), stringValue(member(root, "kind"))) {
			return true, nil
		}
		if !doc.Complete() {
			unresolved = fmt.Errorf("incomplete YAML resource identity")
			continue
		}
		if !graphDocument(root) {
			continue
		}
		value, err := project(ctx, snapshot, doc, compat.DefaultVersion)
		if err != nil {
			unresolved = err
			continue
		}
		api, _ := queryMember(value, "apiVersion").(string)
		kind, _ := queryMember(value, "kind").(string)
		if owned(api, kind) {
			return true, nil
		}
	}
	return false, unresolved
}
