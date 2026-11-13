package lsp

import (
	"context"
	"crypto/sha256"
	"encoding/json"
	"errors"
	"io"
	"os"
	"path/filepath"
	"sync"
	"testing"
	"time"

	"github.com/lazysnail1024/k8s-crd-toolkit/index"
	"github.com/lazysnail1024/k8s-crd-toolkit/source"
	shared "github.com/lazysnail1024/k8s-crd-toolkit/workspace"
	"github.com/lazysnail1024/zed-argo-workflows/internal/analysis"
	"github.com/lazysnail1024/zed-argo-workflows/internal/fileuri"
	"github.com/lazysnail1024/zed-argo-workflows/internal/workspace"
)

// These tests stop real Documents/WorkQueue/index operations at their publication
// boundaries. No timers or scheduler luck are needed to let an older worker
// finish after a foreground read of an unopened file.
func closedQueryFixture(t *testing.T) (*catalog, string, source.URI, string) {
	t.Helper()
	c := testCatalogNoWorkers(t)
	root := t.TempDir()
	if err := os.WriteFile(filepath.Join(root, ".argo-workflows-ls.yaml"), []byte("schemaVersion: 1\ntargetVersion: '3.6.0'\nreferencePolicy: closed\ndefaultNamespace: argo\n"), 0600); err != nil {
		t.Fatal(err)
	}
	header, err := workspace.Describe(context.Background(), root, "", nil)
	if err != nil {
		t.Fatal(err)
	}
	c.roots[root] = &catalogRoot{header: header, identity: catalogIdentity(header, ""), done: true, epoch: 1, scanEpoch: 1, priority: map[string]bool{}}
	path := filepath.Join(root, "library.yaml")
	address, err := fileuri.FromPath(path)
	if err != nil {
		t.Fatal(err)
	}
	uri := source.URI(address)
	text := "apiVersion: argoproj.io/v1alpha1\nkind: WorkflowTemplate\nmetadata: {name: library, namespace: argo}\nspec:\n  templates:\n    - name: work\n      container: {image: alpine}\n"
	writeClosedQueryFile(t, path, text)
	c.mu.Lock()
	c.diskEvent(uri, false)
	c.mu.Unlock()
	c.process(popClosedQueryJob(t, c))
	if file := c.files[uri]; file.opened || file.invalidated || !c.scopeReady(root, file.target.ID) {
		t.Fatal("fixture did not index a closed file")
	}
	return c, path, uri, text
}

func writeClosedQueryFile(t *testing.T, path, text string) {
	t.Helper()
	if err := os.WriteFile(path, []byte(text), 0600); err != nil {
		t.Fatal(err)
	}
}

func popClosedQueryJob(t *testing.T, c *catalog) shared.WorkTicket {
	t.Helper()
	job, ok := c.queue.Pop()
	if !ok {
		t.Fatal("expected a queued catalog worker")
	}
	return job
}

func queryClosedSymbols(t *testing.T, c *catalog, uri source.URI, text string) (workspace.Document, []catalogProof) {
	t.Helper()
	s := &session{catalog: c, roots: []string{c.files[uri].root}, method: "textDocument/documentSymbol", ctx: context.Background()}
	document, err := s.queryDocument(string(uri))
	if err != nil || document.Snapshot.Text() != text {
		t.Fatalf("closed query did not read the current source: %v", err)
	}
	if !c.valid(s.proofs) {
		t.Fatal("closed query rejected its own freshly read source")
	}
	return document, s.proofs
}

