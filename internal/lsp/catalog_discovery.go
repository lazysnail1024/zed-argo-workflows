package lsp

import (
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"time"

	"github.com/lazysnail1024/k8s-crd-toolkit/source"
	shared "github.com/lazysnail1024/k8s-crd-toolkit/workspace"
	"github.com/lazysnail1024/zed-argo-workflows/internal/fileuri"
	"github.com/lazysnail1024/zed-argo-workflows/internal/workspace"
)

func (c *catalog) discover() {
	timer := time.NewTicker(10 * time.Millisecond)
	defer timer.Stop()
	active := map[*catalogRoot]bool{}
	defer func() {
		for root := range active {
			if root.scanner != nil {
				_ = root.scanner.Close()
			}
		}
	}()
	for {
		select {
		case <-c.ctx.Done():
			return
		case <-timer.C:
		}
		c.mu.Lock()
		current := map[*catalogRoot]bool{}
		for _, root := range c.roots {
			current[root] = true
		}
		c.mu.Unlock()
		for root := range active {
			if !current[root] && root.scanner != nil {
				_ = root.scanner.Close()
			}
		}
		active = current
		for root := range current {
			c.discoverRoot(root)
			c.reportProgress(root)
		}
	}
}

func (c *catalog) reportProgress(root *catalogRoot) {
	c.mu.Lock()
	defer c.mu.Unlock()
	if c.roots[root.header.Root] != root || root.reported || time.Since(root.reportedAt) < time.Second {
		return
	}
	state := "Indexing"
	if !root.done {
		state = "Discovering"
	}
	if root.problem != "" {
		state = "Failed"
	}
	data, _ := json.Marshal(map[string]any{"phase": "index", "state": state, "root": root.header.Root, "pass": root.pass, "knownFiles": root.known, "queue": c.queue.Len(), "reads": root.reads, "readBytes": root.bytes, "parses": root.parses, "problem": root.problem})
	c.logs[root.header.Root], root.reportedAt = string(data), time.Now()
	if state == "Failed" {
		root.lastState, root.reported = state, true
	}
	c.signal()
}

func (c *catalog) refreshHeader(root *catalogRoot) {
	c.mu.Lock()
	if root.unavailable != "" || time.Since(root.checked) < 30*time.Second {
		c.mu.Unlock()
		return
	}
	root.checked = time.Now()
	name, override, overlay, epoch := root.header.Root, root.override, root.overlay, root.epoch
	c.mu.Unlock()
	header, err := workspace.Describe(c.ctx, name, override, overlay)
	c.mu.Lock()
	defer c.mu.Unlock()
	if c.roots[name] != root || root.epoch != epoch {
		return
	}
	if err != nil {
		if root.problem != err.Error() {
			root.epoch++
		}
		root.problem = err.Error()
		root.complete = false
		root.reported = false
		for uri, file := range c.files {
			if file.root == name {
				c.mark(uri, shared.Background, true)
				if file.opened {
					c.publications[string(uri)] = true
				}
			}
		}
		c.signal()
		return
	}
	identity := catalogIdentity(header, override)
	if root.identity == identity && root.problem == "" {
		return
	}
	root.header, root.identity, root.problem = header, identity, ""
	root.epoch++
	root.done, root.complete, root.scanProblem = false, false, ""
	root.reported = false
	for uri, file := range c.files {
		if file.root == name {
			file.target = workspace.Target{}
			c.mark(uri, shared.Background, true)
		}
	}
}

func (c *catalog) discoverRoot(root *catalogRoot) {
	c.refreshHeader(root)
	c.mu.Lock()
	if root.problem != "" {
		c.mu.Unlock()
		return
	}
	epoch, name := root.epoch, root.header.Root
	restart := root.scanner == nil || root.scanEpoch != epoch || root.done && !root.nextPass.IsZero() && !time.Now().Before(root.nextPass)
	priority := root.priority
	root.priority = map[string]bool{}
	c.mu.Unlock()
	if restart {
		if root.scanner != nil {
			_ = root.scanner.Close()
		}
		scanner, err := shared.NewScanner([]string{name}, shared.ScanOptions{Extensions: []string{".yaml", ".yml"}, ExcludeDirs: []string{".git", "node_modules", "vendor", ".argo-ls"}, MaxDirectories: 100000})
		c.mu.Lock()
		root.scanner, root.scanEpoch, root.done, root.nextPass = scanner, epoch, false, time.Time{}
		root.reported = false
		root.complete = false
		root.pass++
		root.scanPass = root.membership.Start()
		root.scanProblem = ""
		if err != nil {
			root.scanProblem = err.Error()
			root.done = true
		}
		c.mu.Unlock()
	}
	if root.scanner == nil {
		return
	}
	for path := range priority {
		_ = root.scanner.Prioritize(path)
	}
	c.mu.Lock()
	if c.roots[name] != root || root.epoch != epoch || root.done || c.queue.Len() > 448 {
		c.finishPass(root)
		c.mu.Unlock()
		return
	}
	c.mu.Unlock()
	ctx, cancel := context.WithTimeout(c.ctx, 50*time.Millisecond)
	batch, err := root.scanner.Next(ctx, 64)
	cancel()
	c.mu.Lock()
	if root.epoch != epoch || c.roots[name] != root {
		c.mu.Unlock()
		return
	}
	for _, issue := range batch.Issues {
		root.scanProblem = fmt.Sprintf("%s: %v", issue.Path, issue.Err)
	}
	if err != nil && !errors.Is(err, context.DeadlineExceeded) && !errors.Is(err, context.Canceled) {
		root.scanProblem = err.Error()
	}
	if batch.Done && !batch.Complete && root.scanProblem == "" {
		root.scanProblem = "workspace discovery is incomplete"
	}
	c.mu.Unlock()
	c.admitDiscovery(root, epoch, batch.Files)
	c.mu.Lock()
	if c.roots[name] == root && root.epoch == epoch {
		// Scanner completion is visible only after every member of this batch
		// has been accepted or recorded as an explicit discovery failure.
		root.done = batch.Done
		c.finishPass(root)
	}
	c.mu.Unlock()
}

