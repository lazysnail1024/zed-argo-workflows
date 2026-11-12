package analysis

import (
	"context"

	"github.com/lazysnail1024/k8s-crd-toolkit/index"
)

const symbolQueryWorkLimit = 100000 + 1000000

type symbolQueryKey struct{}
type symbolQueryBudget struct{ remaining int }

// The budget belongs to the whole request, including repeated repair candidate
// queries and separate scopes. It survives fresh parse/projection invocations.
// As with invocation parsing caches, a request consumes it sequentially.
func queryIndexedSymbols(ctx context.Context, known *index.Index, scope index.Scope, prefix index.Key, query string, limit int) (index.SymbolQueryResult, error) {
	ctx = WithInvocation(ctx)
	budget := ctx.Value(symbolQueryKey{}).(*symbolQueryBudget)
	if err := ctx.Err(); err != nil {
		return index.SymbolQueryResult{}, err
	}
	if budget.remaining <= 0 {
		return index.SymbolQueryResult{}, index.ErrQueryBudget
	}
	rows, err := known.SymbolsWithPrefixContext(ctx, scope, prefix, query, limit, budget.remaining)
	budget.remaining -= rows.Work
	if err != nil {
		return index.SymbolQueryResult{}, err
	}
	return rows, nil
}
