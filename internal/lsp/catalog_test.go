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
	c.roots[root.header.Root] = root
	uri := source.URI("file:///test/template.yaml")
	seen := root.membership.Observe()
	root.scanPass = root.membership.Start()
	c.files[uri] = &catalogFile{root: "/test", seen: seen, target: workspace.Target{ID: "scope"}}
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
	c.roots["/test"] = root
	c.files["file:///test/a.yaml"] = &catalogFile{root: "/test", target: workspace.Target{ID: "A"}, problem: "projection failed"}
	c.files["file:///test/b.yaml"] = &catalogFile{root: "/test", target: workspace.Target{ID: "B"}}
	c.refreshReadiness("file:///test/a.yaml")
	c.refreshReadiness("file:///test/b.yaml")
	if c.scopeReadyLocked(root, "A") || !c.scopeReadyLocked(root, "B") {
		t.Fatal("known failure in A contaminated independently complete B")
	}
	c.files["file:///test/unknown.yaml"] = &catalogFile{root: "/test", problem: "target not determined"}
	c.refreshReadiness("file:///test/unknown.yaml")
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

func TestAcceptedDocumentsShareAuthorityAcrossRootsAndRefusal(t *testing.T) {
	c, err := newCatalog()
	if err != nil {
		t.Fatal(err)
	}
	c.cancel() // Exercise accepted input authority independently of discovery.
	root := t.TempDir()
	address := func(name string) string {
		uri, err := fileuri.FromPath(name)
		if err != nil {
			t.Fatal(err)
		}
		return uri
	}
	inside, outside := address(filepath.Join(root, "inside.yaml")), address(filepath.Join(t.TempDir(), "outside.yaml"))
	config := address(filepath.Join(root, ".argo-workflows-ls.yaml"))
	s := session{catalog: c, roots: []string{root}, initialized: true, buffers: map[string]buffer{}, pending: map[string]bool{}, log: io.Discard, out: io.Discard}
	accept := func(method string, params any) {
		raw, err := json.Marshal(params)
		if err != nil {
			t.Fatal(err)
		}
		if _, _, err := s.accept(message{Method: method, Params: raw}); err != nil {
			t.Fatal(err)
		}
	}
	open := func(uri, text string) {
		accept("textDocument/didOpen", map[string]any{"textDocument": map[string]any{"uri": uri, "version": 1, "text": text}})
	}
	check := func(uri, text string) source.Snapshot {
		t.Helper()
		snapshot, ok := c.documents.Snapshot(source.URI(uri))
		if !ok || snapshot.Text() != text || snapshot.Revision() != s.buffers[uri].snapshot.Revision() {
			t.Fatalf("accepted input must borrow one authoritative snapshot: %s, open=%v, text=%q", uri, ok, snapshot.Text())
		}
		return snapshot
	}
	configuration := "schemaVersion: 1\ntargetVersion: '3.6.0'\n"
	open(outside, "outside")
	open(config, configuration)
	open(inside, "ab😀\r\ncd\n")
	first := check(inside, "ab😀\r\ncd\n")
	change := func(version int, changes []map[string]any) {
		accept("textDocument/didChange", map[string]any{"textDocument": map[string]any{"uri": inside, "version": version}, "contentChanges": changes})
	}
	span := func(line, start, end int) map[string]any {
		return map[string]any{"start": map[string]int{"line": line, "character": start}, "end": map[string]int{"line": line, "character": end}}
	}
	change(2, []map[string]any{{"range": span(0, 2, 4), "text": "Z"}, {"range": span(1, 1, 2), "text": "!"}})
	changed := check(inside, "abZ\r\nc!\n")
	if changed.Revision() <= first.Revision() || c.documents.OpenBytes() != len("outside")+len(configuration)+len(changed.Text()) {
		t.Fatal("sequential UTF-16 edits did not update the shared revision and global allowance")
	}
	change(2, []map[string]any{{"text": "stale"}})
	change(3, []map[string]any{{"range": span(0, 0, 1), "text": "temporary"}, {"range": span(99, 0, 1), "text": "invalid"}})
	if check(inside, changed.Text()).Revision() != changed.Revision() {
		t.Fatal("stale or partially invalid notification changed input authority")
	}
	file := c.files[source.URI(inside)]
	proof := catalogProof{root: root, epoch: c.roots[root].epoch, files: map[source.URI]catalogInput{source.URI(inside): {file.stamp, file.hash}}}
	change(4, []map[string]any{{"text": strings.Repeat("x", 5*1024*1024+1)}})
	if s.buffers[inside].unavailable == "" || c.valid([]catalogProof{proof}) {
		t.Fatal("refused input did not revoke a captured file proof")
	}
	check(inside, changed.Text())
	change(5, []map[string]any{{"text": changed.Text()}})
	if s.buffers[inside].unavailable != "" {
		t.Fatal("full replacement did not recover the refused input")
	}
	accept("textDocument/didChange", map[string]any{"textDocument": map[string]any{"uri": config, "version": 2}, "contentChanges": []map[string]any{{"text": strings.Repeat("x", 1024*1024+1)}}})
	if s.buffers[config].unavailable == "" {
		t.Fatal("configuration's 1 MiB limit was lost")
	}
	check(config, configuration)
	accept("textDocument/didChange", map[string]any{"textDocument": map[string]any{"uri": config, "version": 3}, "contentChanges": []any{}})
	if s.buffers[config].unavailable == "" {
		t.Fatal("empty notification recovered an unavailable buffer without replacement")
	}
	beforeRemoval := c.documents.OpenBytes()
	accept("workspace/didChangeWorkspaceFolders", map[string]any{"event": map[string]any{"removed": []map[string]string{{"uri": address(root)}}}})
	check(inside, changed.Text())
	check(outside, "outside")
	if len(c.files) != 0 || c.documents.OpenBytes() != beforeRemoval {
		t.Fatal("removing an index root closed authoritative editor buffers")
	}
	accept("textDocument/didClose", map[string]any{"textDocument": map[string]string{"uri": inside}})
	open(inside, "reopened")
	if check(inside, "reopened").Revision() <= changed.Revision() {
		t.Fatal("close/reopen reused a source revision despite reset editor version")
	}
}

