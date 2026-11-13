package analysis

import (
	"container/list"
	"context"
	"sync"

	"github.com/lazysnail1024/k8s-crd-toolkit/source"
	"github.com/lazysnail1024/k8s-crd-toolkit/yamlview"
	"github.com/lazysnail1024/zed-argo-workflows/internal/workspace"
)

type storedDocumentFacts struct {
	identity    snapshotIdentity
	view        yamlview.View
	projections map[projectionIdentity]projectedDocument
	cost        factBudget
}

// FactStore is the single optional parse/projection handoff shared by catalog,
// publication and request workers. Its aggregate allowance is the same 8 MiB /
// 512000-node allowance as a Facts batch, never an allowance per file or scope.
// Completed workers publish detached values after their owner validates input
// proofs. Fresh snapshot acquisition still precedes every Restore call.
type FactStore struct {
	mu      sync.Mutex
	entries map[source.URI]*list.Element
	order   list.List
	cost    factBudget
}

// Store does no analysis or tree walking. CaptureFacts already measured every
// document outside the owner's lock. A newer identity replaces the old URI;
// least-recently-used entries are discarded before admitting excess retention.
func (s *FactStore) Store(facts *Facts) {
	if facts == nil {
		return
	}
	documents := make(map[snapshotIdentity]*storedDocumentFacts, len(facts.views))
	for key, view := range facts.views {
		cost := facts.costs[key]
		if cost.nodes == 0 || cost.text > maxFactText || cost.nodes > maxFactNodes {
			continue
		}
		documents[key] = &storedDocumentFacts{key, view, map[projectionIdentity]projectedDocument{}, cost}
	}
	for key, value := range facts.projections {
		if document := documents[key.snapshotIdentity]; document != nil {
			// A fresh projection may use a reread's equal string while its
			// restored CST still owns the canonical source bytes. Retain only
			// the canonical backing storage charged by this document's cost.
			key.snapshotIdentity = document.identity
			document.projections[key] = value
		}
	}
	s.mu.Lock()
	defer s.mu.Unlock()
	if s.entries == nil {
		s.entries = map[source.URI]*list.Element{}
	}
	for key, document := range documents {
		s.remove(key.uri)
		for document.cost.text > maxFactText-s.cost.text || document.cost.nodes > maxFactNodes-s.cost.nodes {
			oldest := s.order.Front()
			if oldest == nil {
				break
			}
			s.remove(oldest.Value.(*storedDocumentFacts).identity.uri)
		}
		s.entries[key.uri] = s.order.PushBack(document)
		s.cost.text += document.cost.text
		s.cost.nodes += document.cost.nodes
	}
}

// Restore copies only matching immutable facts into this request's invocation.
// Revision, complete bytes and release are checked after reading current input.
// No store membership can supply a workspace-completeness or input-proof claim.
func (s *FactStore) Restore(ctx context.Context, w workspace.Workspace) {
	if ctx.Err() != nil {
		return
	}
	facts := &Facts{boundary: FactBoundary(w), views: map[snapshotIdentity]yamlview.View{}, projections: map[projectionIdentity]projectedDocument{}, costs: map[snapshotIdentity]factBudget{}}
	s.mu.Lock()
	for _, document := range w.Documents {
		entry := s.entries[document.Snapshot.URI()]
		if entry == nil {
			continue
		}
		stored := entry.Value.(*storedDocumentFacts)
		key := snapshotKey(document.Snapshot)
		if stored.identity != key {
			continue
		}
		// Preserve the original text backing storage after comparison. The
		// newly read snapshot is borrowed input, not another retained copy.
		key = stored.identity
		facts.views[key] = stored.view
		facts.costs[key] = stored.cost
		for projection, value := range stored.projections {
			if projection.version == document.Target.Version {
				facts.projections[projection] = value
			}
		}
		s.order.MoveToBack(entry)
	}
	s.mu.Unlock()
	RestoreFacts(ctx, facts, w)
}

func (s *FactStore) remove(uri source.URI) {
	if entry := s.entries[uri]; entry != nil {
		stored := entry.Value.(*storedDocumentFacts)
		s.cost.text -= stored.cost.text
		s.cost.nodes -= stored.cost.nodes
		delete(s.entries, uri)
		s.order.Remove(entry)
	}
}

// Delete releases an observed stale or removed URI without changing analysis.
func (s *FactStore) Delete(uri source.URI) {
	s.mu.Lock()
	defer s.mu.Unlock()
	s.remove(uri)
}

func (s *FactStore) Clear() {
	s.mu.Lock()
	defer s.mu.Unlock()
	s.entries = nil
	s.order.Init()
	s.cost = factBudget{}
}
