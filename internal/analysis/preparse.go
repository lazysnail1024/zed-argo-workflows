package analysis

import (
	"context"
	"runtime"
	"sync"

	"github.com/lazysnail1024/k8s-crd-toolkit/source"
	"github.com/lazysnail1024/k8s-crd-toolkit/yamlview"
	"github.com/lazysnail1024/zed-argo-workflows/internal/workspace"
)

// PreparseBatch warms only immutable CST views for a small, ordered batch.
// The caller still evaluates every document sequentially in its original
// invocation: projections, target selection and expansion admission are not
// worker work. Large documents and cache misses outside the batch retain the
// ordinary parseView path.
func PreparseBatch(ctx context.Context, documents []workspace.Document) int {
	if len(documents) == 0 {
		return 0
	}
	state, _ := ctx.Value(invocationKey{}).(*invocation)
	workers := min(4, runtime.GOMAXPROCS(0), len(documents))
	if state == nil || workers < 2 || ctx.Err() != nil {
		return 1
	}

	type pendingView struct {
		snapshot source.Snapshot
		key      snapshotIdentity
		view     yamlview.View
		err      error
	}
	const batchBytes = 64 * 1024
	pending := make([]pendingView, 0, workers)
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
		pending = append(pending, pendingView{snapshot: snapshot, key: key})
		plannedBytes += len(text)
		count++
	}
	if len(pending) < 2 {
		return max(1, count)
	}

	var workersDone sync.WaitGroup
	workersDone.Add(len(pending))
	for i := range pending {
		go func(i int) {
			defer workersDone.Done()
			// yamlview owns and closes its native parser/tree. No invocation
			// maps, projections or global expansion budget are read or changed.
			pending[i].view, pending[i].err = yamlview.Parse(ctx, pending[i].snapshot)
		}(i)
	}
	// Native parsing checks cancellation in bounded slices. Always join the
	// current batch, including on cancellation, before returning to the caller.
	workersDone.Wait()
	for _, parsed := range pending {
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
	return count
}
