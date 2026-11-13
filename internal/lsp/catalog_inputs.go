package lsp

import (
	"context"
	"crypto/sha256"
	"fmt"
	"os"
	"sort"
	"strings"
	"time"

	"github.com/lazysnail1024/k8s-crd-toolkit/index"
	"github.com/lazysnail1024/k8s-crd-toolkit/source"
	shared "github.com/lazysnail1024/k8s-crd-toolkit/workspace"
	"github.com/lazysnail1024/zed-argo-workflows/internal/analysis"
	"github.com/lazysnail1024/zed-argo-workflows/internal/workspace"
)

// catalogSnapshot binds the bytes to the input identity at publication. An
// index worker may catch up later without changing that identity; another read
// of different bytes or an authoritative notification must revoke it.
type catalogSnapshot struct {
	source.Snapshot
	input             catalogInput
	changed, repaired bool
}

func (c *catalog) readSnapshot(ctx context.Context, uri source.URI) (catalogSnapshot, error) {
	for {
		c.mu.Lock()
		file := c.files[uri]
		if file != nil && file.blocked {
			problem := file.problem
			if problem == "" {
				problem = "opened document is unavailable"
			}
			c.mu.Unlock()
			return catalogSnapshot{}, fmt.Errorf("%s", problem)
		}
		if snapshot, opened := c.documents.Snapshot(uri); opened {
			defer c.mu.Unlock()
			return c.observeSnapshot(uri, snapshot), nil
		}
		if pending := c.reading[uri]; pending != nil {
			c.mu.Unlock()
			select {
			case <-ctx.Done():
				return catalogSnapshot{}, ctx.Err()
			case <-pending:
				continue
			}
		}
		path, err := filePath(string(uri))
		if err != nil {
			c.mu.Unlock()
			return catalogSnapshot{}, err
		}
		// Documents intentionally revokes an older ticket when a new read
		// begins. Serialize only this URI's I/O/publication window so a
		// background read cannot revoke a foreground read of unchanged bytes.
		// Parsing and indexing never hold this short-lived reservation.
		if c.reading == nil {
			c.reading = map[source.URI]chan struct{}{}
		}
		done := make(chan struct{})
		c.reading[uri] = done
		ticket, err := c.documents.BeginRead(uri)
		c.mu.Unlock()
		if err != nil {
			c.releaseRead(uri, done)
			return catalogSnapshot{}, err
		}
		return c.readDiskSnapshot(ctx, uri, path, ticket, done)
	}
}

func (c *catalog) releaseRead(uri source.URI, done chan struct{}) {
	c.mu.Lock()
	defer c.mu.Unlock()
	// Removal can retire a reservation before its reader exits. The old
	// reader must not release a recreated URI's newer reservation.
	if c.reading[uri] == done {
		delete(c.reading, uri)
		close(done)
	}
}

func (c *catalog) readDiskSnapshot(ctx context.Context, uri source.URI, path string, ticket shared.ReadTicket, done chan struct{}) (catalogSnapshot, error) {
	defer c.releaseRead(uri, done)
	defer c.documents.DiscardRead(ticket)
	text, err := workspace.ReadText(ctx, path)
	if err != nil {
		return catalogSnapshot{}, err
	}
	if err := ctx.Err(); err != nil {
		return catalogSnapshot{}, err
	}
	// Publish and record the observed bytes under the same catalog lock as
	// notifications. A later successful read cannot be overwritten by an older
	// reader that was paused between Documents publication and proof capture.
	c.mu.Lock()
	defer c.mu.Unlock()
	snapshot, err := c.documents.PublishRead(ticket, text)
	if err != nil {
		return catalogSnapshot{}, err
	}
	file := c.files[uri]
	if file != nil {
		if root := c.roots[file.root]; root != nil {
			root.reads++
			root.bytes += uint64(len(text))
		}
	}
	read := c.observeSnapshot(uri, snapshot)
	if file != nil && file.hash == read.input.hash && file.revision != 0 {
		read.Snapshot, err = source.New(uri, file.revision, text)
	}
	return read, err
}

