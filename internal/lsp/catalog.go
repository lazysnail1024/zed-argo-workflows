package lsp

import (
	"context"
	"crypto/sha256"
	"encoding/json"
	"errors"
	"os"
	"path/filepath"
	"sort"
	"strings"
	"sync"
	"time"

	"github.com/lazysnail1024/k8s-crd-toolkit/index"
	"github.com/lazysnail1024/k8s-crd-toolkit/source"
	shared "github.com/lazysnail1024/k8s-crd-toolkit/workspace"
	"github.com/lazysnail1024/zed-argo-workflows/internal/analysis"
	"github.com/lazysnail1024/zed-argo-workflows/internal/fileuri"
	"github.com/lazysnail1024/zed-argo-workflows/internal/workspace"
)

const catalogTextLimit = 128 * 1024 * 1024

type catalogRoot struct {
	header                                                workspace.Workspace
	override, identity, problem, scanProblem, unavailable string
	membership                                            shared.Membership
	scanPass                                              shared.ScanPass
	epoch, inputEpoch, pass, scanEpoch                    uint64
	scanner                                               *shared.Scanner // Used only by discover, including Close/Prioritize.
	priority                                              map[string]bool
	done, complete, reported                              bool
	lastState                                             string
	nextPass                                              time.Time
	checked                                               time.Time
	reportedAt                                            time.Time
	overlay                                               *string
	reads, bytes, parses                                  uint64
	known, unknown, pending                               int
	unready                                               map[string]int
	members                                               map[string]int
}

type catalogFile struct {
	root                                          string
	target                                        workspace.Target
	seen                                          shared.Observation
	stamp                                         uint64
	revision                                      source.Revision
	hash                                          [32]byte
	observedHash                                  [32]byte
	observed                                      bool
	config, problem                               string
	opened, blocked, invalidated, owned, awaiting bool
	symbols                                       []analysis.DomainSymbol
	symbolBytes                                   int
	readiness                                     catalogReadiness
	querySlot                                     int
}

// catalog survives every request/publication worker. Its index maps retain
// identities and summaries, and Documents owns authoritative open buffers.
// The optional fact store is the single bounded owner of detached parse and
// projection data for both catalog and session workers. No Workspace or analysis
// invocation outlives its worker.
type catalog struct {
	mu                       sync.Mutex
	ctx                      context.Context
	cancel                   context.CancelFunc
	documents                *shared.Documents
	index                    *index.Index
	facts                    analysis.FactStore
	queue                    *shared.WorkQueue
	roots                    map[string]*catalogRoot
	files                    map[source.URI]*catalogFile
	reading                  map[source.URI]chan struct{}
	queryFiles               []source.URI
	queryEpoch               uint64
	events                   chan struct{}
	publications             map[string]bool
	logs                     map[string]string
	symbolBytes, symbolCount int
}

func newCatalog() (*catalog, error) {
	documents, err := shared.NewDocuments(shared.DocumentLimits{Entries: 100002, FileBytes: 5 * 1024 * 1024, OpenBytes: catalogTextLimit})
	if err != nil {
		return nil, err
	}
	idx, err := index.New(index.Limits{Files: 100000, Symbols: 1000000, References: 2000000, Bytes: catalogTextLimit})
	if err != nil {
		return nil, err
	}
	queue, err := shared.NewWorkQueue(512, 100002)
	if err != nil {
		return nil, err
	}
	ctx, cancel := context.WithCancel(context.Background())
	c := &catalog{ctx: ctx, cancel: cancel, documents: documents, index: idx, queue: queue, roots: map[string]*catalogRoot{}, files: map[source.URI]*catalogFile{}, events: make(chan struct{}, 1), publications: map[string]bool{}, logs: map[string]string{}}
	go c.discover()
	for range 2 {
		go c.worker()
	}
	return c, nil
}

func (c *catalog) signal() {
	select {
	case c.events <- struct{}{}:
	default:
	}
}

func (c *catalog) notifications() ([]string, []string) {
	c.mu.Lock()
	defer c.mu.Unlock()
	result := make([]string, 0, len(c.publications))
	for uri := range c.publications {
		result = append(result, uri)
	}
	sort.Strings(result)
	logs := []string{}
	for _, line := range c.logs {
		logs = append(logs, line)
	}
	c.publications, c.logs = map[string]bool{}, map[string]string{}
	return result, logs
}

