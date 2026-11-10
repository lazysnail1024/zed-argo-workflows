package lsp

import (
	"testing"

	"github.com/lazysnail1024/k8s-crd-toolkit/index"
	"github.com/lazysnail1024/k8s-crd-toolkit/source"
	"github.com/lazysnail1024/zed-argo-workflows/internal/workspace"
)

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