// observeSnapshot requires c.mu. Retain only a hash of the latest source, even
// while an older summary is pending. Queue occupancy says nothing about which
// bytes a running worker is parsing, so it cannot stand in for this identity.
func (c *catalog) observeSnapshot(uri source.URI, snapshot source.Snapshot) catalogSnapshot {
	hash := sha256.Sum256([]byte(snapshot.Text()))
	read := catalogSnapshot{Snapshot: snapshot, input: catalogInput{hash: hash}}
	file := c.files[uri]
	if file == nil {
		return read
	}
	previous := file.hash
	if file.observed {
		previous = file.observedHash
	}
	read.changed = previous != hash && (file.observed || !file.invalidated)
	if read.changed {
		file.stamp++
		c.invalidate(uri, file)
		if root := c.roots[file.root]; root != nil {
			root.complete, root.reported = false, false
		}
	}
	file.observedHash, file.observed = hash, true
	read.input.stamp = file.stamp
	read.repaired = read.changed || file.hash != hash
	c.refreshReadiness(uri)
	return read
}

func (c *catalog) header(root string) (workspace.Workspace, bool, error) {
	c.mu.Lock()
	defer c.mu.Unlock()
	r := c.roots[root]
	if r == nil {
		return workspace.Workspace{}, false, fmt.Errorf("workspace index is not initialized")
	}
	if r.problem != "" {
		return r.header, false, fmt.Errorf("%s", r.problem)
	}
	return r.header, r.complete, nil
}

func (c *catalog) scopeReadyLocked(root *catalogRoot, target string) bool {
	if root == nil || root.problem != "" || root.scanProblem != "" || !root.done {
		return false
	}
	return root.unknown == 0 && root.unready[target] == 0
}

func (c *catalog) scopeReady(root, target string) bool {
	c.mu.Lock()
	defer c.mu.Unlock()
	return c.scopeReadyLocked(c.roots[root], target)
}

// A local-only publication does not acknowledge completed external diagnostics.
// Remember it on the bounded open-file entry until its scope can be evaluated.
// Foreground queries never advance this publication state.
func (c *catalog) acceptedPublication(uri string, local bool) bool {
	c.mu.Lock()
	defer c.mu.Unlock()
	file := c.files[source.URI(uri)]
	if file == nil || !file.opened {
		return false
	}
	ready := c.scopeReadyLocked(c.roots[file.root], file.target.ID)
	file.awaiting = local && !ready
	return local && ready
}

func (c *catalog) limited(root, target string) string {
	c.mu.Lock()
	defer c.mu.Unlock()
	r := c.roots[root]
	if r == nil || !r.reported || r.complete {
		return ""
	}
	if r.scanProblem != "" {
		return r.scanProblem
	}
	for _, file := range c.files {
		if file.root == root && (file.target.ID == target || file.target.ID == "") && file.problem != "" {
			return file.problem
		}
	}
	return "workspace inputs are incomplete"
}

func (c *catalog) document(ctx context.Context, uri string) (workspace.Document, catalogProof, error) {
	for {
		if err := ctx.Err(); err != nil {
			return workspace.Document{}, catalogProof{}, err
		}
		c.mu.Lock()
		root := c.rootFor(source.URI(uri))
		r := c.roots[root]
		if r == nil {
			c.mu.Unlock()
			return workspace.Document{}, catalogProof{}, fmt.Errorf("document is outside workspace")
		}
		if r.problem == "configuration pending" {
			// Describe is metadata-only. A local closed-file request waits for
			// that result, never for directory discovery or scope completeness.
			c.mu.Unlock()
			select {
			case <-ctx.Done():
				return workspace.Document{}, catalogProof{}, ctx.Err()
			case <-time.After(5 * time.Millisecond):
			}
			continue
		}
		if r.problem != "" {
			problem := r.problem
			c.mu.Unlock()
			return workspace.Document{}, catalogProof{}, fmt.Errorf("%s", problem)
		}
		header, override := r.header, r.override
		proof := catalogProof{root: root, epoch: r.epoch}
		file := c.files[source.URI(uri)]
		var stamp uint64
		if file != nil {
			stamp = file.stamp
			proof.files = map[source.URI]catalogInput{source.URI(uri): {stamp, file.hash}}
		}
		c.mu.Unlock()
		read, err := c.readSnapshot(ctx, source.URI(uri))
		if err != nil {
			return workspace.Document{}, proof, err
		}
		document, err := workspace.OpenDocument(header, read.Snapshot, override)
		if err != nil {
			return document, proof, err
		}
		c.mu.Lock()
		if c.roots[root] != r || r.epoch != proof.epoch || c.files[source.URI(uri)] != file || file != nil && file.stamp != read.input.stamp {
			c.mu.Unlock()
			return workspace.Document{}, proof, shared.ErrStale
		}
		if file != nil {
			hash := read.input.hash
			if read.changed || read.repaired && !c.queue.Pending(source.URI(uri)) {
				// A newer source must survive an older worker's exit, including
				// when that worker is still holding its queue reservation.
				c.mark(source.URI(uri), shared.Related, false)
			}
			proof.repaired = read.repaired
			proof.files[source.URI(uri)] = catalogInput{file.stamp, hash}
		}
		c.mu.Unlock()
		c.facts.Restore(ctx, workspace.Workspace{Documents: []workspace.Document{document}})
		return document, proof, nil
	}
}

