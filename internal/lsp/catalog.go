package lsp

import (
	"context"
	"crypto/sha256"
	"encoding/json"
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
	epoch, pass, scanEpoch                                uint64
	scanner                                               *shared.Scanner // Used only by discover, including Close/Prioritize.
	backlog                                               []string
	priority                                              map[string]bool
	done, complete, reported                              bool
	lastState                                             string
	nextPass                                              time.Time
	checked                                               time.Time
	reportedAt                                            time.Time
	overlay                                               *string
	reads, bytes, parses                                  uint64
}

type catalogFile struct {
	root                                                          string
	target                                                        workspace.Target
	stamp, seen, observed                                         uint64
	revision                                                      source.Revision
	hash                                                          [32]byte
	config, problem                                               string
	opened, blocked, dirty, running, invalidated, owned, awaiting bool
	previous                                                      *index.Facts
	symbols                                                       []analysis.DomainSymbol
	symbolBytes, factsCost, previousBytes                         int
}

// catalog survives every request/publication worker. Its maps retain identities,
// summaries and authoritative buffers, never closed-file text, a Workspace, or
// an analysis invocation. Existing optional parse facts have their own budget.
type catalog struct {
	mu                                      sync.Mutex
	ctx                                     context.Context
	cancel                                  context.CancelFunc
	documents                               *shared.Documents
	index                                   *index.Index
	queue                                   *shared.Queue
	roots                                   map[string]*catalogRoot
	files                                   map[source.URI]*catalogFile
	events                                  chan struct{}
	publications                            map[string]bool
	logs                                    map[string]string
	openBytes                               int
	symbolBytes, symbolCount, previousBytes int
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
	queue, err := shared.NewQueue(512)
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

// syncInputs runs after the transport owner accepts an event. Input authority
// and immediate index invalidation are serialized before workers can accept it.
func (c *catalog) syncInputs(s *session, m message) {
	c.mu.Lock()
	defer c.mu.Unlock()
	c.openBytes = s.bufferBytes
	current := map[string]bool{}
	rootsChanged := false
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
		if file.root != root {
			file.root = root
			file.seen = c.roots[root].pass
			file.target = workspace.Target{}
			c.mark(uri, shared.Related, true)
		}
		if _, ok := s.buffers[string(uri)]; !ok && file.opened {
			file.opened, file.blocked = false, false
			c.documents.Close(uri)
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
			c.files[uri] = file
		}
		if file.opened && file.observed == uint64(b.snapshot.Revision()) && file.blocked == (b.unavailable != "") {
			continue
		}
		file.opened, file.observed, file.blocked = true, uint64(b.snapshot.Revision()), b.unavailable != ""
		if file.blocked {
			c.documents.Close(uri)
			c.removeFacts(uri)
			file.problem = b.unavailable
			continue
		}
		var snapshot source.Snapshot
		var err error
		if previous, exists := c.documents.Snapshot(uri); exists {
			snapshot, err = c.documents.Change(uri, previous.Revision(), []source.Change{{Range: source.Span{End: len(previous.Text())}, Text: b.snapshot.Text()}})
		} else {
			snapshot, err = c.documents.Open(uri, b.snapshot.Text())
		}
		if err != nil {
			file.problem = err.Error()
			c.removeFacts(uri)
			continue
		}
		file.revision = snapshot.Revision()
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
		c.files[uri] = &catalogFile{root: root}
	}
	c.files[uri].seen = c.roots[root].pass
	c.documents.Invalidate(uri)
	c.mark(uri, shared.Related, true)
}

func (c *catalog) mark(uri source.URI, priority shared.Priority, invalidate bool) {
	file := c.files[uri]
	if file == nil || file.blocked {
		return
	}
	file.stamp++
	file.dirty = true
	if invalidate {
		c.invalidate(uri, file)
		if root := c.roots[file.root]; root != nil {
			root.complete, root.reported = false, false
		}
	}
	// A full queue leaves a dirty bit on bounded file metadata. Discovery pauses;
	// workers refill freed slots, rather than dropping an accepted file event.
	if root := c.roots[file.root]; root == nil || root.problem != "" {
		return
	}
	if err := c.queue.Put(shared.Job{URI: uri, Priority: priority}); err != nil && err != shared.ErrLimit {
		file.problem = err.Error()
	}
}

func (c *catalog) invalidate(uri source.URI, file *catalogFile) {
	if file.previous == nil && file.factsCost <= 32*1024*1024-c.previousBytes {
		if previous, found, _ := c.index.GetFile(uri); found && previous.Fresh {
			facts := previous.Facts
			file.previous = &facts
			file.previousBytes = file.factsCost
			c.previousBytes += file.previousBytes
		}
	}
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
	change, err := c.index.Remove(uri)
	if err == nil {
		c.affected(change.Affected)
	}
	if file := c.files[uri]; file != nil {
		c.releasePrevious(file)
		file.invalidated = true
	}
}

