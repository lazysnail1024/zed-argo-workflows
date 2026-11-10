package lsp

import (
	"bufio"
	"bytes"
	"context"
	"encoding/json"
	"fmt"
	"io"
	"sort"
	"time"

	"github.com/lazysnail1024/zed-argo-workflows/internal/analysis"
	"github.com/lazysnail1024/zed-argo-workflows/internal/workspace"
)

type queuedRequest struct {
	message message
	epoch   uint64
}
type workerResult struct {
	reconciliation  bool
	inputIdentities map[workspaceKey]string
	output, log     []byte
	state           session
	err             error
	canceled        bool
	epoch           uint64
	background      bool
	uri             string
	final           bool
}

// Serve owns mutable state and all transport writes. At most one foreground
// request and one background publication worker run on immutable snapshots.
// Publication is accepted per URI, so a later timeout cannot discard progress.
func Serve(in io.Reader, out, log io.Writer) int {
	s := session{out: out, log: log, buffers: map[string]buffer{}, actions: map[string]pendingAction{}, pending: map[string]bool{}}
	publicationInputs := map[string]map[workspaceKey]string{}
	refreshInputKeys := func() {
		s.acquiredInputs = map[workspaceKey]string{}
		for uri, identities := range publicationInputs {
			root := s.root(uri)
			if _, opened := s.buffers[uri]; !opened || root == "" {
				delete(publicationInputs, uri)
				continue
			}
			for key, value := range identities {
				if key.root != root {
					delete(identities, key)
					continue
				}
				// Only the key set is used by the owner to schedule probes. The
				// comparison below retains every URI's published identity, even
				// when another URI has newer diagnostics for the same scope.
				s.acquiredInputs[key] = value
			}
		}
	}
	type input struct {
		message message
		err     error
	}
	incoming := make(chan input, 16)
	stop := make(chan struct{})
	defer close(stop)
	go func() {
		r := bufio.NewReader(in)
		for {
			m, err := read(r)
			select {
			case incoming <- input{m, err}:
			case <-stop:
				return
			}
			if err != nil {
				return
			}
		}
	}()
	finished := make(chan workerResult, 16)
	var foregroundCancel, backgroundCancel, reconciliationCancel context.CancelFunc
	reconciling := false
	var active *queuedRequest
	foregroundRunning, backgroundRunning, canceled := false, false, false
	queue := []queuedRequest{}
	due := time.Time{}
	timer := time.NewTicker(20 * time.Millisecond)
	defer timer.Stop()
	scan := time.NewTicker(30 * time.Second)
	defer scan.Stop()
	snapshot := func(ctx context.Context) session {
		clone := s
		clone.ctx = ctx
		clone.factInput = nil
		clone.factBoundary = ""
		clone.factMixed = false

		clone.acquiredInputs = map[workspaceKey]string{}
		clone.usedInputs = nil
		clone.queryKey = ""
		clone.queryResults = map[string]json.RawMessage{}
		for key, value := range s.queryResults {
			clone.queryResults[key] = value
		}
		clone.ctx = analysis.WithExternalFacts(clone.ctx, s.externalFacts)
		clone.pending = nil
		clone.workspaces = map[workspaceKey]workspace.Workspace{}
		clone.externalResults = map[string]externalResult{}
		clone.roots = append([]string(nil), s.roots...)
		clone.buffers = map[string]buffer{}
		for uri, b := range s.buffers {
			clone.buffers[uri] = b
		}
		clone.actions = map[string]pendingAction{}
		for key, a := range s.actions {
			clone.actions[key] = a
		}
		return clone
	}
	abort := func(code int, reason string) {
		if reconciling {
			reconciliationCancel()
		}
		if foregroundRunning {
			foregroundCancel()
			if active != nil && !canceled {
				_ = s.failure(active.message.ID, code, reason)
				canceled = true
			}
		}
		if backgroundRunning {
			backgroundCancel()
		}
	}
	defer func() {
		if reconciliationCancel != nil {
			reconciliationCancel()
		}
		if foregroundCancel != nil {
			foregroundCancel()
		}
		if backgroundCancel != nil {
			backgroundCancel()
		}
	}()
	launch := func(request *queuedRequest, uris []string) {
		ctx, cancel := context.WithTimeout(context.Background(), 5*time.Second)
		ctx = analysis.WithInvocation(workspace.WithSnapshotBudget(ctx, s.bufferBytes))
		state := snapshot(ctx)
		background := request == nil
		if background {
			backgroundRunning = true
			backgroundCancel = cancel
		} else {
			foregroundRunning = true
			foregroundCancel = cancel
			active = request
			canceled = false
		}
		go func() {
			var output, logs bytes.Buffer
			state.out = &output
			state.log = &logs
			send := func(result workerResult) bool {
				select {
				case finished <- result:
					return true
				case <-stop:
					return false
				}
			}
			var err error
			if request != nil {
				_, _, err = state.handle(request.message)
			} else {
				// Clear unrelated YAML before a slow Workflow can consume the
				// shared deadline. These per-URI results precede the final result,
				// so the owner removes them from pending before reporting a timeout.
				ownedURIs := []string{}
				for _, uri := range uris {
					if state.ownsPublication(uri) {
						ownedURIs = append(ownedURIs, uri)
						continue
					}
					output.Reset()
					err = state.publishEmpty(uri)
					if err != nil {
						break
					}
					if !send(workerResult{output: append([]byte(nil), output.Bytes()...), epoch: state.epoch, background: true, uri: uri}) {
						return
					}
				}
				for _, uri := range ownedURIs {
					if err != nil || ctx.Err() != nil {
						break
					}
					output.Reset()
					state.usedInputs = map[workspaceKey]bool{}
					err = state.publish(uri)
					if err != nil {
						break
					}
					if ctx.Err() == nil {
						identities := map[workspaceKey]string{}
						for key := range state.usedInputs {
							if value, ok := state.acquiredInputs[key]; ok {
								identities[key] = value
							}
						}
						if !send(workerResult{output: append([]byte(nil), output.Bytes()...), inputIdentities: identities, epoch: state.epoch, background: true, uri: uri}) {
							return
						}
					}
				}
				output.Reset()
			}
			state.externalFacts = nil
			if background {
				state.externalFacts = analysis.CaptureExternalFacts(state.context())
			}
			state.facts = nil
			if err == nil && state.factInput != nil && ctx.Err() == nil {
				state.facts = analysis.CaptureFacts(ctx, *state.factInput)
			}
			send(workerResult{output: append([]byte(nil), output.Bytes()...), log: logs.Bytes(), state: state, err: err, canceled: ctx.Err() != nil, epoch: state.epoch, background: background, final: true})
		}()
	}
	for {
		if !s.shutdown {
			if !foregroundRunning {
				for len(queue) > 0 {
					q := queue[0]
					queue = queue[1:]
					if q.epoch != s.epoch {
						_ = s.failure(q.message.ID, -32801, "input changed before request execution")
						continue
					}
					launch(&q, nil)
					break
				}
			}
			if !backgroundRunning && len(s.pending) > 0 && !due.IsZero() && !time.Now().Before(due) {
				uris := make([]string, 0, len(s.pending))
				for uri := range s.pending {
					uris = append(uris, uri)
				}
				sort.Strings(uris)
				due = time.Time{}
				launch(nil, uris)
			}
		}
		select {
		case input := <-incoming:
			if input.err != nil {
				if input.err != io.EOF {
					fmt.Fprintln(log, "LSP transport:", input.err)
				}
				abort(-32800, "transport closed")
				return 2
			}
			m := input.message
			if m.Method == "$/cancelRequest" {
				var p struct {
					ID json.RawMessage `json:"id"`
				}
				if json.Unmarshal(m.Params, &p) == nil {
					if active != nil && bytes.Equal(active.message.ID, p.ID) && !canceled {
						foregroundCancel()
						_ = s.failure(active.message.ID, -32800, "request cancelled")
						canceled = true
					}
					kept := queue[:0]
					for _, q := range queue {
						if bytes.Equal(q.message.ID, p.ID) {
							_ = s.failure(q.message.ID, -32800, "request cancelled")
						} else {
							kept = append(kept, q)
						}
					}
					queue = kept
				}
				continue
			}
			if len(m.ID) > 0 && m.Method != "initialize" && m.Method != "shutdown" {
				if s.shutdown {
					_ = s.failure(m.ID, -32600, "server is shutting down")
				} else if len(queue) >= 16 {
					_ = s.failure(m.ID, -32803, "request queue is full")
				} else {
					queue = append(queue, queuedRequest{m, s.epoch})
				}
				continue
			}
			if m.Method == "shutdown" {
				abort(-32800, "server is shutting down")
				for _, q := range queue {
					_ = s.failure(q.message.ID, -32800, "server is shutting down")
				}
				queue = nil
				s.pending = map[string]bool{}
			}
			epoch := s.epoch
			done, code, err := s.handle(m)
			if err != nil {
				fmt.Fprintln(log, "LSP:", err)
				return 2
			}
			if done {
				abort(-32800, "server exiting")
				return code
			}
			if m.Method == "textDocument/didClose" || m.Method == "workspace/didChangeWorkspaceFolders" {
				refreshInputKeys()
			}
			if epoch != s.epoch {
				abort(-32801, "input changed during request")
				due = time.Now().Add(120 * time.Millisecond)
			}
		case result := <-finished:
			if result.reconciliation {
				reconciling = false
				reconciliationCancel()
				if !s.shutdown && result.epoch == s.epoch && !result.canceled {
					changedRoots := map[string]bool{}
					for _, identities := range publicationInputs {
						for key, published := range identities {
							if current, ok := result.inputIdentities[key]; ok && current != published {
								changedRoots[key.root] = true
							}
						}
					}
					if len(changedRoots) > 0 {
						s.epoch++
						abort(-32801, "workspace inputs changed during reconciliation")
						for uri := range s.buffers {
							if changedRoots[s.root(uri)] {
								s.pending[uri] = true
							}
						}
						due = time.Now().Add(120 * time.Millisecond)
					}
				}
				continue
			}
			if len(result.log) > 0 {
				_, _ = log.Write(result.log)
			}
			if result.background && !result.final {
				if !s.shutdown && result.epoch == s.epoch {
					if _, err := out.Write(result.output); err != nil {
						return 2
					}
					// The reconciliation baseline belongs to accepted diagnostics.
					// A foreground query can observe newer disk input without having
					// published those diagnostics. Advancing the baseline there would
					// hide a missed watcher event from every subsequent probe.
					publicationInputs[result.uri] = result.inputIdentities
					refreshInputKeys()
					delete(s.pending, result.uri)
				}
				continue
			}
			if result.background {
				backgroundCancel()
				backgroundRunning = false
			} else {
				foregroundCancel()
				foregroundRunning = false
			}
			current := result.epoch == s.epoch && !s.shutdown
			if current && !result.canceled && (!canceled || result.background) {
				if result.err != nil {
					fmt.Fprintln(log, "LSP worker:", result.err)
					return 2
				}
				if !result.background {
					if _, err := out.Write(result.output); err != nil {
						return 2
					}
					s.actions = result.state.actions
					s.actionSequence = result.state.actionSequence
					s.queryResults = result.state.queryResults
					s.queryBytes = result.state.queryBytes
				}
				if result.state.externalFacts != nil {
					s.externalFacts = result.state.externalFacts
				}
				if result.state.facts != nil {
					s.facts = result.state.facts
				}
			} else if !result.background && active != nil && !canceled {
				_ = s.failure(active.message.ID, -32800, "request timed out or cancelled")
			}
			if result.background && current && result.canceled {
				// A stable input that exhausts its budget terminates visibly. New input,
				// an explicit save or the next reconciliation may retry; no hot retry loop.
				for uri := range s.pending {
					params := map[string]any{"uri": uri, "diagnostics": []map[string]any{analysisStatus("WF_ANALYSIS_INCOMPLETE", "analysis deadline exceeded")}}
					if b, ok := s.buffers[uri]; ok {
						params["version"] = b.version
					}
					if err := s.write(map[string]any{"jsonrpc": "2.0", "method": "textDocument/publishDiagnostics", "params": params}); err != nil {
						return 2
					}
					delete(s.pending, uri)
				}
			}
			if !result.background {
				active = nil
			}
			if !s.shutdown && len(s.pending) > 0 && due.IsZero() {
				due = time.Now().Add(120 * time.Millisecond)
			}
		case <-scan.C:
			if !s.shutdown && !reconciling && len(s.acquiredInputs) > 0 {
				// Reconciliation is one bounded I/O probe. It does not run analysis or
				// invalidate a revision until freshly acquired input actually differs.
				ctx, cancel := context.WithTimeout(context.Background(), 5*time.Second)
				reconciliationCancel = cancel
				reconciling = true
				state := snapshot(workspace.WithSnapshotBudget(ctx, s.bufferBytes))
				keys := []workspaceKey{}
				for key := range s.acquiredInputs {
					for _, root := range s.roots {
						if root == key.root {
							keys = append(keys, key)
							break
						}
					}
				}
				go func() {
					for _, key := range keys {
						if ctx.Err() != nil {
							break
						}
						_, _ = state.workspace(key.root, key.scope)
					}
					result := workerResult{reconciliation: true, epoch: state.epoch, inputIdentities: state.acquiredInputs, canceled: ctx.Err() != nil}
					select {
					case finished <- result:
					case <-stop:
					}
				}()
			}
		case <-timer.C:
		}
	}
}