func (c *catalog) admitDiscovery(root *catalogRoot, epoch uint64, paths []string) {
	for _, path := range paths {
		c.mu.Lock()
		if c.roots[root.header.Root] != root || root.epoch != epoch {
			c.mu.Unlock()
			return
		}
		header, roots := root.header, c.rootNames()
		c.mu.Unlock()
		ok, err := workspace.Candidate(header, path, roots)
		address, uriErr := fileuri.FromPath(path)
		c.mu.Lock()
		if root.epoch != epoch || c.roots[header.Root] != root {
			c.mu.Unlock()
			return
		}
		if err != nil {
			root.scanProblem = err.Error()
		}
		if uriErr != nil {
			root.scanProblem = uriErr.Error()
		}
		if !ok || err != nil || uriErr != nil {
			c.mu.Unlock()
			continue
		}
		uri := source.URI(address)
		file := c.files[uri]
		if file == nil {
			if len(c.files) >= 100000 {
				root.scanProblem = "file index exceeds 100000 entries"
				c.mu.Unlock()
				continue
			}
			file = &catalogFile{root: header.Root}
			c.addFile(uri, file)
			root.complete = false
		}
		observed := root.membership.Seen(file.seen)
		file.seen = root.membership.Observe()
		if !file.opened && !observed && !c.queue.Pending(uri) {
			c.mark(uri, shared.Background, false)
		}
		c.mu.Unlock()
	}
}

// A pass finishes only after its bounded batches and file jobs finish. Partial
// enumeration and per-file failures never establish absence or uniqueness.
func (c *catalog) finishPass(root *catalogRoot) {
	if c.roots[root.header.Root] != root || root.scanEpoch != root.epoch || !root.done || root.reported || root.pending != 0 {
		return
	}
	root.membership.Finish(root.scanPass, root.problem == "" && root.scanProblem == "")
	failed, known := root.problem != "" || root.scanProblem != "", 0
	globalFailure := failed
	incomplete := map[string]bool{}
	for uri, file := range c.files {
		if file.root != root.header.Root {
			continue
		}
		if !file.opened && root.scanPass.Missing(file.seen) {
			c.remove(uri)
			continue
		}
		if c.queue.Pending(uri) {
			return
		}
		known++
		failed = failed || file.problem != "" || file.blocked || file.invalidated || file.target.ID == ""
		if file.target.ID == "" {
			globalFailure = true
		}
		if file.problem != "" || file.blocked || file.invalidated {
			incomplete[file.target.ID] = true
		}
	}
	root.complete = !failed
	root.reported = true
	if root.nextPass.IsZero() {
		root.nextPass = time.Now().Add(30 * time.Second)
	}
	state := "Ready"
	if failed {
		state = "Limited"
	}
	data, _ := json.Marshal(map[string]any{"phase": "index", "state": state, "root": root.header.Root, "generation": root.epoch, "pass": root.pass, "files": known, "queue": c.queue.Len(), "reads": root.reads, "readBytes": root.bytes, "parses": root.parses, "retainedTextBytes": c.documents.OpenBytes(), "symbolBytes": c.symbolBytes})
	c.logs[root.header.Root] = string(data)
	root.reportedAt = time.Now()
	for uri, file := range c.files {
		if file.root != root.header.Root || !file.opened {
			continue
		}
		if state != root.lastState || file.awaiting && !globalFailure && !incomplete[file.target.ID] {
			c.publications[string(uri)] = true
		}
	}
	root.lastState = state
	c.signal()
}