func TestCatalogObsoletePassCannotSweepCurrentRoot(t *testing.T) {
	c, err := newCatalog()
	if err != nil {
		t.Fatal(err)
	}
	c.cancel()
	c.mu.Lock()
	defer c.mu.Unlock()
	old := &catalogRoot{header: workspace.Workspace{Root: "/test"}, done: true, epoch: 1, scanEpoch: 1}
	old.scanPass = old.membership.Start()
	current := &catalogRoot{header: old.header, epoch: 2, scanEpoch: 2}
	current.scanPass = current.membership.Start()
	uri := source.URI("file:///test/current.yaml")
	file := &catalogFile{root: "/test", target: workspace.Target{ID: "/test#root"}, seen: current.membership.Observe()}
	c.roots["/test"], c.files[uri] = current, file
	c.finishPass(old)
	if c.files[uri] != file || old.reported {
		t.Fatal("superseded root completion deleted current-root input or published readiness")
	}
	// A configuration event invalidates a scan before the replacement scan starts.
	// Its older membership token is still current inside Membership at this point.
	current.done = true
	current.epoch++
	file.seen = shared.Observation{}
	c.finishPass(current)
	if c.files[uri] != file || current.reported {
		t.Fatal("superseded configuration scan established deletion or readiness")
	}
}

func TestCatalogObservesSavedBufferAfterScannedDirectory(t *testing.T) {
	c, err := newCatalog()
	if err != nil {
		t.Fatal(err)
	}
	c.cancel()
	c.mu.Lock()
	defer c.mu.Unlock()
	root := t.TempDir()
	header, err := workspace.Describe(context.Background(), root, "", nil)
	if err != nil {
		t.Fatal(err)
	}
	r := &catalogRoot{header: header, identity: catalogIdentity(header, ""), epoch: 1, scanEpoch: 1, done: true, priority: map[string]bool{}}
	r.scanPass = r.membership.Start()
	c.roots[root] = r
	uri, _ := fileuri.FromPath(filepath.Join(root, "created.yaml"))
	snapshot, err := c.documents.Open(source.URI(uri), "created: true\n")
	if err != nil {
		t.Fatal(err)
	}
	s := session{catalog: c, roots: []string{root}, buffers: map[string]buffer{uri: {snapshot: snapshot, version: 1}}}
	c.syncInputs(&s, message{Method: "textDocument/didOpen"})
	file := c.files[source.URI(uri)]
	if file == nil || !r.membership.Seen(file.seen) {
		t.Fatal("new buffer was not observed in the in-flight discovery pass")
	}
	// A new scan can pass this directory while the file is still an overlay.
	// Save then close must preserve membership until the fresh disk read resolves.
	r.scanPass = r.membership.Start()
	if err := os.WriteFile(filepath.Join(root, "created.yaml"), []byte(snapshot.Text()), 0600); err != nil {
		t.Fatal(err)
	}
	c.documents.Close(source.URI(uri))
	delete(s.buffers, uri)
	c.syncInputs(&s, message{Method: "textDocument/didClose"})
	c.finishPass(r)
	if c.files[source.URI(uri)] != file || !c.queue.Pending(source.URI(uri)) || r.complete {
		t.Fatal("scan completion deleted a closed authoritative input before its disk read")
	}
	// Pending work now fences finishPass. Drain that reservation as the closed
	// input worker would, then prove the saved input survives the actual sweep.
	job, ok := c.queue.Pop()
	if !ok || job.URI != source.URI(uri) {
		t.Fatal("closed authoritative input was not queued for reconciliation")
	}
	c.queue.Complete(job)
	c.refreshReadiness(job.URI)
	c.finishPass(r)
	if c.files[source.URI(uri)] != file {
		t.Fatal("completed close reconciliation deleted a file observed by the saved buffer")
	}
}

