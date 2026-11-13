package analysis

import (
	"context"
	"fmt"
	"strings"
	"testing"

	"github.com/lazysnail1024/k8s-crd-toolkit/source"
)

func TestSharedProjectionAdmitsEverySnapshotIndependently(t *testing.T) {
	text := strings.Replace(storedWorkflow, "spec:\n", "defaults: &container {image: alpine}\nspec:\n", 1)
	text = strings.Replace(text, "container: {image: alpine}", "container: *container", 1)
	first := storedInput(t, "file:///first.yaml", text, "3.6.0", 1)
	second := storedInput(t, "file:///second.yaml", text, "3.6.0", 1)
	ctx := WithInvocation(context.Background())
	view, err := parseView(ctx, first.Snapshot)
	if err != nil {
		t.Fatal(err)
	}
	doc := view.Documents()[0]
	if _, err := project(ctx, first.Snapshot, doc, first.Target.Version); err != nil {
		t.Fatal(err)
	}
	budget := ctx.Value(expansionKey{}).(*expansionBudget)
	cost := budget.nodes
	if cost == 0 {
		t.Fatal("fixture did not exercise logical alias expansion")
	}
	// Re-reading the same logical input does not charge it twice. The same
	// immutable projection in another URI still consumes its own allowance.
	budget.nodes = 100000 - cost + 1
	if _, err := project(ctx, first.Snapshot, doc, first.Target.Version); err != nil {
		t.Fatal("same logical identity was charged twice:", err)
	}
	secondView, err := parseView(ctx, second.Snapshot)
	if err != nil {
		t.Fatal(err)
	}
	if _, err := project(ctx, second.Snapshot, secondView.Documents()[0], second.Target.Version); err == nil || ExpansionError(ctx) == nil {
		t.Fatal("shared content bypassed the second URI's logical admission")
	}
	// A subsequent result has its own allowance and source locations, even
	// when it reuses the exact immutable bytes and projection from above.
	fresh := WithLogicalResult(ctx)
	assertStoredDefinition(t, fresh, second)
	if fresh.Value(expansionKey{}).(*expansionBudget).nodes != cost {
		t.Fatal("fresh result inherited a prior admission or budget failure")
	}
}

func BenchmarkWorkflowElements(b *testing.B) {
	for _, count := range []int{100, 1000} {
		b.Run(fmt.Sprint(count), func(b *testing.B) {
			text := "items: [" + strings.Repeat("item,", count-1) + "item]\n"
			snapshot, err := source.New("file:///sequence.yaml", 1, text)
			if err != nil {
				b.Fatal(err)
			}
			ctx := WithInvocation(context.Background())
			view, err := parseView(ctx, snapshot)
			if err != nil {
				b.Fatal(err)
			}
			doc := view.Documents()[0]
			value, err := project(ctx, snapshot, doc, "3.6.0")
			if err != nil {
				b.Fatal(err)
			}
			items := workflowMember(workflowRoot(value, doc.Root()), "items")
			b.ReportAllocs()
			b.ResetTimer()
			for b.Loop() {
				if len(items.Elements()) != count {
					b.Fatal("semantic sequence lost elements")
				}
			}
		})
	}
}
