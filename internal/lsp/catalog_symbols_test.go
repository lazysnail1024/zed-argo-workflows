package lsp

import (
	"context"
	"errors"
	"fmt"
	"runtime"
	"testing"
	"time"

	"github.com/lazysnail1024/k8s-crd-toolkit/index"
	"github.com/lazysnail1024/k8s-crd-toolkit/source"
	"github.com/lazysnail1024/zed-argo-workflows/internal/analysis"
)

func symbolCatalog() *catalog {
	return &catalog{roots: map[string]*catalogRoot{"root": {epoch: 7}}, files: map[source.URI]*catalogFile{}}
}

func admitSymbols(c *catalog, uri source.URI, names ...string) {
	file := &catalogFile{root: "root", stamp: 1}
	for ordinal, name := range names {
		file.symbols = append(file.symbols, analysis.DomainSymbol{Name: name, Kind: 13, Container: "template · parameter",
			Location: analysis.Location{URI: string(uri), Range: analysis.Range{Start: analysis.Position{Line: uint32(ordinal)}}}})
	}
	c.addFile(uri, file)
}

func TestSymbolQueryCountsFilesAndNonmatchingSymbols(t *testing.T) {
	c := symbolCatalog()
	admitSymbols(c, "file:///a.yaml", "template", "parameter")
	items, proofs, err := c.workspaceSymbols(context.Background(), "absent", 2)
	if !errors.Is(err, index.ErrQueryBudget) || items != nil || proofs != nil {
		t.Fatalf("exhausted work became a complete empty result: items=%v proofs=%v err=%v", items, proofs, err)
	}
	items, _, err = c.workspaceSymbols(context.Background(), "absent", 3)
	if err != nil || items == nil || len(items) != 0 {
		t.Fatalf("exact file+symbol budget should complete: items=%v err=%v", items, err)
	}
	ctx, cancel := context.WithCancel(context.Background())
	cancel()
	if items, _, err = c.workspaceSymbols(ctx, "", 3); !errors.Is(err, context.Canceled) || items != nil {
		t.Fatalf("cancellation returned candidates: items=%v err=%v", items, err)
	}
	if items, proofs, err = symbolResult(ctx, nil); !errors.Is(err, context.Canceled) || items != nil || proofs != nil {
		t.Fatalf("empty result skipped the final cancellation check: items=%v proofs=%v err=%v", items, proofs, err)
	}
}

func TestSymbolQueryKeepsDisplayKindsOrderAndCap(t *testing.T) {
	c := symbolCatalog()
	admitSymbols(c, "file:///z.yaml", "last-template", "last-parameter")
	names := make([]string, workspaceSymbolLimit)
	for n := range names {
		names[n] = fmt.Sprintf("parameter-%05d", n)
	}
	admitSymbols(c, "file:///a.yaml", names...)
	items, proofs, err := c.workspaceSymbols(context.Background(), "", workspaceSymbolWorkLimit)
	if err != nil || len(items) != workspaceSymbolLimit || !c.valid(proofs) {
		t.Fatalf("bounded display query failed: count=%d err=%v", len(items), err)
	}
	for n, item := range items {
		if item["name"] != names[n] || item["kind"] != 13 || item["containerName"] != "template · parameter" {
			t.Fatalf("resource-only replacement or ordering drift at %d: %+v", n, item)
		}
	}
	items, _, err = c.workspaceSymbols(context.Background(), "last-parameter", workspaceSymbolWorkLimit)
	if err != nil || len(items) != 1 || items[0]["name"] != "last-parameter" {
		t.Fatalf("late match was hidden by output cap: items=%v err=%v", items, err)
	}
}

func TestSymbolQueryCursorDoesNotRetainRemovedIdentity(t *testing.T) {
	c := symbolCatalog()
	for n := 0; n < workspaceSymbolPageSize+1; n++ {
		admitSymbols(c, source.URI(fmt.Sprintf("file:///%03d.yaml", n)), "symbol")
	}
	cursor := symbolCursor{end: len(c.queryFiles), epoch: c.queryEpoch}
	page, work, err := c.symbolPage(context.Background(), &cursor, workspaceSymbolWorkLimit)
	if err != nil || len(page) != workspaceSymbolPageSize || work != workspaceSymbolPageSize {
		t.Fatalf("page was not bounded: files=%d work=%d err=%v", len(page), work, err)
	}
	removed := source.URI("file:///000.yaml")
	c.removeQueryFile(removed, c.files[removed])
	delete(c.files, removed)
	if _, _, err := c.symbolPage(context.Background(), &cursor, workspaceSymbolWorkLimit); !errors.Is(err, index.ErrQueryChanged) {
		t.Fatalf("swap removal silently skipped a cursor member: %v", err)
	}
	for slot, uri := range c.queryFiles {
		if uri == removed || c.files[uri].querySlot != slot {
			t.Fatalf("identity page retained removed URI or wrong slot: %q", uri)
		}
	}
	cursor = symbolCursor{end: len(c.queryFiles), epoch: c.queryEpoch}
	admitSymbols(c, "file:///new.yaml", "new")
	if _, _, err := c.symbolPage(context.Background(), &cursor, workspaceSymbolWorkLimit); err != nil {
		t.Fatalf("append invalidated an existing identity page: %v", err)
	}
}

