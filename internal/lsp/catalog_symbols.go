package lsp

import (
	"container/heap"
	"context"
	"sort"
	"strings"
	"time"

	"github.com/lazysnail1024/k8s-crd-toolkit/index"
	"github.com/lazysnail1024/k8s-crd-toolkit/source"
	"github.com/lazysnail1024/zed-argo-workflows/internal/analysis"
)

const workspaceSymbolWorkLimit = 100000 + 1000000 // Existing file and symbol capacities.
const workspaceSymbolLimit = 20000
const workspaceSymbolPageSize = 64

// queryFiles contains identities only, not another symbol table. Append keeps
// an active cursor valid; swap-removal fences it before a moved entry can be
// skipped. Callers hold catalog.mu. DomainSymbol slices are immutable once
// committed, so a query can borrow one page and inspect its symbols unlocked.
func (c *catalog) addFile(uri source.URI, file *catalogFile) {
	file.querySlot = len(c.queryFiles)
	c.queryFiles = append(c.queryFiles, uri)
	c.files[uri] = file
}

func (c *catalog) removeQueryFile(uri source.URI, file *catalogFile) {
	slot := file.querySlot
	if slot < 0 || slot >= len(c.queryFiles) || c.queryFiles[slot] != uri {
		return
	}
	last := len(c.queryFiles) - 1
	moved := c.queryFiles[last]
	c.queryFiles[slot] = moved
	if other := c.files[moved]; other != nil {
		other.querySlot = slot
	}
	c.queryFiles[last] = ""
	c.queryFiles = c.queryFiles[:last]
	c.queryEpoch++
}

type symbolCursor struct {
	next, end int
	epoch     uint64
}

type symbolFile struct {
	uri     source.URI
	root    string
	epoch   uint64
	input   catalogInput
	symbols []analysis.DomainSymbol
}

// A canceled query must not wait behind another catalog operation to acquire
// its next page. This does not change the transport owner's final proof check.
func (c *catalog) lockSymbols(ctx context.Context) error {
	if err := ctx.Err(); err != nil {
		return err
	}
	if c.mu.TryLock() {
		return nil
	}
	timer := time.NewTicker(time.Millisecond)
	defer timer.Stop()
	for {
		select {
		case <-ctx.Done():
			return ctx.Err()
		case <-timer.C:
			if c.mu.TryLock() {
				return nil
			}
		}
	}
}

func (c *catalog) symbolPage(ctx context.Context, cursor *symbolCursor, remaining int) ([]symbolFile, int, error) {
	if err := c.lockSymbols(ctx); err != nil {
		return nil, 0, err
	}
	defer c.mu.Unlock()
	if cursor.epoch != c.queryEpoch {
		return nil, 0, index.ErrQueryChanged
	}
	page := make([]symbolFile, 0, workspaceSymbolPageSize)
	work := 0
	for cursor.next < cursor.end && work < workspaceSymbolPageSize {
		if err := ctx.Err(); err != nil {
			return nil, work, err
		}
		if work >= remaining {
			return nil, work, index.ErrQueryBudget
		}
		uri := c.queryFiles[cursor.next]
		cursor.next++
		work++
		file := c.files[uri]
		if file == nil || file.invalidated || file.blocked || file.problem != "" {
			continue
		}
		root := c.roots[file.root]
		if root != nil {
			page = append(page, symbolFile{uri, file.root, root.epoch, catalogInput{file.stamp, file.hash}, file.symbols})
		}
	}
	return page, work, nil
}

type symbolMatch struct {
	symbol  analysis.DomainSymbol
	uri     source.URI
	ordinal int
	root    string
	epoch   uint64
	input   catalogInput
}

func symbolMatchLess(a, b symbolMatch) bool {
	if a.uri != b.uri {
		return a.uri < b.uri
	}
	return a.ordinal < b.ordinal
}

type symbolMatches []symbolMatch

