package lsp

import (
	"context"
	"encoding/json"
	"fmt"
	"io"
	"os"
	"path/filepath"
	"testing"

	"github.com/lazysnail1024/k8s-crd-toolkit/index"
	"github.com/lazysnail1024/k8s-crd-toolkit/source"
	shared "github.com/lazysnail1024/k8s-crd-toolkit/workspace"
	"github.com/lazysnail1024/zed-argo-workflows/internal/fileuri"
	"github.com/lazysnail1024/zed-argo-workflows/internal/workspace"
)

func testCatalogNoWorkers(t *testing.T) *catalog {
	t.Helper()
	documents, err := shared.NewDocuments(shared.DocumentLimits{Entries: 10010, FileBytes: 5 * 1024 * 1024, OpenBytes: catalogTextLimit})
	if err != nil {
		t.Fatal(err)
	}
	idx, err := index.New(index.Limits{Files: 10010, Symbols: 20020, References: 20020, Bytes: catalogTextLimit})
	if err != nil {
		t.Fatal(err)
	}
	queue, err := shared.NewWorkQueue(512, 10010)
	if err != nil {
		t.Fatal(err)
	}
	ctx, cancel := context.WithCancel(context.Background())
	t.Cleanup(cancel)
	return &catalog{ctx: ctx, cancel: cancel, documents: documents, index: idx, queue: queue,
		roots: map[string]*catalogRoot{}, files: map[source.URI]*catalogFile{},
		events: make(chan struct{}, 1), publications: map[string]bool{}, logs: map[string]string{}}
}

func TestReadinessTracksPendingFailureRetargetAndRemoval(t *testing.T) {
	c := testCatalogNoWorkers(t)
	name := t.TempDir()
	header, err := workspace.Describe(context.Background(), name, "3.6.0", nil)
	if err != nil {
		t.Fatal(err)
	}
	r := &catalogRoot{header: header, done: true, epoch: 1, scanEpoch: 1}
	c.roots[name] = r
	uri, err := fileuri.FromPath(filepath.Join(name, "current.yaml"))
	if err != nil {
		t.Fatal(err)
	}
	if err := os.WriteFile(filepath.Join(name, "current.yaml"), []byte("ordinary: true\n"), 0600); err != nil {
		t.Fatal(err)
	}
	c.mu.Lock()
	c.diskEvent(source.URI(uri), false)
	if r.known != 1 || r.unknown != 1 || r.pending != 1 || c.scopeReadyLocked(r, "unrelated") {
		t.Fatal("an unclassified input established scope completeness")
	}
	job, ok := c.queue.Pop()
	c.mu.Unlock()
	if !ok {
		t.Fatal("disk event did not enqueue work")
	}
	c.process(job)
	c.mu.Lock()
	file := c.files[source.URI(uri)]
	target := file.target.ID
	if target == "" || r.known != 1 || r.unknown != 0 || r.pending != 0 || !c.scopeReadyLocked(r, target) {
		t.Fatalf("successful parse did not publish ready scope: %+v, file=%+v", r, file)
	}
	c.mark(source.URI(uri), shared.Related, true)
	job, ok = c.queue.Pop()
	if !ok || c.scopeReadyLocked(r, target) || !c.scopeReadyLocked(r, "unrelated") {
		t.Fatal("pending input did not affect only its classified scope")
	}
	stamp := file.stamp
	c.mu.Unlock()
	c.finishFile(source.URI(uri), file, stamp, r.epoch, fmt.Errorf("failed projection"))
	c.complete(job)
	c.mu.Lock()
	if c.scopeReadyLocked(r, target) || r.pending != 0 || r.unready[target] != 1 {
		t.Fatal("failed worker was interpreted as complete input")
	}
	file.target = workspace.Target{ID: "new-target"}
	c.mark(source.URI(uri), shared.Related, true)
	if r.unready[target] != 0 || r.unready["new-target"] != 1 || r.known != 1 {
		t.Fatal("retargeting retained the old contribution or double-counted the file")
	}
	c.remove(source.URI(uri))
	if r.known != 0 || r.unknown != 0 || r.pending != 0 || len(r.unready) != 0 {
		t.Fatal("removal retained readiness contributions")
	}
	c.mu.Unlock()
}

func TestReadinessKeepsSupersededWorkerReservation(t *testing.T) {
	c := testCatalogNoWorkers(t)
	c.mu.Lock()
	r := &catalogRoot{header: workspace.Workspace{Root: "/test"}, done: true}
	c.roots["/test"] = r
	uri := source.URI("file:///test/one.yaml")
	c.files[uri] = &catalogFile{root: "/test", target: workspace.Target{ID: "A"}}
	c.mark(uri, shared.Current, false)
	job, ok := c.queue.Pop()
	if !ok {
		t.Fatal("missing first job")
	}
	c.mark(uri, shared.Current, false)
	c.mu.Unlock()
	c.complete(job)
	c.mu.Lock()
	if r.known != 1 || r.pending != 1 || c.scopeReadyLocked(r, "A") {
		t.Fatal("old worker completion erased a newer notification")
	}
	job, ok = c.queue.Pop()
	c.mu.Unlock()
	if !ok {
		t.Fatal("newer notification was lost")
	}
	c.complete(job)
	c.mu.Lock()
	defer c.mu.Unlock()
	if r.pending != 0 || !c.scopeReadyLocked(r, "A") {
		t.Fatal("final worker reservation was not released")
	}
}

func TestFolderReplacementRevokesSamePathProof(t *testing.T) {
	c := testCatalogNoWorkers(t)
	root := t.TempDir()
	header, err := workspace.Describe(context.Background(), root, "", nil)
	if err != nil {
		t.Fatal(err)
	}
	r := &catalogRoot{header: header, identity: catalogIdentity(header, ""), epoch: 9, inputEpoch: 7, priority: map[string]bool{}}
	c.roots[root] = r
	proof := catalogProof{root: root, epoch: r.epoch}
	uri, err := fileuri.FromPath(root)
	if err != nil {
		t.Fatal(err)
	}
	params, err := json.Marshal(map[string]any{"event": map[string]any{
		"added": []map[string]string{{"uri": uri}}, "removed": []map[string]string{{"uri": uri}},
	}})
	if err != nil {
		t.Fatal(err)
	}
	s := session{catalog: c, roots: []string{root}, initialized: true, buffers: map[string]buffer{}, pending: map[string]bool{}, out: io.Discard, log: io.Discard}
	if _, _, err := s.accept(message{Method: "workspace/didChangeWorkspaceFolders", Params: params}); err != nil {
		t.Fatal(err)
	}
	if c.valid([]catalogProof{proof}) || r.epoch <= 9 || r.inputEpoch <= 7 {
		t.Fatal("same-path root replacement retained the removed root's proof")
	}
}
