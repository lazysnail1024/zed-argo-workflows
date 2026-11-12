package lsp

import (
	"context"
	"crypto/sha256"
	"encoding/json"
	"errors"
	"io"
	"os"
	"path/filepath"
	"strings"
	"testing"

	"github.com/lazysnail1024/k8s-crd-toolkit/source"
	shared "github.com/lazysnail1024/k8s-crd-toolkit/workspace"
	"github.com/lazysnail1024/zed-argo-workflows/internal/analysis"
	"github.com/lazysnail1024/zed-argo-workflows/internal/fileuri"
	"github.com/lazysnail1024/zed-argo-workflows/internal/workspace"
)

func TestClosedReadRetryRevokesCapturedProofWithoutByteChange(t *testing.T) {
	c := testCatalogNoWorkers(t)
	uri, text := source.URI("file:///caller.yaml"), "unchanged bytes"
	file := &catalogFile{root: "root", stamp: 4, hash: sha256.Sum256([]byte(text)), invalidated: true}
	c.roots["root"] = &catalogRoot{epoch: 7}
	c.files[uri] = file
	proof := catalogProof{root: "root", epoch: 7, files: map[source.URI]catalogInput{uri: {file.stamp, file.hash}}}
	if !c.valid([]catalogProof{proof}) {
		t.Fatal("fixture proof must initially be current")
	}
	// A foreground closed read can supersede the catalog worker's disk ticket.
	older, err := c.documents.BeginRead(uri)
	if err != nil {
		t.Fatal(err)
	}
	newer, err := c.documents.BeginRead(uri)
	if err != nil {
		t.Fatal(err)
	}
	if _, err := c.documents.PublishRead(newer, text); err != nil {
		t.Fatal(err)
	}
	_, err = c.documents.PublishRead(older, text)
	if !errors.Is(err, shared.ErrStale) {
		t.Fatalf("older closed read was not superseded: %v", err)
	}
	c.finishFile(uri, file, 4, 7, err)
	if file.stamp != 5 || file.hash != proof.files[uri].hash || !c.queue.Pending(uri) || c.valid([]catalogProof{proof}) {
		t.Fatal("stale-read retry failed to revoke the old proof while retaining unchanged bytes")
	}
}

func TestReadOnlyClosureKeepsSnapshotsWithTheirProofs(t *testing.T) {
	c := testCatalogNoWorkers(t)
	root := t.TempDir()
	if err := os.WriteFile(filepath.Join(root, ".argo-workflows-ls.yaml"), []byte("schemaVersion: 1\ntargetVersion: '3.6.0'\nreferencePolicy: closed\n"), 0600); err != nil {
		t.Fatal(err)
	}
	header, err := workspace.Describe(context.Background(), root, "", nil)
	if err != nil {
		t.Fatal(err)
	}
	c.roots[root] = &catalogRoot{header: header, identity: catalogIdentity(header, ""), done: true,
		epoch: 1, scanEpoch: 1, priority: map[string]bool{}}
	owner := session{catalog: c, roots: []string{root}, initialized: true, buffers: map[string]buffer{},
		pending: map[string]bool{}, actions: map[string]pendingAction{}, out: io.Discard, log: io.Discard}
	accept := func(method string, params any) {
		t.Helper()
		raw, err := json.Marshal(params)
		if err != nil {
			t.Fatal(err)
		}
		if _, _, err := owner.accept(message{Method: method, Params: raw}); err != nil {
			t.Fatal(err)
		}
	}
	callerText := "apiVersion: argoproj.io/v1alpha1\nkind: Workflow\nmetadata: {name: caller, namespace: argo}\nspec:\n  entrypoint: main\n  templates:\n    - name: main\n      steps:\n        - - name: call\n            templateRef: {name: library, template: work}\n"
	libraryText := "apiVersion: argoproj.io/v1alpha1\nkind: WorkflowTemplate\nmetadata: {name: library, namespace: argo}\nspec:\n  templates:\n    - name: work\n      container: {image: alpine}\n"
	addresses := map[string]string{}
	for name, text := range map[string]string{"caller.yaml": callerText, "library.yaml": libraryText} {
		uri, err := fileuri.FromPath(filepath.Join(root, name))
		if err != nil {
			t.Fatal(err)
		}
		addresses[name] = uri
		accept("textDocument/didOpen", map[string]any{"textDocument": map[string]any{"uri": uri, "version": 1, "text": text}})
	}
	drain := func() {
		t.Helper()
		for n := 0; n < 8; n++ {
			job, ok := c.queue.Pop()
			if !ok {
				return
			}
			c.process(job)
		}
		t.Fatal("fixture jobs did not settle")
	}
	drain()
	caller, library := addresses["caller.yaml"], addresses["library.yaml"]
	params, err := json.Marshal(map[string]any{"textDocument": map[string]string{"uri": caller}})
	if err != nil {
		t.Fatal(err)
	}
	request := owner.queueRequest(message{Method: "textDocument/definition", Params: params})
	worker := owner
	worker.buffers = map[string]buffer{}
	for uri, b := range owner.buffers {
		worker.buffers[uri] = b
	}
	worker.method, worker.focus = "textDocument/definition", caller
	worker.ctx = analysis.WithInvocation(context.Background())
	worker.workspaces = map[workspaceKey]workspace.Workspace{}
	moved := "# new dependency location\n" + libraryText
	accept("textDocument/didChange", map[string]any{"textDocument": map[string]any{"uri": library, "version": 2},
		"contentChanges": []map[string]string{{"text": moved}}})
	drain()
	if !owner.requestCurrent(request) || worker.buffers[library].snapshot.Text() != libraryText {
		t.Fatal("fixture must keep an admissible caller and the worker's old dependency snapshot")
	}
	w, err := worker.workspace(root, "")
	if err != nil {
		t.Fatal(err)
	}
	if !c.valid(worker.proofs) {
		t.Fatal("a stable, current dependency closure did not provide a valid proof")
	}
	found := false
	for _, document := range w.Documents {
		if string(document.Snapshot.URI()) == library {
			found = document.Snapshot.Text() == moved && document.Snapshot.Revision() == owner.buffers[library].snapshot.Revision()
		}
	}
	if !found {
		t.Fatal("new catalog proof was paired with the worker's old dependency source")
	}
	offset := strings.Index(callerText, "template: work") + len("template: ")
	position, err := owner.buffers[caller].snapshot.Position(offset)
	if err != nil {
		t.Fatal(err)
	}
	locations := analysis.ExternalDefinitions(worker.ctx, w, caller, source.Position{Line: position.Line, UTF16Column: position.UTF16Column})
	if len(locations) != 1 || locations[0].URI != library || locations[0].Range.Start.Line != 6 {
		t.Fatalf("navigation accepted an obsolete dependency location: %+v", locations)
	}
}