// closure follows known key candidates without interpreting absence as proof.
// Reverse traversal is only needed for references/edits, not ordinary caller
// diagnostics or definition lookup into a widely used template library.
func (c *catalog) closure(ctx context.Context, header workspace.Workspace, selected, focus string, reverse, all, repair bool) ([]source.URI, error) {
	ctx = analysis.WithInvocation(ctx)
	seen := map[source.URI]bool{}
	queue := []source.URI{}
	add := func(uri source.URI) {
		if !seen[uri] {
			seen[uri] = true
			queue = append(queue, uri)
		}
	}
	if focus != "" {
		add(source.URI(focus))
	}
	if all || reverse {
		c.mu.Lock()
		for uri, file := range c.files {
			if file.root == header.Root && file.owned && (all || file.opened) && (selected == "" || file.target.Scope == selected) {
				add(uri)
			}
		}
		c.mu.Unlock()
	}
	visitedKeys := map[struct {
		scope   index.Scope
		key     index.Key
		reverse bool
	}]bool{}
	for n := 0; n < len(queue); n++ {
		if err := ctx.Err(); err != nil {
			return nil, err
		}
		uri := queue[n]
		file, found, _ := c.index.GetFile(uri)
		if (!found || !file.Fresh) && string(uri) == focus {
			document, _, err := c.document(ctx, focus)
			if err != nil {
				return nil, err
			}
			summary, err := analysis.Summarize(ctx, header, document)
			if err != nil {
				return nil, err
			}
			file = index.File{URI: uri, Scope: index.Scope(document.Target.ID), Facts: summary.Facts, Fresh: true}
		}
		if !file.Fresh {
			continue
		}
		keys := append([]index.Key(nil), file.Facts.Dependencies...)
		for _, ref := range file.Facts.References {
			keys = append(keys, ref.Target)
		}
		for _, key := range keys {
			if err := ctx.Err(); err != nil {
				return nil, err
			}
			identity := struct {
				scope   index.Scope
				key     index.Key
				reverse bool
			}{file.Scope, key, false}
			if visitedKeys[identity] {
				continue
			}
			visitedKeys[identity] = true
			definitions, _ := c.index.Definitions(file.Scope, key)
			if repair && string(uri) == focus && len(definitions) == 0 {
				var err error
				definitions, err = analysis.ResourceAlternatives(ctx, c.index, file.Scope, key)
				if err != nil {
					return nil, err
				}
			}
			for _, definition := range definitions {
				add(definition.URI)
			}
		}
		if !reverse {
			continue
		}
		for _, symbol := range file.Facts.Symbols {
			if err := ctx.Err(); err != nil {
				return nil, err
			}
			identity := struct {
				scope   index.Scope
				key     index.Key
				reverse bool
			}{file.Scope, symbol.Key, true}
			if visitedKeys[identity] {
				continue
			}
			visitedKeys[identity] = true
			dependents, _ := c.index.Dependents(file.Scope, symbol.Key)
			for _, dependent := range dependents {
				add(dependent)
			}
		}
	}
	sort.Slice(queue, func(a, b int) bool { return queue[a] < queue[b] })
	return queue, nil
}

type catalogInput struct {
	stamp uint64
	hash  [32]byte
}