func (c *catalog) rootNames() []string {
	result := make([]string, 0, len(c.roots))
	for root := range c.roots {
		result = append(result, root)
	}
	return result
}

func (c *catalog) rootFor(uri source.URI) string {
	name, err := filePath(string(uri))
	if err != nil {
		return ""
	}
	best := ""
	for root := range c.roots {
		relative, err := filepath.Rel(root, name)
		if err == nil && relative != ".." && !strings.HasPrefix(relative, ".."+string(filepath.Separator)) && len(root) > len(best) {
			best = root
		}
	}
	return best
}

func catalogIdentity(header workspace.Workspace, override string) string {
	data, _ := json.Marshal(struct {
		Config   workspace.Config
		Targets  []workspace.Target
		Override string
	}{header.Config, header.Targets, override})
	return string(data)
}

// accept serializes input authority and index invalidation. Requests run on
// immutable session snapshots and never enter this transport mutation boundary.
func (s *session) accept(m message) (bool, int, error) {
	switch m.Method {
	case "initialize", "textDocument/didOpen", "textDocument/didChange", "textDocument/didClose", "textDocument/didSave", "workspace/didChangeWorkspaceFolders", "workspace/didChangeConfiguration", "workspace/didChangeWatchedFiles":
		s.catalog.mu.Lock()
		defer s.catalog.mu.Unlock()
		done, code, err := s.handle(m)
		if err == nil && !done && s.initialized && !s.shutdown {
			s.catalog.syncInputs(s, m)
		}
		return done, code, err
	default:
		return s.handle(m)
	}
}

// syncInputs runs after the transport owner accepts an event. Input authority
// and immediate index invalidation are serialized before workers can accept it.
func (c *catalog) syncInputs(s *session, m message) {
	current := map[string]bool{}
	// A remove/add event fences the old root lifetime even if the final set of
	// paths is identical. Workers must not reuse proofs from the removed root.
	rootsChanged := m.Method == "workspace/didChangeWorkspaceFolders"
	for _, root := range s.roots {
		current[root] = true
		r := c.roots[root]
		if r == nil {
			rootsChanged = true
			r = &catalogRoot{header: workspace.Workspace{Root: root}, priority: map[string]bool{}, problem: "configuration pending"}
			c.roots[root] = r
		}
		configURI, _ := fileuri.FromPath(filepath.Join(root, ".argo-workflows-ls.yaml"))
		var overlay *string
		unavailable := ""
		if b, ok := s.buffers[configURI]; ok {
			text := b.snapshot.Text()
			overlay = &text
			unavailable = b.unavailable
		}
		changed := r.override != s.override || !sameOverlay(r.overlay, overlay) || r.unavailable != unavailable || configurationEvent(m, configURI)
		r.override, r.overlay, r.unavailable = s.override, overlay, unavailable
		if changed {
			r.epoch++
			r.inputEpoch++
			r.reported = false
			r.checked, r.nextPass = time.Time{}, time.Time{}
			r.problem, r.scanProblem, r.complete = "configuration pending", "", false
			if unavailable != "" {
				r.problem = unavailable
			}
			for uri, file := range c.files {
				if file.root == root {
					file.target = workspace.Target{}
					c.mark(uri, shared.Background, true)
					if file.opened {
						c.publications[string(uri)] = true
					}
				}
			}
			c.signal()
		}
	}
	for root := range c.roots {
		if !current[root] {
			rootsChanged = true
			delete(c.roots, root)
		}
	}
	if rootsChanged {
		for _, root := range c.roots {
			// Registered roots change disk ownership even when configuration and
			// file bytes are identical. Resume from a new discovery pass.
			root.epoch++
			root.inputEpoch++
			root.done, root.complete, root.reported = false, false, false
			root.nextPass = time.Time{}
		}
	}
	for uri, file := range c.files {
		root := c.rootFor(uri)
		if root == "" {
			c.remove(uri)
			continue
		}
		if rootsChanged || file.root != root {
			file.root = root
			file.seen = c.roots[root].membership.Observe()
			file.target = workspace.Target{}
			c.mark(uri, shared.Related, true)
		}
		if _, ok := s.buffers[string(uri)]; !ok && file.opened {
			file.opened, file.blocked = false, false
			file.seen = c.roots[root].membership.Observe()
			c.mark(uri, shared.Related, true)
		}
	}
	for address, b := range s.buffers {
		uri := source.URI(address)
		root := c.rootFor(uri)
		path, _ := filePath(address)
		if root == "" || filepath.Base(path) == ".argo-workflows-ls.yaml" {
			continue
		}
		file := c.files[uri]
		if file == nil {
			if len(c.files) >= 100000 {
				c.roots[root].problem = "file index exceeds 100000 entries"
				continue
			}
			file = &catalogFile{root: root}
			c.addFile(uri, file)
		}
		if file.opened && file.revision == b.snapshot.Revision() && file.blocked == (b.unavailable != "") {
			continue
		}
		file.opened, file.revision, file.blocked = true, b.snapshot.Revision(), b.unavailable != ""
		file.seen = c.roots[root].membership.Observe()
		if file.blocked {
			file.stamp++ // Revoke work that has not obtained an index ticket yet.
			c.queue.Remove(uri)
			c.removeFacts(uri)
			file.problem = b.unavailable
			c.refreshReadiness(uri)
			continue
		}
		c.mark(uri, shared.Current, true)
		if len(c.roots[root].priority) < 64 {
			c.roots[root].priority[filepath.Dir(path)] = true
		}
	}
	if m.Method == "workspace/didChangeWatchedFiles" {
		var p struct {
			Changes []struct {
				URI  string `json:"uri"`
				Type int    `json:"type"`
			} `json:"changes"`
		}
		if json.Unmarshal(m.Params, &p) == nil {
			for _, change := range p.Changes {
				c.diskEvent(source.URI(change.URI), change.Type == 3)
			}
		}
	}
	if m.Method == "textDocument/didSave" {
		var p struct {
			TextDocument struct {
				URI string `json:"uri"`
			} `json:"textDocument"`
		}
		if json.Unmarshal(m.Params, &p) == nil {
			c.diskEvent(source.URI(p.TextDocument.URI), false)
		}
	}
}