func TestSymbolQueryCancelsWhileWaitingForCatalogLock(t *testing.T) {
	c := symbolCatalog()
	admitSymbols(c, "file:///a.yaml", "symbol")
	c.mu.Lock()
	defer c.mu.Unlock()
	ctx, cancel := context.WithTimeout(context.Background(), 10*time.Millisecond)
	defer cancel()
	start := time.Now()
	items, _, err := c.workspaceSymbols(ctx, "symbol", workspaceSymbolWorkLimit)
	if !errors.Is(err, context.DeadlineExceeded) || items != nil || time.Since(start) > time.Second {
		t.Fatalf("query waited for a locked catalog after cancellation: %v", err)
	}
}

func TestSymbolQueryRetainsPositionProofForDelivery(t *testing.T) {
	c := symbolCatalog()
	admitSymbols(c, "file:///a.yaml", "symbol")
	file := c.files["file:///a.yaml"]
	matches := symbolMatches{{symbol: file.symbols[0], uri: "file:///a.yaml", root: "root", epoch: 7, input: catalogInput{stamp: 1}}}
	file.stamp++
	items, proofs, err := symbolResult(context.Background(), matches)
	if err != nil || len(items) != 1 || c.valid(proofs) {
		t.Fatalf("stale positions lost the delivery fence: items=%v proofs=%v err=%v", items, proofs, err)
	}
}

type symbolStepContext struct {
	context.Context
	cancel context.CancelFunc
	checks int
}

func (c *symbolStepContext) Err() error {
	c.checks++
	if c.checks == 8 {
		c.cancel()
	}
	return c.Context.Err()
}

func TestSymbolQueryCancelsInsideNonmatchingScan(t *testing.T) {
	c := symbolCatalog()
	names := make([]string, 100)
	for n := range names {
		names[n] = "known"
	}
	admitSymbols(c, "file:///a.yaml", names...)
	base, cancel := context.WithCancel(context.Background())
	defer cancel()
	ctx := &symbolStepContext{Context: base, cancel: cancel}
	items, proofs, err := c.workspaceSymbols(ctx, "absent", workspaceSymbolWorkLimit)
	if !errors.Is(err, context.Canceled) || items != nil || proofs != nil || ctx.checks != 8 {
		t.Fatalf("nonmatching inner scan did not observe cancellation: checks=%d items=%v err=%v", ctx.checks, items, err)
	}
}

func TestSymbolQueryBorrowedPagesRaceWithReplacementAndRemoval(t *testing.T) {
	c := symbolCatalog()
	for n := range 128 {
		admitSymbols(c, source.URI(fmt.Sprintf("file:///%03d.yaml", n)), "template", "parameter")
	}
	ctx, cancel := context.WithTimeout(context.Background(), 5*time.Second)
	defer cancel()
	finished := make(chan struct{})
	go func() {
		defer close(finished)
		for n := range 400 {
			uri := source.URI(fmt.Sprintf("file:///%03d.yaml", n%128))
			c.mu.Lock()
			file := c.files[uri]
			if n%2 == 0 {
				c.removeQueryFile(uri, file)
				delete(c.files, uri)
				admitSymbols(c, uri, "new-template", "new-parameter")
			} else {
				// Commit replaces an immutable slice; the previous borrowed page
				// keeps its values but its returned positions lose their proof.
				file.stamp++
				file.symbols = append([]analysis.DomainSymbol(nil), file.symbols...)
				file.symbols[0].Name = fmt.Sprintf("template-%d", n)
			}
			c.mu.Unlock()
			runtime.Gosched()
		}
	}()
	for range 200 {
		items, _, err := c.workspaceSymbols(ctx, "", workspaceSymbolWorkLimit)
		if err != nil && !errors.Is(err, index.ErrQueryChanged) {
			t.Fatalf("concurrent page query failed unexpectedly: %v", err)
		}
		if err == nil && len(items) != 256 {
			t.Fatalf("query silently skipped or repeated a committed member: %d", len(items))
		}
		runtime.Gosched()
	}
	<-finished
	items, proofs, err := c.workspaceSymbols(ctx, "", workspaceSymbolWorkLimit)
	if err != nil || len(items) != 256 || !c.valid(proofs) {
		t.Fatalf("query failed to recover after replacement/removal: count=%d err=%v", len(items), err)
	}
}
