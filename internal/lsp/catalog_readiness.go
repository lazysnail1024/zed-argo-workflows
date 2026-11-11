package lsp

import (
	"github.com/lazysnail1024/k8s-crd-toolkit/source"
	shared "github.com/lazysnail1024/k8s-crd-toolkit/workspace"
)

// A file contributes once to its root's readiness counts. Keep that contribution
// on the existing file record so target changes and root replacement can remove
// the old contribution before adding the new one. All callers hold catalog.mu.
type catalogReadiness struct {
	root    *catalogRoot
	target  string
	unready bool
	pending bool
}

func (state catalogReadiness) add(delta int) {
	if state.root == nil {
		return
	}
	r := state.root
	r.known += delta
	if state.target == "" {
		r.unknown += delta
	}
	if state.pending {
		r.pending += delta
	}
	if state.unready {
		if r.unready == nil {
			r.unready = map[string]int{}
		}
		r.unready[state.target] += delta
		if r.unready[state.target] == 0 {
			delete(r.unready, state.target)
		}
	}
}

func (c *catalog) refreshReadiness(uri source.URI) {
	file := c.files[uri]
	if file == nil {
		return
	}
	pending := c.queue != nil && c.queue.Pending(uri)
	next := catalogReadiness{root: c.roots[file.root], target: file.target.ID,
		pending: pending, unready: pending || file.blocked || file.problem != "" || file.invalidated}
	if next == file.readiness {
		return
	}
	file.readiness.add(-1)
	next.add(1)
	file.readiness = next
}

// Retain the pending contribution until the actual worker exits. A superseding
// notification may already be waiting on this URI; Complete preserves it.
func (c *catalog) complete(job shared.WorkTicket) {
	c.mu.Lock()
	defer c.mu.Unlock()
	c.queue.Complete(job)
	c.refreshReadiness(job.URI)
}