func TestClosedReadRetryPreservesWinningProof(t *testing.T) {
	c, path, uri, original := closedQueryFixture(t)
	moved := "# first missed watch\n" + original
	writeClosedQueryFile(t, path, moved)
	_, first := queryClosedSymbols(t, c, uri, moved)
	job := popClosedQueryJob(t, c)
	file, root := c.files[uri], c.roots[c.files[uri].root]
	stamp := file.stamp
	older, err := c.documents.BeginRead(uri)
	if err != nil {
		t.Fatal(err)
	}
	olderText, err := workspace.ReadText(context.Background(), path)
	if err != nil {
		t.Fatal(err)
	}
	// The foreground read supersedes only the older read ticket, not the source.
	_, winning := queryClosedSymbols(t, c, uri, moved)
	_, err = c.documents.PublishRead(older, olderText)
	if !errors.Is(err, shared.ErrStale) {
		t.Fatalf("fixture did not supersede the older read: %v", err)
	}
	c.finishFile(uri, file, stamp, root.epoch, err)
	c.complete(job)
	if !c.valid(first) || !c.valid(winning) {
		t.Fatal("retrying a superseded worker read revoked a winning unchanged source proof")
	}
	if !c.queue.Pending(uri) {
		t.Fatal("superseded worker did not retain a repair notification")
	}
	c.process(popClosedQueryJob(t, c))
	if !c.valid(first) || !c.valid(winning) || file.hash != sha256.Sum256([]byte(moved)) || file.invalidated {
		t.Fatal("index catch-up revoked the same source or failed to converge")
	}
}

func TestClosedReadSupersedesPendingParse(t *testing.T) {
	c, path, uri, original := closedQueryFixture(t)
	first := "# first missed watch\n" + original
	writeClosedQueryFile(t, path, first)
	older, olderProof := queryClosedSymbols(t, c, uri, first)
	job := popClosedQueryJob(t, c)
	file := c.files[uri]
	stamp := file.stamp
	// This is the actual index ticket and summary a worker holds between Begin
	// and Commit. Keep its reservation running while the next source is read.
	ticket, err := c.index.Begin(uri, index.Scope(older.Target.ID), older.Snapshot.Revision())
	if err != nil {
		t.Fatal(err)
	}
	summary, err := analysis.Summarize(analysis.WithInvocation(context.Background()), c.roots[file.root].header, older)
	if err != nil {
		t.Fatal(err)
	}
	second := "# second missed watch\n" + first
	writeClosedQueryFile(t, path, second)
	_, winning := queryClosedSymbols(t, c, uri, second)
	if _, err := c.index.Commit(ticket, summary.Facts); !errors.Is(err, index.ErrStaleTicket) {
		t.Fatalf("a pending parse committed superseded bytes after a newer closed read: %v", err)
	}
	if file.stamp == stamp || c.valid(olderProof) || !c.queue.Waiting(uri) {
		t.Fatal("new bytes did not revoke the older input and retain a follow-up worker")
	}
	c.complete(job)
	c.process(popClosedQueryJob(t, c))
	if !c.valid(winning) || file.hash != sha256.Sum256([]byte(second)) || file.invalidated {
		t.Fatal("the newer closed input did not survive index catch-up")
	}
}

func TestClosedReadProofRejectsLaterWatchAfterCatchUp(t *testing.T) {
	for _, changed := range []bool{false, true} {
		name := "same-bytes"
		if changed {
			name = "changed-bytes"
		}
		t.Run(name, func(t *testing.T) {
			c, path, uri, text := closedQueryFixture(t)
			_, proof := queryClosedSymbols(t, c, uri, text)
			if changed {
				writeClosedQueryFile(t, path, "# later real change\n"+text)
			}
			c.mu.Lock()
			c.diskEvent(uri, false)
			c.mu.Unlock()
			if c.valid(proof) {
				t.Fatal("a real subsequent watch did not revoke the earlier proof")
			}
			c.process(popClosedQueryJob(t, c))
			if c.valid(proof) {
				t.Fatal("index catch-up resurrected a proof revoked by a real subsequent watch")
			}
		})
	}
}

// ReadText checks Err immediately after a disk ticket is acquired. Blocking that
// check gives the test an exact read boundary without changing production I/O.
type pausedReadContext struct {
	context.Context
	entered, resume chan struct{}
	once            sync.Once
}

func (c *pausedReadContext) Err() error {
	c.once.Do(func() {
		close(c.entered)
		<-c.resume
	})
	return c.Context.Err()
}

type observedReadContext struct {
	context.Context
	waiting, reading chan struct{}
	wait, read       sync.Once
}

func (c *observedReadContext) Done() <-chan struct{} {
	c.wait.Do(func() { close(c.waiting) })
	return c.Context.Done()
}

func (c *observedReadContext) Err() error {
	c.read.Do(func() { close(c.reading) })
	return c.Context.Err()
}

type closedReadResult struct {
	text string
	err  error
}