func (h symbolMatches) Len() int           { return len(h) }
func (h symbolMatches) Less(i, j int) bool { return symbolMatchLess(h[j], h[i]) }
func (h symbolMatches) Swap(i, j int)      { h[i], h[j] = h[j], h[i] }
func (h *symbolMatches) Push(value any)    { *h = append(*h, value.(symbolMatch)) }
func (h *symbolMatches) Pop() any {
	last := len(*h) - 1
	value := (*h)[last]
	(*h)[last] = symbolMatch{}
	*h = (*h)[:last]
	return value
}

func (h *symbolMatches) keep(value symbolMatch) {
	if len(*h) < workspaceSymbolLimit {
		heap.Push(h, value)
		return
	}
	if symbolMatchLess(value, (*h)[0]) {
		(*h)[0] = value
		heap.Fix(h, 0)
	}
}

// workspaceSymbols retains the same URI/file-order first 20,000 display symbols
// (including templates and parameters). A cap on results is not a work budget:
// every file and symbol visited is charged even when nothing matches. Failure
// discards candidates, so cancellation/exhaustion cannot masquerade as absence.
func (c *catalog) workspaceSymbols(ctx context.Context, query string, maxWork int) ([]map[string]any, []catalogProof, error) {
	if err := c.lockSymbols(ctx); err != nil {
		return nil, nil, err
	}
	cursor := symbolCursor{end: len(c.queryFiles), epoch: c.queryEpoch}
	c.mu.Unlock()
	if maxWork <= 0 {
		return nil, nil, index.ErrQueryBudget
	}
	matches := symbolMatches{}
	remaining := maxWork
	for cursor.next < cursor.end {
		page, work, err := c.symbolPage(ctx, &cursor, remaining)
		remaining -= work
		if err != nil {
			return nil, nil, err
		}
		for _, file := range page {
			if err := collectSymbolMatches(ctx, query, file, &remaining, &matches); err != nil {
				return nil, nil, err
			}
		}
	}
	if err := ctx.Err(); err != nil {
		return nil, nil, err
	}
	if err := c.lockSymbols(ctx); err != nil {
		return nil, nil, err
	}
	current := cursor.epoch == c.queryEpoch
	c.mu.Unlock()
	if !current {
		return nil, nil, index.ErrQueryChanged
	}
	return symbolResult(ctx, matches)
}

func collectSymbolMatches(ctx context.Context, query string, file symbolFile, remaining *int, matches *symbolMatches) error {
	for ordinal, symbol := range file.symbols {
		if err := ctx.Err(); err != nil {
			return err
		}
		if *remaining <= 0 {
			return index.ErrQueryBudget
		}
		*remaining--
		if strings.Contains(symbol.Name, query) {
			matches.keep(symbolMatch{symbol, file.uri, ordinal, file.root, file.epoch, file.input})
		}
	}
	return nil
}

// Serialize detached candidates together with their input proof. Serve is the
// only production caller and checks the proof immediately before delivery;
// rechecking here would neither replace that fence nor close its race window.
func symbolResult(ctx context.Context, matches symbolMatches) ([]map[string]any, []catalogProof, error) {
	sort.Slice(matches, func(i, j int) bool { return symbolMatchLess(matches[i], matches[j]) })
	result := make([]map[string]any, 0, len(matches))
	byRoot := map[string]*catalogProof{}
	for _, match := range matches {
		if err := ctx.Err(); err != nil {
			return nil, nil, err
		}
		proof := byRoot[match.root]
		if proof == nil {
			proof = &catalogProof{root: match.root, epoch: match.epoch, files: map[source.URI]catalogInput{}}
			byRoot[match.root] = proof
		}
		if proof.epoch != match.epoch {
			return nil, nil, index.ErrQueryChanged
		}
		proof.files[match.uri] = match.input
		symbol := match.symbol
		result = append(result, map[string]any{"name": symbol.Name, "kind": symbol.Kind, "location": symbol.Location, "containerName": symbol.Container})
	}
	proofs := make([]catalogProof, 0, len(byRoot))
	for _, proof := range byRoot {
		proofs = append(proofs, *proof)
	}
	if err := ctx.Err(); err != nil {
		return nil, nil, err
	}
	return result, proofs, nil
}
