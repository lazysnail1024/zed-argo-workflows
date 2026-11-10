package lsp

import (
	"context"
	"crypto/sha256"
	"fmt"
	"os"
	"sort"
	"strings"

	"github.com/lazysnail1024/k8s-crd-toolkit/index"
	"github.com/lazysnail1024/k8s-crd-toolkit/source"
	shared "github.com/lazysnail1024/k8s-crd-toolkit/workspace"
	"github.com/lazysnail1024/zed-argo-workflows/internal/analysis"
	"github.com/lazysnail1024/zed-argo-workflows/internal/workspace"
)

func isMissing(err error) bool { return os.IsNotExist(err) }

func (c *catalog) readSnapshot(ctx context.Context, uri source.URI) (source.Snapshot, error) {
	c.mu.Lock()
	file := c.files[uri]
	blocked := file != nil && file.blocked
	problem := "opened document is unavailable"
	if blocked && file.problem != "" {
		problem = file.problem
	}
	c.mu.Unlock()
	if blocked {
		return source.Snapshot{}, fmt.Errorf("%s", problem)
	}
	if snapshot, opened := c.documents.Snapshot(uri); opened {
		return snapshot, nil
	}
	path, err := filePath(string(uri))
	if err != nil {
		return source.Snapshot{}, err
	}
	ticket, err := c.documents.BeginRead(uri)
	if err != nil {
		if snapshot, open := c.documents.Snapshot(uri); open {
			return snapshot, nil
		}
		return source.Snapshot{}, err
	}
	defer c.documents.DiscardRead(ticket)
	text, err := workspace.ReadText(ctx, path)
	if err != nil {
		return source.Snapshot{}, err
	}
	if err := ctx.Err(); err != nil {
		return source.Snapshot{}, err
	}
	snapshot, err := c.documents.PublishRead(ticket, text)
	if err != nil {
		return source.Snapshot{}, err
	}
	hash := sha256.Sum256([]byte(text))
	c.mu.Lock()
	defer c.mu.Unlock()
	file = c.files[uri]
	if file != nil {
		if root := c.roots[file.root]; root != nil {
			root.reads++
			root.bytes += uint64(len(text))
		}
		if file.hash == hash && file.revision != 0 {
			snapshot, err = source.New(uri, file.revision, text)
			if err != nil {
				return source.Snapshot{}, err
			}
		}
	}
	return snapshot, nil
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
	if root == nil || root.problem != "" || root.scanProblem != "" || !root.done || len(root.backlog) != 0 {
		return false
	}
	for _, file := range c.files {
		if file.root != root.header.Root {
			continue
		}
		if file.target.ID == "" {
			return false
		}
		if file.target.ID == target && (file.dirty || file.running || file.blocked || file.problem != "" || file.invalidated) {
			return false
		}
	}
	return true
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

func (c *catalog) document(ctx context.Context, uri string) (workspace.Document, error) {
	c.mu.Lock()
	root := c.rootFor(source.URI(uri))
	r := c.roots[root]
	if r == nil {
		c.mu.Unlock()
		return workspace.Document{}, fmt.Errorf("document is outside workspace")
	}
	header, override := r.header, r.override
	c.mu.Unlock()
	snapshot, err := c.readSnapshot(ctx, source.URI(uri))
	if err != nil {
		return workspace.Document{}, err
	}
	return workspace.OpenDocument(header, snapshot, override)
}

// closure follows known key candidates without interpreting absence as proof.
// Reverse traversal is only needed for references/edits, not ordinary caller
// diagnostics or definition lookup into a widely used template library.
func (c *catalog) closure(ctx context.Context, header workspace.Workspace, selected, focus string, reverse, all, repair bool) ([]source.URI, error) {
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
			document, err := c.document(ctx, focus)
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
				definitions, err = analysis.ResourceAlternatives(c.index, file.Scope, key)
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
	root   string
	epoch  uint64
	states map[index.Scope]uint64
	files  map[source.URI]catalogInput
}

func (c *catalog) valid(proofs []catalogProof) bool {
	c.mu.Lock()
	defer c.mu.Unlock()
	for _, proof := range proofs {
		root := c.roots[proof.root]
		if root == nil || root.epoch != proof.epoch {
			return false
		}
		for scope, generation := range proof.states {
			_, state := c.index.Definitions(scope, "")
			if !c.scopeReadyLocked(root, string(scope)) || state.Generation != generation {
				return false
			}
		}
		for uri, input := range proof.files {
			file := c.files[uri]
			if file == nil || file.stamp != input.stamp || !file.invalidated && file.hash != input.hash {
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
		if file == nil || file.invalidated || file.revision != candidate.Revision {
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
			_, state := c.index.Definitions(index.Scope(target.ID), "")
			proof.states[index.Scope(target.ID)] = state.Generation
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
	bytes := 0
	for _, uri := range uris {
		document, err := c.document(ctx, string(uri))
		if err != nil {
			if isMissing(err) {
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
		bytes += len(document.Snapshot.Text())
		if bytes > catalogTextLimit {
			return w, proof, fmt.Errorf("dependency input exceeds 128 MiB")
		}
		c.mu.Lock()
		file := c.files[uri]
		if file != nil && file.hash != sha256.Sum256([]byte(document.Snapshot.Text())) && !file.running && !file.dirty {
			c.mark(uri, shared.Related, true)
			delete(proof.states, index.Scope(document.Target.ID))
			w.IncompleteScopes[document.Target.ID] = true
		}
		if file == nil || file.problem != "" || file.invalidated {
			w.IncompleteScopes[document.Target.ID] = true
		}
		if file != nil {
			proof.files[uri] = catalogInput{file.stamp, sha256.Sum256([]byte(document.Snapshot.Text()))}
		}
		if strings.HasPrefix(document.Target.Scope, "opened:") && file != nil && file.problem == "" {
			delete(w.IncompleteScopes, document.Target.ID)
		}
		c.mu.Unlock()
		w.Documents = append(w.Documents, document)
	}
	return w, proof, nil
}

func (c *catalog) workspaceSymbols(query string) ([]map[string]any, []catalogProof) {
	c.mu.Lock()
	defer c.mu.Unlock()
	result := []map[string]any{}
	byRoot := map[string]*catalogProof{}
	uris := make([]source.URI, 0, len(c.files))
	for uri := range c.files {
		uris = append(uris, uri)
	}
	sort.Slice(uris, func(a, b int) bool { return uris[a] < uris[b] })
	for _, uri := range uris {
		file := c.files[uri]
		if file.invalidated || file.blocked || file.problem != "" {
			continue
		}
		for _, symbol := range file.symbols {
			if !strings.Contains(symbol.Name, query) {
				continue
			}
			result = append(result, map[string]any{"name": symbol.Name, "kind": symbol.Kind, "location": symbol.Location, "containerName": symbol.Container})
			proof := byRoot[file.root]
			if proof == nil {
				root := c.roots[file.root]
				if root == nil {
					continue
				}
				proof = &catalogProof{root: file.root, epoch: root.epoch, files: map[source.URI]catalogInput{}}
				byRoot[file.root] = proof
			}
			proof.files[uri] = catalogInput{file.stamp, file.hash}
			if len(result) >= 20000 {
				break
			}
		}
		if len(result) >= 20000 {
			break
		}
	}
	proofs := make([]catalogProof, 0, len(byRoot))
	for _, proof := range byRoot {
		proofs = append(proofs, *proof)
	}
	return result, proofs
}
