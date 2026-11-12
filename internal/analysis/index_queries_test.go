package analysis

import (
	"context"
	"errors"
	"testing"

	"github.com/lazysnail1024/k8s-crd-toolkit/index"
	"github.com/lazysnail1024/k8s-crd-toolkit/source"
)

func TestSymbolQueryBudgetBelongsToWholeRequest(t *testing.T) {
	known, err := index.New(index.Limits{Files: 2, Symbols: 2, References: 2, Bytes: 4096})
	if err != nil {
		t.Fatal(err)
	}
	ticket, err := known.Begin("file:///a.yaml", "scope", 1)
	if err != nil {
		t.Fatal(err)
	}
	key := resourceIndexKey("WorkflowTemplate", "argo", "library")
	facts := index.Facts{Symbols: []index.Symbol{{ID: "resource", Key: key, Name: "library", Span: source.Span{End: 1}, Selection: source.Span{End: 1}}}}
	if _, err := known.Commit(ticket, facts); err != nil {
		t.Fatal(err)
	}
	ctx := WithInvocation(context.Background())
	budget := ctx.Value(symbolQueryKey{}).(*symbolQueryBudget)
	budget.remaining = 4 // Two scans: one file plus one symbol each.
	for range 2 {
		rows, err := queryIndexedSymbols(ctx, known, "scope", "", "absent", 1)
		if err != nil || !rows.Complete || len(rows.Items) != 0 {
			t.Fatalf("nonmatching bounded scan failed: %+v %v", rows, err)
		}
	}
	// Simulated edits may reset parse caches but not grant free index scans.
	ctx = WithInvocation(context.WithValue(ctx, invocationKey{}, (*invocation)(nil)))
	if rows, err := ResourceAlternatives(ctx, known, "scope", key); !errors.Is(err, index.ErrQueryBudget) || rows != nil {
		t.Fatalf("candidate scan reset a consumed request budget: %+v %v", rows, err)
	}
	fresh := WithInvocation(context.Background())
	if rows, err := ResourceAlternatives(fresh, known, "scope", key); err != nil || len(rows) != 1 {
		t.Fatalf("independent request did not recover its budget: %+v %v", rows, err)
	}
}