type catalogProof struct {
	root     string
	epoch    uint64
	states   map[index.Scope]uint64
	files    map[source.URI]catalogInput
	repaired bool
}

func (c *catalog) valid(proofs []catalogProof) bool {
	c.mu.Lock()
	defer c.mu.Unlock()
	return c.validLocked(proofs)
}

// validLocked requires c.mu, allowing callers to fence publication or retention
// with the same input proof without a validation-to-publication gap.
func (c *catalog) validLocked(proofs []catalogProof) bool {
	for _, proof := range proofs {
		root := c.roots[proof.root]
		if root == nil || root.epoch != proof.epoch {
			return false
		}
		for scope, generation := range proof.states {
			if !c.scopeReadyLocked(root, string(scope)) || c.scopeGeneration(root, string(scope)) != generation {
				return false
			}
		}
		for uri, input := range proof.files {
			file := c.files[uri]
			if file == nil || !file.invalidated && file.hash != input.hash {
				return false
			}
			if file.stamp != input.stamp {
				return false
			}
		}
	}
	return true
}

func (c *catalog) candidateProof(root string, candidates []index.Definition) (catalogProof, bool) {
	c.mu.Lock()
	defer c.mu.Unlock()
	r := c.roots[root]
	proof := catalogProof{root: root, files: map[source.URI]catalogInput{}}
	if r == nil {
		return proof, false
	}
	proof.epoch = r.epoch
	for _, candidate := range candidates {
		file := c.files[candidate.URI]
		if file == nil || file.blocked || file.problem != "" || file.invalidated || file.revision != candidate.Revision {
			return proof, false
		}
		proof.files[candidate.URI] = catalogInput{file.stamp, file.hash}
	}
	return proof, true
}

func (c *catalog) workspace(ctx context.Context, root, selected, focus string, reverse, all, repair bool) (workspace.Workspace, catalogProof, error) {
	proof := catalogProof{root: root, files: map[source.URI]catalogInput{}, states: map[index.Scope]uint64{}}
	c.mu.Lock()
	r := c.roots[root]
	if r == nil || r.problem != "" {
		problem := "workspace index is not initialized"
		if r != nil {
			problem = r.problem
		}
		c.mu.Unlock()
		return workspace.Workspace{}, proof, fmt.Errorf("%s", problem)
	}
	header := r.header
	proof.epoch = r.epoch
	incomplete := map[string]bool{}
	for _, target := range header.Targets {
		if selected != "" && target.Scope != selected {
			continue
		}
		if c.scopeReadyLocked(r, target.ID) {
			proof.states[index.Scope(target.ID)] = c.scopeGeneration(r, target.ID)
		} else {
			incomplete[target.ID] = true
		}
	}
	c.mu.Unlock()
	uris, err := c.closure(ctx, header, selected, focus, reverse, all, repair)
	if err != nil {
		return header, proof, err
	}
	w := header
	w.Documents = nil
	w.IncompleteScopes = incomplete
	for _, uri := range uris {
		document, observed, err := c.document(ctx, string(uri))
		if err != nil {
			if os.IsNotExist(err) {
				c.mu.Lock()
				c.remove(uri)
				c.mu.Unlock()
			}
			proof.states = nil
			w.NotEvaluated = append(w.NotEvaluated, string(uri)+": "+err.Error())
			for _, target := range w.Targets {
				w.IncompleteScopes[target.ID] = true
			}
			continue
		}
		if selected != "" && document.Target.Scope != selected {
			continue
		}
		c.mu.Lock()
		file := c.files[uri]
		if observed.repaired {
			delete(proof.states, index.Scope(document.Target.ID))
			w.IncompleteScopes[document.Target.ID] = true
		}
		if file == nil || file.problem != "" || file.invalidated {
			w.IncompleteScopes[document.Target.ID] = true
		}
		if input, ok := observed.files[uri]; ok {
			// Preserve the stamp from the read, not a later watch notification.
			proof.files[uri] = input
		}
		if strings.HasPrefix(document.Target.Scope, "opened:") && file != nil && file.problem == "" {
			delete(w.IncompleteScopes, document.Target.ID)
		}
		c.mu.Unlock()
		w.Documents = append(w.Documents, document)
	}
	return w, proof, nil
}