func startClosedRead(c *catalog, ctx context.Context, uri source.URI) <-chan closedReadResult {
	result := make(chan closedReadResult, 1)
	go func() {
		snapshot, err := c.readSnapshot(ctx, uri)
		result <- closedReadResult{snapshot.Text(), err}
	}()
	return result
}

func awaitClosedRead(t *testing.T, ctx context.Context, result <-chan closedReadResult) closedReadResult {
	t.Helper()
	select {
	case value := <-result:
		return value
	case <-ctx.Done():
		t.Fatal("a disk reader or waiter was stranded")
		return closedReadResult{}
	}
}

func pauseClosedRead(t *testing.T, c *catalog, ctx context.Context, uri source.URI) (<-chan closedReadResult, func()) {
	t.Helper()
	paused := &pausedReadContext{Context: ctx, entered: make(chan struct{}), resume: make(chan struct{})}
	var release sync.Once
	resume := func() { release.Do(func() { close(paused.resume) }) }
	t.Cleanup(resume)
	result := startClosedRead(c, paused, uri)
	select {
	case <-paused.entered:
	case <-ctx.Done():
		t.Fatal("reader did not acquire its disk ticket")
	}
	return result, resume
}

func TestClosedReadAcquisitionDoesNotSupersedeActiveRead(t *testing.T) {
	c, path, uri, original := closedQueryFixture(t)
	text := "# completed write before either request\n" + original
	writeClosedQueryFile(t, path, text)
	ctx, cancel := context.WithTimeout(context.Background(), 5*time.Second)
	defer cancel()
	foreground, resume := pauseClosedRead(t, c, ctx, uri)
	backgroundContext := &observedReadContext{Context: ctx, waiting: make(chan struct{}), reading: make(chan struct{})}
	background := startClosedRead(c, backgroundContext, uri)
	var backgroundResult closedReadResult
	select {
	case <-backgroundContext.waiting:
		resume()
		backgroundResult = awaitClosedRead(t, ctx, background)
	case <-backgroundContext.reading:
		// On the old implementation, let the background publish first. This
		// deterministically supersedes the paused foreground's disk ticket.
		backgroundResult = awaitClosedRead(t, ctx, background)
		resume()
	case <-ctx.Done():
		t.Fatal("second read neither waited nor reached I/O")
	}
	foregroundResult := awaitClosedRead(t, ctx, foreground)
	if foregroundResult.err != nil || backgroundResult.err != nil || foregroundResult.text != text || backgroundResult.text != text {
		t.Fatalf("an independent read revoked a foreground query of stable bytes: foreground=%v, background=%v", foregroundResult.err, backgroundResult.err)
	}
}

func TestClosedReadWaitIsCancelableAndPerURI(t *testing.T) {
	c, path, uri, text := closedQueryFixture(t)
	ctx, cancel := context.WithTimeout(context.Background(), 5*time.Second)
	defer cancel()
	active, resume := pauseClosedRead(t, c, ctx, uri)
	waitContext, cancelWait := context.WithCancel(ctx)
	waiterContext := &observedReadContext{Context: waitContext, waiting: make(chan struct{}), reading: make(chan struct{})}
	waiter := startClosedRead(c, waiterContext, uri)
	select {
	case <-waiterContext.waiting:
	case <-waiterContext.reading:
		t.Fatal("a same-URI reader bypassed the active read reservation")
	case <-ctx.Done():
		t.Fatal("waiter did not reach its cancellation boundary")
	}
	cancelWait()
	if result := awaitClosedRead(t, ctx, waiter); !errors.Is(result.err, context.Canceled) {
		t.Fatalf("queued read ignored cancellation: %v", result.err)
	}
	other := filepath.Join(filepath.Dir(path), "other.yaml")
	writeClosedQueryFile(t, other, "ordinary: true\n")
	address, err := fileuri.FromPath(other)
	if err != nil {
		t.Fatal(err)
	}
	if result := awaitClosedRead(t, ctx, startClosedRead(c, ctx, source.URI(address))); result.err != nil || result.text != "ordinary: true\n" {
		t.Fatalf("one URI's disk read blocked an independent URI: %v", result.err)
	}
	shutdownContext := &observedReadContext{Context: c.ctx, waiting: make(chan struct{}), reading: make(chan struct{})}
	shutdownWaiter := startClosedRead(c, shutdownContext, uri)
	select {
	case <-shutdownContext.waiting:
	case <-ctx.Done():
		t.Fatal("shutdown waiter did not start")
	}
	c.cancel()
	if result := awaitClosedRead(t, ctx, shutdownWaiter); !errors.Is(result.err, context.Canceled) {
		t.Fatalf("catalog shutdown stranded a read waiter: %v", result.err)
	}
	resume()
	if result := awaitClosedRead(t, ctx, active); result.err != nil || result.text != text {
		t.Fatalf("canceling waiters revoked the active read: %v", result.err)
	}
	if result := awaitClosedRead(t, ctx, startClosedRead(c, ctx, uri)); result.err != nil {
		t.Fatalf("completed read retained its reservation: %v", result.err)
	}
}

