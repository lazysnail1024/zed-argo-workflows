package lsp

import (
	"bufio"
	"bytes"
	"context"
	"encoding/json"
	"fmt"
	"github.com/lazysnail1024/zed-argo-workflows/internal/analysis"
	"github.com/lazysnail1024/zed-argo-workflows/internal/workspace"
	"io"
	"time"
)

type queuedRequest struct {
	message message
	epoch   uint64
}
type workerResult struct {
	output, log []byte
	state       session
	err         error
	canceled    bool
}

// Serve owns mutable state and transport writes. One bounded worker receives
// immutable session snapshots; only the owner may accept its epoch-bound output.
func Serve(in io.Reader, out, log io.Writer) int {
	s := session{out: out, log: log, buffers: map[string]buffer{}, actions: map[string]pendingAction{}, pending: map[string]bool{}}
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
	finished := make(chan workerResult, 1)
	var cancel context.CancelFunc
	var active *queuedRequest
	activeEpoch := uint64(0)
	var publishing []string
	running, canceled := false, false
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
		if clone.factsEpoch != clone.epoch {
			clone.facts = nil
		}
		clone.pending = nil
		clone.workspaces = map[workspaceKey]workspace.Workspace{}
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
		if running {
			cancel()
			if active != nil && !canceled {
				_ = s.failure(active.message.ID, code, reason)
				canceled = true
			}
		}
	}
	defer func() {
		if cancel != nil {
			cancel()
		}
	}()
	for {
		// Queries bypass diagnostic debounce. Queue size and worker count are fixed.
		if !running && !s.shutdown {
			var request *queuedRequest
			for len(queue) > 0 && request == nil {
				q := queue[0]
				queue = queue[1:]
				if q.epoch != s.epoch {
					_ = s.failure(q.message.ID, -32801, "input changed before request execution")
				} else {
					request = &q
				}
			}
			publish := request == nil && len(s.pending) > 0 && !due.IsZero() && !time.Now().Before(due)
			if request != nil || publish {
				ctx, c := context.WithTimeout(context.Background(), 5*time.Second)
				cancel = c
				ctx = workspace.WithSnapshotBudget(ctx, s.bufferBytes)
				ctx = analysis.WithInvocation(ctx)
				clone := snapshot(ctx)
				uris := []string{}
				if publish {
					for uri := range s.pending {
						uris = append(uris, uri)
					}
					due = time.Time{}
				}
				publishing = uris
				active = request
				activeEpoch = s.epoch
				running = true
				canceled = false
				go func(state session, q *queuedRequest, uris []string) {
					var output, logs bytes.Buffer
					state.out = &output
					state.log = &logs
					var err error
					if q != nil {
						_, _, err = state.handle(q.message)
					} else {
						for _, uri := range uris {
							if state.context().Err() != nil {
								break
							}
							if err = state.publish(uri); err != nil {
								break
							}
						}
					}
					state.facts = nil
					if err == nil && state.factInput != nil && state.context().Err() == nil {
						state.facts = analysis.CaptureFacts(state.context(), *state.factInput)
					}
					finished <- workerResult{output.Bytes(), logs.Bytes(), state, err, state.context().Err() != nil}
				}(clone, request, uris)
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
					if active != nil && bytes.Equal(active.message.ID, p.ID) {
						abort(-32800, "request cancelled")
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
				if running {
					select {
					case <-finished:
					case <-time.After(5 * time.Second):
						return 2
					}
				}
				return code
			}
			if s.epoch != epoch {
				s.facts = nil
				abort(-32801, "input changed during request")
				due = time.Now().Add(120 * time.Millisecond)
			}
		case result := <-finished:
			cancel()
			running = false
			if !canceled && activeEpoch == s.epoch && !result.canceled {
				if result.err != nil {
					fmt.Fprintln(log, "LSP worker:", result.err)
					return 2
				}
				if _, err := out.Write(result.output); err != nil {
					return 2
				}
				// Publication work remains pending until this exact snapshot is accepted.
				for _, uri := range publishing {
					delete(s.pending, uri)
				}
				s.actions = result.state.actions
				s.actionSequence = result.state.actionSequence
				s.facts = result.state.facts
				s.factsEpoch = s.epoch
			} else if active != nil && !canceled {
				_ = s.failure(active.message.ID, -32800, "request timed out or cancelled")
			}
			if len(result.log) > 0 {
				_, _ = log.Write(result.log)
			}
			active = nil
			publishing = nil
			if !s.shutdown && len(s.pending) > 0 && due.IsZero() {
				due = time.Now().Add(120 * time.Millisecond)
			}
		case <-scan.C:
			if !s.shutdown {
				s.epoch++
				s.facts = nil
				abort(-32801, "workspace reconciliation")
				for uri := range s.buffers {
					s.pending[uri] = true
				}
				due = time.Now().Add(120 * time.Millisecond)
			}
		case <-timer.C:
		}
	}
}