func TestDocumentCapacityRefusalRecoversAfterClose(t *testing.T) {
	c, err := newCatalog()
	if err != nil {
		t.Fatal(err)
	}
	c.cancel()
	c.documents, err = shared.NewDocuments(shared.DocumentLimits{Entries: 1, FileBytes: 4, OpenBytes: 8})
	if err != nil {
		t.Fatal(err)
	}
	s := session{catalog: c, initialized: true, buffers: map[string]buffer{}, pending: map[string]bool{}, log: io.Discard, out: io.Discard}
	accept := func(method, params string) {
		if _, _, err := s.accept(message{Method: method, Params: json.RawMessage(params)}); err != nil {
			t.Fatalf("capacity refusal terminated the session: %v", err)
		}
	}
	accept("textDocument/didOpen", `{"textDocument":{"uri":"file:///a.yaml","version":1,"text":"one"}}`)
	accept("textDocument/didOpen", `{"textDocument":{"uri":"file:///b.yaml","version":1,"text":"two"}}`)
	if s.buffers["file:///b.yaml"].unavailable == "" || c.documents.OpenBytes() != 3 {
		t.Fatal("entry capacity did not preserve an explicit, uncharged refusal")
	}
	accept("textDocument/didClose", `{"textDocument":{"uri":"file:///a.yaml"}}`)
	accept("textDocument/didChange", `{"textDocument":{"uri":"file:///b.yaml","version":2},"contentChanges":[{"text":"two"}]}`)
	accepted, opened := c.documents.Snapshot("file:///b.yaml")
	if !opened || accepted.Text() != "two" || s.buffers["file:///b.yaml"].unavailable != "" || c.documents.OpenBytes() != 3 {
		t.Fatal("full replacement did not claim the slot released by close")
	}
	accept("textDocument/didOpen", `{"textDocument":{"uri":"file:///b.yaml","version":3,"text":"oversized"}}`)
	current, opened := c.documents.Snapshot("file:///b.yaml")
	if !opened || current.Revision() != accepted.Revision() || s.buffers["file:///b.yaml"].unavailable == "" || c.documents.OpenBytes() != 3 {
		t.Fatal("refused replacement lost the authoritative snapshot or its budget")
	}
}

func TestCatalogCanceledExhaustedScanDoesNotProveDeletion(t *testing.T) {
	ctx, cancel := context.WithCancel(context.Background())
	cancel()
	documents, _ := shared.NewDocuments(shared.DocumentLimits{Entries: 2, FileBytes: 1024, OpenBytes: 1024})
	queue, _ := shared.NewWorkQueue(1, 2)
	idx, _ := index.New(index.Limits{Files: 2, Symbols: 2, References: 2, Bytes: 1024})
	scanner, err := shared.NewScanner(nil, shared.ScanOptions{MaxDirectories: 2})
	if err != nil {
		t.Fatal(err)
	}
	defer scanner.Close()
	root := &catalogRoot{header: workspace.Workspace{Root: "/test"}, epoch: 1, scanEpoch: 1, scanner: scanner, checked: time.Now()}
	root.scanPass = root.membership.Start()
	uri := source.URI("file:///test/known.yaml")
	file := &catalogFile{root: "/test", target: workspace.Target{ID: "/test#root"}}
	c := &catalog{ctx: ctx, documents: documents, queue: queue, index: idx, roots: map[string]*catalogRoot{"/test": root}, files: map[source.URI]*catalogFile{uri: file}, events: make(chan struct{}, 1), logs: map[string]string{}}
	// An exhausted scanner reports Done even when cancellation makes Complete
	// false. The consumer must carry that distinction through to sweep admission.
	c.discoverRoot(root)
	if !root.done || root.scanProblem == "" || root.complete || c.files[uri] != file {
		t.Fatal("canceled exhausted scan established deletion or readiness")
	}
}