func (c *catalog) diskEvent(uri source.URI, deleted bool) {
	if _, open := c.documents.Snapshot(uri); open {
		return
	}
	root := c.rootFor(uri)
	if root == "" {
		return
	}
	if deleted {
		c.remove(uri)
		return
	}
	path, err := filePath(string(uri))
	if err != nil {
		return
	}
	ok, err := workspace.Candidate(c.roots[root].header, path, c.rootNames())
	if err != nil {
		c.roots[root].problem = err.Error()
		return
	}
	if !ok {
		return
	}
	if c.files[uri] == nil {
		if len(c.files) >= 100000 {
			c.roots[root].problem = "file index exceeds 100000 entries"
			return
		}
		c.addFile(uri, &catalogFile{root: root})
	}
	c.files[uri].seen = c.roots[root].membership.Observe()
	c.documents.Invalidate(uri)
	c.mark(uri, shared.Related, true)
}

func (c *catalog) mark(uri source.URI, priority shared.Priority, invalidate bool) {
	defer c.refreshReadiness(uri)
	file := c.files[uri]
	if file == nil || file.blocked {
		return
	}
	if invalidate {
		file.stamp++
		file.observed = false
		c.invalidate(uri, file)
		if root := c.roots[file.root]; root != nil {
			root.complete, root.reported = false, false
		}
	}
	if root := c.roots[file.root]; root == nil || root.problem != "" {
		return
	}
	if file.opened {
		priority = shared.Current
	}
	if err := c.queue.Put(shared.Job{URI: uri, Priority: priority}); err != nil {
		file.problem = err.Error()
	}
}

func (c *catalog) invalidate(uri source.URI, file *catalogFile) {
	c.facts.Delete(uri)
	scope := file.target.ID
	if scope == "" {
		scope = file.root + "#root"
	}
	_, err := c.index.Begin(uri, index.Scope(scope), file.revision)
	if err != nil {
		c.removeFacts(uri)
		file.problem = err.Error()
		return
	}
	file.invalidated = true
}

func (c *catalog) removeFacts(uri source.URI) {
	c.facts.Delete(uri)
	change, err := c.index.Remove(uri)
	if err == nil {
		c.affected(change.Affected)
	}
	if file := c.files[uri]; file != nil {
		file.invalidated = true
	}
}

