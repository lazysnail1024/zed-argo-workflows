package lsp

import (
	"context"
	"crypto/sha256"
	"errors"
	"os"
	"path/filepath"
	"strings"
	"testing"
	"time"

	"github.com/lazysnail1024/k8s-crd-toolkit/index"
	"github.com/lazysnail1024/k8s-crd-toolkit/source"
	shared "github.com/lazysnail1024/k8s-crd-toolkit/workspace"
	"github.com/lazysnail1024/zed-argo-workflows/internal/fileuri"
	"github.com/lazysnail1024/zed-argo-workflows/internal/workspace"
)

func TestCatalogClosedDocumentWaitsOnlyForMetadata(t *testing.T) {
	root := t.TempDir()
	text := "apiVersion: argoproj.io/v1alpha1\nkind: WorkflowTemplate\nmetadata: {name: sample}\nspec: {templates: []}\n"
	if err := os.WriteFile(filepath.Join(root, ".argo-workflows-ls.yaml"), []byte("schemaVersion: 1\ntargetVersion: \"3.6.0\"\n"), 0600); err != nil {
		t.Fatal(err)
	}
	path := filepath.Join(root, "template.yaml")
	if err := os.WriteFile(path, []byte(text), 0600); err != nil {
		t.Fatal(err)
	}
	uri, err := fileuri.FromPath(path)
	if err != nil {
		t.Fatal(err)
	}
	documents, err := shared.NewDocuments(shared.DocumentLimits{Entries: 4, FileBytes: 1024, OpenBytes: 1024})
	if err != nil {
		t.Fatal(err)
	}
	r := &catalogRoot{header: workspace.Workspace{Root: root}, epoch: 1, problem: "configuration pending"}
	c := &catalog{documents: documents, roots: map[string]*catalogRoot{root: r}, files: map[source.URI]*catalogFile{
		source.URI(uri): {root: root, stamp: 1, hash: sha256.Sum256([]byte(text))},
	}}
	ctx, cancel := context.WithTimeout(context.Background(), 20*time.Millisecond)
	defer cancel()
	if _, _, err := c.document(ctx, uri); !errors.Is(err, context.DeadlineExceeded) {
		t.Fatalf("pending metadata must respect request cancellation, got %v", err)
	}
	header, err := workspace.Describe(context.Background(), root, "", nil)
	if err != nil {
		t.Fatal(err)
	}
	r.header, r.problem = header, ""
	document, proof, err := c.document(context.Background(), uri)
	if err != nil || document.Target.Version != "3.6.0" || document.Snapshot.Text() != text || r.done {
		t.Fatalf("local document requires only ready metadata, document=%+v, err=%v", document.Target, err)
	}
	if !c.valid([]catalogProof{proof}) {
		t.Fatal("unchanged local input proof was rejected")
	}
	r.epoch++
	if c.valid([]catalogProof{proof}) {
		t.Fatal("local query survived a configuration change")
	}
	r.epoch--
	c.files[source.URI(uri)].stamp++
	if c.valid([]catalogProof{proof}) {
		t.Fatal("local query survived an input change")
	}
	// Open snapshots borrow the allowance reserved by Serve. A closed read
	// must use the same allowance even when other open URIs are outside its root.
	if _, err := c.documents.Open(source.URI(uri), text); err != nil {
		t.Fatal(err)
	}
	full := workspace.WithSnapshotBudget(context.Background(), workspace.MaxSnapshotBytes)
	if _, err := c.readSnapshot(full, source.URI(uri)); err != nil {
		t.Fatalf("reserved open snapshot was charged twice: %v", err)
	}
	c.documents.Close(source.URI(uri))
	if _, err := c.readSnapshot(full, source.URI(uri)); err == nil || !strings.Contains(err.Error(), "128 MiB") {
		t.Fatalf("closed input bypassed the open-buffer reservation: %v", err)
	}
	exact := workspace.WithSnapshotBudget(context.Background(), workspace.MaxSnapshotBytes-len(text))
	if _, err := c.readSnapshot(exact, source.URI(uri)); err != nil {
		t.Fatalf("exact remaining allowance refused: %v", err)
	}
	if _, err := c.readSnapshot(exact, source.URI(uri)); err == nil || !strings.Contains(err.Error(), "128 MiB") {
		t.Fatalf("sequential closed reads did not share a request allowance: %v", err)
	}
}

