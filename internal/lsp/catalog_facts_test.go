package lsp

import (
	"context"
	"testing"

	"github.com/lazysnail1024/zed-argo-workflows/internal/analysis"
	"github.com/lazysnail1024/zed-argo-workflows/internal/workspace"
)

func assertCatalogFacts(t *testing.T, c *catalog, document workspace.Document, want bool) {
	t.Helper()
	ctx := analysis.WithInvocation(context.Background())
	c.facts.Restore(ctx, workspace.Workspace{Documents: []workspace.Document{document}})
	if got := analysis.CaptureDocumentFacts(ctx, document) != nil; got != want {
		t.Fatalf("retained facts for revision %d: got=%v want=%v", document.Snapshot.Revision(), got, want)
	}
}

func TestCatalogFactsRejectStaleProducerAndReleaseInvalidatedInput(t *testing.T) {
	c, path, uri, original := closedQueryFixture(t)
	oldDocument, oldProofs := queryClosedSymbols(t, c, uri, original)
	ctx := analysis.WithInvocation(context.Background())
	c.facts.Restore(ctx, workspace.Workspace{Documents: []workspace.Document{oldDocument}})
	oldFacts := analysis.CaptureDocumentFacts(ctx, oldDocument)
	if oldFacts == nil {
		t.Fatal("completed catalog worker did not retain facts")
	}
	changed := "# moved before the older query finished\n" + original
	writeClosedQueryFile(t, path, changed)
	_, _ = queryClosedSymbols(t, c, uri, changed)
	assertCatalogFacts(t, c, oldDocument, false)
	c.process(popClosedQueryJob(t, c))
	currentDocument, currentProofs := queryClosedSymbols(t, c, uri, changed)
	if !c.valid(currentProofs) {
		t.Fatal("completed current input lost its proof")
	}
	assertCatalogFacts(t, c, currentDocument, true)
	// The old request may finish after the new catalog summary. It cannot
	// replace current retained data after the owner rejects its input proof.
	c.retainFacts(oldFacts, oldProofs)
	assertCatalogFacts(t, c, currentDocument, true)
	assertCatalogFacts(t, c, oldDocument, false)
	c.mu.Lock()
	c.remove(uri)
	c.mu.Unlock()
	assertCatalogFacts(t, c, currentDocument, false)
}