func (c *catalog) remove(uri source.URI) {
	c.documents.Invalidate(uri)
	if done := c.reading[uri]; done != nil {
		delete(c.reading, uri)
		close(done)
	}
	c.queue.Remove(uri)
	c.removeFacts(uri)
	if file := c.files[uri]; file != nil {
		c.symbolBytes -= file.symbolBytes
		c.symbolCount -= len(file.symbols)
		file.readiness.add(-1)
		c.removeQueryFile(uri, file)
	}
	delete(c.files, uri)
}

// Dependents are URI based. Follow exported keys transitively so an unchanged
// caller between a changed template and an open caller cannot stop invalidation.
func (c *catalog) affected(uris []source.URI) {
	seen := map[source.URI]bool{}
	queue := []source.URI{}
	add := func(uri source.URI) {
		if !seen[uri] {
			seen[uri] = true
			queue = append(queue, uri)
		}
	}
	for _, uri := range uris {
		add(uri)
	}
	keys := map[struct {
		scope index.Scope
		key   index.Key
	}]bool{}
	for len(queue) > 0 {
		uri := queue[0]
		queue = queue[1:]
		if file := c.files[uri]; file != nil && file.opened {
			c.publications[string(uri)] = true
		}
		file, found, _ := c.index.GetFile(uri)
		if !found || !file.Fresh {
			continue
		}
		for _, symbol := range file.Facts.Symbols {
			key := struct {
				scope index.Scope
				key   index.Key
			}{file.Scope, symbol.Key}
			if keys[key] {
				continue
			}
			keys[key] = true
			dependencies, _ := c.index.Dependents(file.Scope, symbol.Key)
			for _, dependency := range dependencies {
				add(dependency)
			}
		}
	}
	if len(c.publications) > 0 {
		c.signal()
	}
}

func (c *catalog) worker() {
	for c.ctx.Err() == nil {
		if ticket, ok := c.queue.Pop(); ok {
			c.process(ticket)
			continue
		}
		select {
		case <-c.ctx.Done():
			return
		case <-c.queue.Wake():
		}
	}
}