func TestCatalogPropagatesTransitiveAndMissingDependencies(t *testing.T) {
	c, err := newCatalog()
	if err != nil {
		t.Fatal(err)
	}
	defer c.cancel()
	c.mu.Lock()
	defer c.mu.Unlock()
	commit := func(uri source.URI, key, dependency index.Key) index.Change {
		c.files[uri] = &catalogFile{opened: uri == "file:///a.yaml"}
		facts := index.Facts{Symbols: []index.Symbol{{ID: "resource", Key: key, Name: string(key), Span: source.Span{End: 1}, Selection: source.Span{End: 1}}}}
		if dependency != "" {
			facts.Dependencies = []index.Key{dependency}
		}
		ticket, err := c.index.Begin(uri, "scope", 1)
		if err != nil {
			t.Fatal(err)
		}
		change, err := c.index.Commit(ticket, facts)
		if err != nil {
			t.Fatal(err)
		}
		return change
	}
	commit("file:///a.yaml", "A", "B")
	commit("file:///b.yaml", "B", "C")
	change := commit("file:///c.yaml", "C", "")
	c.affected(change.Affected)
	if !c.publications["file:///a.yaml"] {
		t.Fatal("a missing C becoming available did not reach A through unchanged B")
	}
	c.publications = map[string]bool{}
	c.remove("file:///c.yaml")
	if !c.publications["file:///a.yaml"] {
		t.Fatal("C removal did not reach A through unchanged B")
	}
}

func TestCatalogIncompleteScanDoesNotProveDeletion(t *testing.T) {
	c, err := newCatalog()
	if err != nil {
		t.Fatal(err)
	}
	defer c.cancel()
	c.mu.Lock()
	defer c.mu.Unlock()
	root := &catalogRoot{header: workspace.Workspace{Root: "/test"}, done: true, pass: 2, scanProblem: "directory permission denied"}
	uri := source.URI("file:///test/template.yaml")
	c.files[uri] = &catalogFile{root: "/test", seen: 1, target: workspace.Target{ID: "scope"}}
	c.finishPass(root)
	if c.files[uri] == nil || root.complete {
		t.Fatal("partial enumeration deleted a known member or established completeness")
	}
}

func TestCatalogCompletenessIsPerScopeAfterDiscovery(t *testing.T) {
	c, err := newCatalog()
	if err != nil {
		t.Fatal(err)
	}
	defer c.cancel()
	c.mu.Lock()
	defer c.mu.Unlock()
	root := &catalogRoot{header: workspace.Workspace{Root: "/test"}, done: true}
	c.files["file:///test/a.yaml"] = &catalogFile{root: "/test", target: workspace.Target{ID: "A"}, problem: "projection failed"}
	c.files["file:///test/b.yaml"] = &catalogFile{root: "/test", target: workspace.Target{ID: "B"}}
	if c.scopeReadyLocked(root, "A") || !c.scopeReadyLocked(root, "B") {
		t.Fatal("known failure in A contaminated independently complete B")
	}
	c.files["file:///test/unknown.yaml"] = &catalogFile{root: "/test", problem: "target not determined"}
	if c.scopeReadyLocked(root, "B") {
		t.Fatal("unknown scope was treated as proof of complete B")
	}
}

func TestCatalogRejectsOutdatedProof(t *testing.T) {
	c, err := newCatalog()
	if err != nil {
		t.Fatal(err)
	}
	defer c.cancel()
	c.mu.Lock()
	c.roots["/test"] = &catalogRoot{header: workspace.Workspace{Root: "/test"}, epoch: 2, problem: "configuration pending"}
	c.mu.Unlock()
	if c.valid([]catalogProof{{root: "/test", epoch: 1}}) {
		t.Fatal("old configuration proof survived refresh")
	}
}