func TestClosedReadRemovalRetiresOnlyItsReservation(t *testing.T) {
	c, _, uri, text := closedQueryFixture(t)
	ctx, cancel := context.WithTimeout(context.Background(), 5*time.Second)
	defer cancel()
	older, resumeOlder := pauseClosedRead(t, c, ctx, uri)
	c.mu.Lock()
	c.remove(uri)
	c.diskEvent(uri, false)
	c.mu.Unlock()
	newer, resumeNewer := pauseClosedRead(t, c, ctx, uri)
	resumeOlder()
	if result := awaitClosedRead(t, ctx, older); !errors.Is(result.err, shared.ErrStale) {
		t.Fatalf("removal failed to revoke the old disk ticket: %v", result.err)
	}
	queuedContext := &observedReadContext{Context: ctx, waiting: make(chan struct{}), reading: make(chan struct{})}
	queued := startClosedRead(c, queuedContext, uri)
	select {
	case <-queuedContext.waiting:
	case <-queuedContext.reading:
		t.Fatal("retired reader released a recreated URI's newer reservation")
	case <-ctx.Done():
		t.Fatal("recreated URI's waiter was stranded")
	}
	resumeNewer()
	for _, read := range []<-chan closedReadResult{newer, queued} {
		if result := awaitClosedRead(t, ctx, read); result.err != nil || result.text != text {
			t.Fatalf("recreated URI failed to release its current readers: %v", result.err)
		}
	}
}

func TestClosedReadReservationDoesNotHideAuthorityChanges(t *testing.T) {
	for _, notification := range []string{"watch", "open"} {
		t.Run(notification, func(t *testing.T) {
			c, _, uri, text := closedQueryFixture(t)
			ctx, cancel := context.WithTimeout(context.Background(), 5*time.Second)
			defer cancel()
			active, resume := pauseClosedRead(t, c, ctx, uri)
			if notification == "watch" {
				c.mu.Lock()
				c.diskEvent(uri, false)
				c.mu.Unlock()
			} else {
				text = "# authoritative unsaved buffer\n" + text
				owner := session{catalog: c, roots: []string{c.files[uri].root}, initialized: true,
					buffers: map[string]buffer{}, pending: map[string]bool{}, actions: map[string]pendingAction{}, out: io.Discard, log: io.Discard}
				params, err := json.Marshal(map[string]any{"textDocument": map[string]any{"uri": uri, "version": 1, "text": text}})
				if err != nil {
					t.Fatal(err)
				}
				if _, _, err := owner.accept(message{Method: "textDocument/didOpen", Params: params}); err != nil {
					t.Fatal(err)
				}
				// An authoritative buffer does not wait behind an obsolete disk
				// read and keeps the editor's exact snapshot identity.
				snapshot, err := c.readSnapshot(ctx, uri)
				if err != nil || snapshot.Revision() != owner.buffers[string(uri)].snapshot.Revision() || snapshot.Text() != text {
					t.Fatalf("disk reservation blocked or replaced the authoritative buffer: %v", err)
				}
			}
			resume()
			if result := awaitClosedRead(t, ctx, active); !errors.Is(result.err, shared.ErrStale) {
				t.Fatalf("a real %s failed to revoke an active disk ticket: %v", notification, result.err)
			}
			if result := awaitClosedRead(t, ctx, startClosedRead(c, ctx, uri)); result.err != nil || result.text != text {
				t.Fatalf("reader failed to recover after %s: %v", notification, result.err)
			}
		})
	}
}