func (c *catalog) process(job shared.WorkTicket) {
	defer c.complete(job)
	uri := job.URI
	c.mu.Lock()
	file := c.files[uri]
	if file == nil || file.blocked {
		c.mu.Unlock()
		return
	}
	r := c.roots[file.root]
	if r == nil || r.problem != "" {
		c.mu.Unlock()
		return
	}
	stamp, epoch, header, override, opened := file.stamp, r.epoch, r.header, r.override, file.opened
	roots := c.rootNames()
	c.mu.Unlock()
	ctx, cancel := context.WithTimeout(c.ctx, 5*time.Second)
	defer cancel()
	if !opened {
		path, pathErr := filePath(string(uri))
		candidate, candidateErr := workspace.Candidate(header, path, roots)
		if pathErr != nil || candidateErr != nil {
			if pathErr != nil {
				candidateErr = pathErr
			}
			c.finishFile(uri, file, stamp, epoch, candidateErr)
			return
		}
		if !candidate {
			c.mu.Lock()
			if c.files[uri] == file && file.stamp == stamp && r.epoch == epoch {
				c.remove(uri)
			}
			c.mu.Unlock()
			return
		}
	}
	empty, err := source.New(uri, 0, "")
	if err != nil {
		c.finishFile(uri, file, stamp, epoch, err)
		return
	}
	selected, err := workspace.OpenDocument(header, empty, override)
	if err != nil {
		c.finishFile(uri, file, stamp, epoch, err)
		return
	}
	c.mu.Lock()
	if c.files[uri] == file && file.stamp == stamp && r.epoch == epoch {
		file.target = selected.Target
		c.refreshReadiness(uri)
	}
	c.mu.Unlock()
	read, err := c.readSnapshot(ctx, uri)
	if err != nil {
		c.finishFile(uri, file, stamp, epoch, err)
		return
	}
	snapshot, stamp := read.Snapshot, read.input.stamp
	document, err := workspace.OpenDocument(header, snapshot, override)
	if err != nil {
		c.finishFile(uri, file, stamp, epoch, err)
		return
	}
	hash := sha256.Sum256([]byte(snapshot.Text()))
	c.mu.Lock()
	if c.files[uri] != file || file.stamp != stamp || c.roots[file.root] != r || r.epoch != epoch {
		c.mu.Unlock()
		return
	}
	unchanged := file.hash == hash && file.config == r.identity && file.target.ID == document.Target.ID && file.problem == ""
	if unchanged && !file.invalidated {
		c.mu.Unlock()
		return
	}
	ticket, err := c.index.Begin(uri, index.Scope(document.Target.ID), snapshot.Revision())
	var change index.Change
	reused := false
	if err == nil {
		file.invalidated = true
		if unchanged {
			change, err = c.index.Reuse(ticket)
			reused = err == nil
			if errors.Is(err, index.ErrNoPrevious) {
				err = nil // This valid ticket can still commit a newly parsed summary.
			}
		}
	}
	c.refreshReadiness(uri)
	c.mu.Unlock()
	if err != nil {
		c.finishFile(uri, file, stamp, epoch, err)
		return
	}
	var summary analysis.FileSummary
	var facts *analysis.Facts
	if !reused {
		ctx = analysis.WithInvocation(ctx)
		summary, err = analysis.Summarize(ctx, header, document)
		if err == nil {
			facts = analysis.CaptureDocumentFacts(ctx, document)
		}
	}
	c.mu.Lock()
	defer c.mu.Unlock()
	defer c.refreshReadiness(uri)
	if c.files[uri] != file || file.stamp != stamp || c.roots[file.root] != r || r.epoch != epoch {
		return
	}
	if !reused {
		r.parses++
	}
	if err != nil {
		file.problem = err.Error()
		r.complete = false
		c.signal()
		return
	}
	symbolBytes := file.symbolBytes
	if !reused {
		symbolBytes = 0
		for _, symbol := range summary.Symbols {
			symbolBytes += len(symbol.Name) + len(symbol.Container) + len(symbol.Location.URI) + 64
		}
		if symbolBytes > 64*1024*1024-(c.symbolBytes-file.symbolBytes) || len(summary.Symbols) > 1000000-(c.symbolCount-len(file.symbols)) {
			file.problem = "document symbol summaries exceed the bounded index budget"
			r.complete = false
			c.signal()
			return
		}
		change, err = c.index.Commit(ticket, summary.Facts)
		if err != nil {
			file.problem = err.Error()
			r.complete = false
			c.signal()
			return
		}
	}
	file.hash, file.target, file.config, file.revision = hash, document.Target, r.identity, snapshot.Revision()
	file.problem, file.invalidated = "", false
	if !reused {
		c.symbolBytes += symbolBytes - file.symbolBytes
		c.symbolCount += len(summary.Symbols) - len(file.symbols)
		file.symbols, file.symbolBytes, file.owned = summary.Symbols, symbolBytes, summary.Owned
		if ctx.Err() == nil {
			c.facts.Store(facts)
		}
	}
	c.affected(change.Affected)
}

func (c *catalog) finishFile(uri source.URI, file *catalogFile, stamp, epoch uint64, err error) {
	c.mu.Lock()
	defer c.mu.Unlock()
	defer c.refreshReadiness(uri)
	r := c.roots[file.root]
	if c.files[uri] != file || file.stamp != stamp || r == nil || r.epoch != epoch {
		return
	}
	if os.IsNotExist(err) && !file.opened {
		c.remove(uri)
		return
	}
	if err == shared.ErrStale {
		c.mark(uri, shared.Related, false)
		return
	}
	file.problem = err.Error()
	r.complete = false
	c.signal()
}

func sameOverlay(a, b *string) bool {
	if a == nil || b == nil {
		return a == b
	}
	return *a == *b
}

func configurationEvent(m message, uri string) bool {
	if m.Method == "workspace/didChangeConfiguration" {
		return true
	}
	if m.Method != "workspace/didChangeWatchedFiles" && m.Method != "textDocument/didSave" {
		return false
	}
	var p struct {
		TextDocument struct {
			URI string `json:"uri"`
		} `json:"textDocument"`
		Changes []struct {
			URI string `json:"uri"`
		} `json:"changes"`
	}
	if json.Unmarshal(m.Params, &p) != nil {
		return false
	}
	if p.TextDocument.URI == uri {
		return true
	}
	for _, change := range p.Changes {
		if change.URI == uri {
			return true
		}
	}
	return false
}
