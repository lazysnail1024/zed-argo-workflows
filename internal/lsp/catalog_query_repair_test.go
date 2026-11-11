package lsp

import (
	"context"
	"os"
	"path/filepath"
	"testing"

	"github.com/lazysnail1024/k8s-crd-toolkit/index"
	"github.com/lazysnail1024/k8s-crd-toolkit/source"
	shared "github.com/lazysnail1024/k8s-crd-toolkit/workspace"
	"github.com/lazysnail1024/zed-argo-workflows/internal/fileuri"
	"github.com/lazysnail1024/zed-argo-workflows/internal/workspace"
)

func TestClosedReadRepairsOnlyItsScopeProof(t *testing.T) {
	c := testCatalogNoWorkers(t)
	root := t.TempDir()
	config := "schemaVersion: 1\ntargetVersion: '3.6.0'\nreferencePolicy: closed\ndefaultNamespace: argo\nscopes:\n  - name: selected\n    include: ['a/**']\n  - name: other\n    include: ['b/**']\n"
	if err := os.WriteFile(filepath.Join(root, ".argo-workflows-ls.yaml"), []byte(config), 0600); err != nil {
		t.Fatal(err)
	}
	header, err := workspace.Describe(context.Background(), root, "", nil)
	if err != nil {
		t.Fatal(err)
	}
	r := &catalogRoot{header: header, identity: catalogIdentity(header, ""), done: true, epoch: 1, scanEpoch: 1}
	c.roots[root] = r
	texts := map[string]string{
		"a/caller.yaml":  "apiVersion: argoproj.io/v1alpha1\nkind: Workflow\nmetadata: {name: caller, namespace: argo}\nspec:\n  workflowTemplateRef: {name: library}\n",
		"a/library.yaml": "apiVersion: argoproj.io/v1alpha1\nkind: WorkflowTemplate\nmetadata: {name: library, namespace: argo}\nspec:\n  templates:\n    - name: run\n      container: {image: alpine}\n",
		"b/other.yaml":   "ordinary: true\n",
	}
	addresses := map[string]string{}
	for path, text := range texts {
		name := filepath.Join(root, filepath.FromSlash(path))
		if err := os.MkdirAll(filepath.Dir(name), 0700); err != nil {
			t.Fatal(err)
		}
		if err := os.WriteFile(name, []byte(text), 0600); err != nil {
			t.Fatal(err)
		}
		uri, err := fileuri.FromPath(name)
		if err != nil {
			t.Fatal(err)
		}
		addresses[path] = uri
		c.files[source.URI(uri)] = &catalogFile{root: root}
		c.mu.Lock()
		c.mark(source.URI(uri), shared.Related, true)
		job, ok := c.queue.Pop()
		c.mu.Unlock()
		if !ok {
			t.Fatal("file was not scheduled")
		}
		c.process(job)
	}
	selected := c.files[source.URI(addresses["a/caller.yaml"])].target.ID
	other := c.files[source.URI(addresses["b/other.yaml"])].target.ID
	if !c.scopeReady(root, selected) || !c.scopeReady(root, other) {
		t.Fatal("fixture did not reach independently complete scopes")
	}
	before, proof, err := c.workspace(context.Background(), root, "", addresses["a/caller.yaml"], false, false, false)
	if err != nil || len(before.Documents) != 2 || !c.valid([]catalogProof{proof}) || len(proof.states) < 2 {
		t.Fatalf("initial dependency closure was not complete: documents=%d, states=%v, err=%v", len(before.Documents), proof.states, err)
	}
	moved := "# moved without a watch notification\n" + texts["a/library.yaml"]
	if err := os.WriteFile(filepath.Join(root, "a/library.yaml"), []byte(moved), 0600); err != nil {
		t.Fatal(err)
	}
	after, proof, err := c.workspace(context.Background(), root, "", addresses["a/caller.yaml"], false, false, false)
	if err != nil || !c.valid([]catalogProof{proof}) {
		t.Fatalf("query rejected its own closed-file repair: %v", err)
	}
	if !after.IncompleteScopes[selected] || after.IncompleteScopes[other] {
		t.Fatalf("repair completeness leaked across scopes: %v", after.IncompleteScopes)
	}
	if _, found := proof.states[index.Scope(selected)]; found {
		t.Fatal("self-repaired scope retained an obsolete full-scope proof")
	}
	if _, found := proof.states[index.Scope(other)]; !found {
		t.Fatal("self repair discarded an unrelated scope proof")
	}
	found := false
	for _, document := range after.Documents {
		if string(document.Snapshot.URI()) == addresses["a/library.yaml"] {
			found = document.Snapshot.Text() == moved
		}
	}
	if !found {
		t.Fatal("accepted repair returned the stale snapshot")
	}
	c.mu.Lock()
	c.diskEvent(source.URI(addresses["a/library.yaml"]), false)
	c.mu.Unlock()
	if c.valid([]catalogProof{proof}) {
		t.Fatal("a later real watch was masked by the query's self-repair")
	}
}
