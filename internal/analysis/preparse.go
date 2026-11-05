package analysis

import (
	"context"
	"runtime"
	"sync"

	"github.com/lazysnail1024/k8s-crd-toolkit/source"
	"github.com/lazysnail1024/k8s-crd-toolkit/yamlview"
	"github.com/lazysnail1024/zed-argo-workflows/internal/workspace"
)

// VisitPreparsedDocuments overlaps one bounded batch of immutable CST parsing
// with sequential validation of the current batch. Only the owner plans work,
// admits views and invokes visit; workers never access invocation maps or the
// expansion budget. At most three parser workers leave one CPU for validation.
func VisitPreparsedDocuments(ctx context.Context, documents []workspace.Document, visit func(workspace.Document)) {
	workers := min(3, runtime.GOMAXPROCS(0)-1)
	if workers < 1 {
		for _, document := range documents {
			if ctx.Err() != nil {
				return
			}
			visit(document)
		}
		return
	}
	if len(documents) == 0 {
		return
	}
	var pending *viewBatch
	defer func() {
		// A cancellation or visitor panic must not leave native parsers alive.
		if pending != nil {
			pending.done.Wait()
		}
	}()
	pending = planViews(ctx, documents, workers)
	pending.start(ctx)
	for first := 0; first < len(documents); {
		current := pending
		current.admit(ctx)
		pending = nil
		if ctx.Err() != nil {
			return
		}
		next := first + current.count
		if next < len(documents) {
			pending = planViews(ctx, documents[next:], workers)
			pending.start(ctx)
		}
		for _, document := range documents[first:next] {
			if ctx.Err() != nil {
				return
			}
			visit(document)
		}
		first = next
	}
}

type pendingView struct {
	snapshot source.Snapshot
	key      snapshotIdentity
	view     yamlview.View
	err      error
}

type viewBatch struct {
	count   int
	pending []pendingView
	done    sync.WaitGroup
}

// planViews runs on the owner before starting workers. The next batch retains
// at most 64 KiB of input, and admission rechecks the original 50 MiB cache cap.
func planViews(ctx context.Context, documents []workspace.Document, workers int) *viewBatch {
	batch := &viewBatch{count: 1}
	state, _ := ctx.Value(invocationKey{}).(*invocation)
	if state == nil || ctx.Err() != nil {
		return batch
	}
	workers = min(workers, len(documents))
	const batchBytes = 64 * 1024
	batch.pending = make([]pendingView, 0, workers)
	count, plannedBytes := 0, 0
	for i := 0; i < workers; i++ {
		snapshot := documents[i].Snapshot
		text := snapshot.Text()
		if len(text) > batchBytes {
			break
		}
		key := snapshotKey(snapshot)
		if _, ok := state.views[key]; ok {
			count++
			continue
		}
		if _, ok := state.sharedViews[text]; ok {
			count++
			continue
		}
		// Admission is conservative here; identical texts within the batch
		// will be charged only once when their results are admitted below.
		if plannedBytes+len(text) > batchBytes || state.bytes+plannedBytes+len(text) > 50*1024*1024 {
			break
		}
		batch.pending = append(batch.pending, pendingView{snapshot: snapshot, key: key})
		plannedBytes += len(text)
		count++
	}
	batch.count = max(1, count)
	return batch
}

func (batch *viewBatch) start(ctx context.Context) {
	batch.done.Add(len(batch.pending))
	for i := range batch.pending {
		go func(i int) {
			defer batch.done.Done()
			// yamlview owns and closes its native parser/tree. No invocation
			// maps, projections or global expansion budget are read or changed.
			batch.pending[i].view, batch.pending[i].err = yamlview.Parse(ctx, batch.pending[i].snapshot)
		}(i)
	}
}

func (batch *viewBatch) admit(ctx context.Context) {
	// Native parsing checks cancellation in bounded slices. Always join the
	// current batch, including on cancellation, before returning to the caller.
	batch.done.Wait()
	state, _ := ctx.Value(invocationKey{}).(*invocation)
	if state == nil {
		return
	}
	for _, parsed := range batch.pending {
		if ctx.Err() != nil {
			break
		}
		if parsed.err != nil {
			continue // The original analysis path reports errors; none are cached.
		}
		if _, ok := state.views[parsed.key]; ok {
			continue
		}
		text := parsed.snapshot.Text()
		if _, ok := state.sharedViews[text]; ok {
			continue // Do not retain duplicate backing strings via per-URI keys.
		}
		if state.bytes+len(text) <= 50*1024*1024 {
			state.views[parsed.key] = parsed.view
			state.sharedViews[text] = parsed.view
			state.bytes += len(text)
		}
	}
}