func (c *catalog) remove(uri source.URI) {
	c.documents.Close(uri)
	c.queue.Remove(uri)
	c.removeFacts(uri)
	if file := c.files[uri]; file != nil {
		c.symbolBytes -= file.symbolBytes
		c.symbolCount -= len(file.symbols)
		c.previousBytes -= file.previousBytes
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
	timer := time.NewTicker(5 * time.Millisecond)
	defer timer.Stop()
	for {
		if c.ctx.Err() != nil {
			return
		}
		job, ok := c.queue.Pop()
		if ok {
			c.process(job.URI)
			continue
		}
		select {
		case <-c.ctx.Done():
			return
		case <-timer.C:
		}
	}
}

func (c *catalog) process(uri source.URI) {
	c.mu.Lock()
	file := c.files[uri]
	if file == nil || file.blocked || !file.dirty || file.running {
		c.mu.Unlock()
		return
	}
	r := c.roots[file.root]
	if r == nil || r.problem != "" {
		c.mu.Unlock()
		return
	}
	file.running, file.dirty = true, false
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
			file.running = false
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
	}
	c.mu.Unlock()
	snapshot, err := c.readSnapshot(ctx, uri)
	if err != nil {
		c.finishFile(uri, file, stamp, epoch, err)
		return
	}
	document, err := workspace.OpenDocument(header, snapshot, override)
	if err != nil {
		c.finishFile(uri, file, stamp, epoch, err)
		return
	}
	hash := sha256.Sum256([]byte(snapshot.Text()))
	c.mu.Lock()
	if c.files[uri] != file || file.stamp != stamp || c.roots[file.root] != r || r.epoch != epoch {
		file.running = false
		c.mu.Unlock()
		return
	}
	unchanged := file.hash == hash && file.config == r.identity && file.target.ID == document.Target.ID && file.problem == ""
	if unchanged && !file.invalidated {
		file.running = false
		c.mu.Unlock()
		return
	}
	if !unchanged && !file.invalidated {
		// A missed-watch read has now observed different bytes. Withdraw old
		// locations before parsing, and revoke results captured during the read.
		file.stamp++
		stamp = file.stamp
	}
	ticket, err := c.index.Begin(uri, index.Scope(document.Target.ID), snapshot.Revision())
	if err == nil {
		file.invalidated = true
	}
	previous := file.previous
	c.mu.Unlock()
	if err != nil {
		c.finishFile(uri, file, stamp, epoch, err)
		return
	}
	var summary analysis.FileSummary
	reused := unchanged && previous != nil
	if reused {
		summary.Facts = *previous
	} else {
		ctx = analysis.WithInvocation(ctx)
		summary, err = analysis.Summarize(ctx, header, document)
	}
	c.mu.Lock()
	defer c.mu.Unlock()
	file.running = false
	if c.files[uri] != file || file.stamp != stamp || c.roots[file.root] != r || r.epoch != epoch {
		return
	}
	if !reused {
		r.parses++
	}
	if err != nil {
		file.problem = err.Error()
		c.releasePrevious(file)
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
			c.releasePrevious(file)
			r.complete = false
			c.signal()
			return
		}
	}
	change, err := c.index.Commit(ticket, summary.Facts)
	if err != nil {
		file.problem = err.Error()
		c.releasePrevious(file)
		r.complete = false
		c.signal()
		return
	}
	file.hash, file.target, file.config, file.revision = hash, document.Target, r.identity, snapshot.Revision()
	file.problem, file.invalidated = "", false
	c.releasePrevious(file)
	file.factsCost = len(summary.Facts.Symbols)*112 + len(summary.Facts.References)*32 + len(summary.Facts.Dependencies)*16
	if !reused {
		c.symbolBytes += symbolBytes - file.symbolBytes
		c.symbolCount += len(summary.Symbols) - len(file.symbols)
		file.symbols, file.symbolBytes, file.owned = summary.Symbols, symbolBytes, summary.Owned
	}
	c.affected(change.Affected)
}

func (c *catalog) finishFile(uri source.URI, file *catalogFile, stamp, epoch uint64, err error) {
	c.mu.Lock()
	defer c.mu.Unlock()
	file.running = false
	r := c.roots[file.root]
	if c.files[uri] != file || file.stamp != stamp || r == nil || r.epoch != epoch {
		return
	}
	if isMissing(err) && !file.opened {
		c.remove(uri)
		return
	}
	if err == shared.ErrStale {
		c.mark(uri, shared.Related, false)
		return
	}
	file.problem = err.Error()
	c.releasePrevious(file)
	r.complete = false
	c.signal()
}

func (c *catalog) releasePrevious(file *catalogFile) {
	c.previousBytes -= file.previousBytes
	file.previous, file.previousBytes = nil, 0
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
