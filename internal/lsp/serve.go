package lsp

import (
	"bufio"
	"bytes"
	"context"
	"encoding/json"
	"fmt"
	"io"
	"runtime/debug"
	"sort"
	"time"

	"github.com/lazysnail1024/zed-argo-workflows/internal/analysis"
	"github.com/lazysnail1024/zed-argo-workflows/internal/workspace"
)

type queuedRequest struct {
	message message
	epoch   uint64
	input   requestInput
}
type workerResult struct {
	output, log []byte
	state       session
	err         error
	canceled    bool
	epoch       uint64
	background  bool
	uri         string
	foreign     bool
	final       bool
	proofs      []catalogProof
	facts       *analysis.Facts
	local       bool
	lane        int
}

// Serve owns mutable state and all transport writes. One local query, one heavy
// request and one background publication worker run on immutable snapshots.
// Publication is accepted per URI, so a later timeout cannot discard progress.
func Serve(in io.Reader, out, log io.Writer, render ...RenderCommand) int {
	// Leave room for native YAML parsing, embedded mappings and the client.
	// This controls Go's heap headroom, not RSS or the explicit input limits.
	previousLimit := debug.SetMemoryLimit(-1)
	if previousLimit > 288*1024*1024 {
		debug.SetMemoryLimit(288 * 1024 * 1024)
		defer debug.SetMemoryLimit(previousLimit)
	}
	s := session{out: out, log: log, buffers: map[string]buffer{}, actions: map[string]pendingAction{}, pending: map[string]bool{}}
	if len(render) > 0 {
		s.render = render[0]
	}
	var err error
	s.catalog, err = newCatalog()
	if err != nil {
		fmt.Fprintln(log, "LSP catalog:", err)
		return 2
	}
	defer s.catalog.cancel()
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
	var backgroundCancel context.CancelFunc
	backgroundRunning := false
	// An unchanged foreign buffer has already been cleared. Catalog index
	// progress must not requeue it behind a slow Workflow publication.
	clearedForeign := map[string]bool{}
	lanes := [2]requestLane{}
	due := time.Time{}
	timer := time.NewTicker(20 * time.Millisecond)
	defer timer.Stop()
	snapshot := func(ctx context.Context) session {
		clone := s
		clone.ctx = ctx
		clone.factInput = nil
		clone.factBoundary = ""
		clone.factMixed = false
		clone.proofs = nil

		clone.queryKey = ""
		clone.queryResults = map[string]json.RawMessage{}
		for key, value := range s.queryResults {
			clone.queryResults[key] = value
		}
		clone.ctx = analysis.WithExternalFacts(clone.ctx, s.externalFacts)
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
	abort := func(code int, reason string, all bool) {
		for i := range lanes {
			lane := &lanes[i]
			if lane.running && (all || !s.requestCurrent(*lane.active)) {
				lane.cancel()
				if !lane.canceled {
					_ = s.failure(lane.active.message.ID, code, reason)
					lane.canceled = true
				}
			}
		}
		if backgroundRunning {
			backgroundCancel()
		}
	}
	defer func() {
		for i := range lanes {
			if lanes[i].cancel != nil {
				lanes[i].cancel()
			}
		}
		if backgroundCancel != nil {
			backgroundCancel()
		}
	}()
	launch := func(request *queuedRequest, uris []string) {
		duration := 5 * time.Second
		if request != nil && request.message.Method == "workspace/executeCommand" {
			duration = 30 * time.Second
		}
		ctx, cancel := context.WithTimeout(context.Background(), duration)
		ctx = analysis.WithInvocation(workspace.WithSnapshotBudget(ctx, s.catalog.documents.OpenBytes()))
		state := snapshot(ctx)
		background := request == nil
		laneID := -1
		if background {
			backgroundRunning = true
			backgroundCancel = cancel
		} else {
			laneID = laneFor(request.message.Method)
			lane := &lanes[laneID]
			lane.running, lane.canceled = true, false
			lane.cancel, lane.active = cancel, request
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
			var cacheProofs []catalogProof
			if request != nil {
				state.method = request.message.Method
				state.focus = request.input.uri
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
					if !send(workerResult{output: append([]byte(nil), output.Bytes()...), epoch: state.epoch, background: true, uri: uri, foreign: true}) {
						return
					}
				}
				for _, uri := range ownedURIs {
					if err != nil || ctx.Err() != nil {
						break
					}
					output.Reset()
					state.focus, state.method = uri, ""
					state.workspaces = map[workspaceKey]workspace.Workspace{}
					state.proofs = nil
					state.localPublication = false
					err = state.publish(uri)
					if err != nil {
						break
					}
					if ctx.Err() == nil {
						cacheProofs = append(cacheProofs, state.proofs...)
						if !send(workerResult{output: append([]byte(nil), output.Bytes()...), epoch: state.epoch, background: true, uri: uri, proofs: state.proofs, local: state.localPublication}) {
							return
						}
					}
				}
				output.Reset()
			}
			if background {
				state.proofs = cacheProofs
			}
			state.externalFacts = nil
			if background {
				state.externalFacts = analysis.CaptureExternalFacts(state.context())
			}
			var facts *analysis.Facts
			if err == nil && state.factInput != nil && ctx.Err() == nil {
				facts = analysis.CaptureFacts(ctx, *state.factInput)
			}
			// The owner accepts detached caches and actions only. Do not carry
			// worker parsing contexts or borrowed input through the result queue.
			state.ctx, state.factInput, state.workspaces, state.buffers = nil, nil, nil, nil
			send(workerResult{output: append([]byte(nil), output.Bytes()...), log: logs.Bytes(), state: state, facts: facts, err: err, canceled: ctx.Err() != nil, epoch: state.epoch, background: background, final: true, proofs: state.proofs, lane: laneID})
		}()
	}
	for {
		if !s.shutdown {
			for i := range lanes {
				lane := &lanes[i]
				for !lane.running && len(lane.queue) > 0 {
					q := lane.queue[0]
					lane.queue = lane.queue[1:]
					if !s.requestCurrent(q) {
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
				abort(-32800, "transport closed", true)
				return 2
			}
			m := input.message
			if m.Method == "$/cancelRequest" {
				var p struct {
					ID json.RawMessage `json:"id"`
				}
				if json.Unmarshal(m.Params, &p) == nil {
					for i := range lanes {
						lane := &lanes[i]
						if lane.active != nil && bytes.Equal(lane.active.message.ID, p.ID) && !lane.canceled {
							lane.cancel()
							_ = s.failure(lane.active.message.ID, -32800, "request cancelled")
							lane.canceled = true
						}
						kept := lane.queue[:0]
						for _, q := range lane.queue {
							if bytes.Equal(q.message.ID, p.ID) {
								_ = s.failure(q.message.ID, -32800, "request cancelled")
							} else {
								kept = append(kept, q)
							}
						}
						lane.queue = kept
					}
				}
				continue
			}
			if m.Method == "" {
				continue
			} // Responses to server-initiated showDocument requests.
			if len(m.ID) > 0 && m.Method != "initialize" && m.Method != "shutdown" {
				lane := &lanes[laneFor(m.Method)]
				if s.shutdown {
					_ = s.failure(m.ID, -32600, "server is shutting down")
				} else if len(lane.queue) >= 8 {
					_ = s.failure(m.ID, -32803, "request queue is full")
				} else {
					lane.queue = append(lane.queue, s.queueRequest(m))
				}
				continue
			}
			if m.Method == "shutdown" {
				abort(-32800, "server is shutting down", true)
				for i := range lanes {
					for _, q := range lanes[i].queue {
						_ = s.failure(q.message.ID, -32800, "server is shutting down")
					}
					lanes[i].queue = nil
				}
				s.pending = map[string]bool{}
			}
			epoch := s.epoch
			done, code, err := s.accept(m)
			if err != nil {
				fmt.Fprintln(log, "LSP:", err)
				return 2
			}
			if done {
				abort(-32800, "server exiting", true)
				return code
			}
			if epoch != s.epoch {
				clear(clearedForeign)
				abort(-32801, "input changed during request", false)
				due = time.Now().Add(120 * time.Millisecond)
			}
		case result := <-finished:
			if len(result.log) > 0 {
				_, _ = log.Write(result.log)
			}
			if result.background && !result.final {
				if !s.shutdown && result.epoch == s.epoch && s.catalog.valid(result.proofs) {
					if _, err := out.Write(result.output); err != nil {
						return 2
					}
					// Catalog observations schedule publications independently of query
					// caches. Only an accepted result clears this URI's pending work.
					if !s.catalog.acceptedPublication(result.uri, result.local) {
						delete(s.pending, result.uri)
					}
					if result.foreign {
						clearedForeign[result.uri] = true
					}
				}
				continue
			}
			var lane *requestLane
			if result.background {
				backgroundCancel()
				backgroundRunning = false
			} else {
				lane = &lanes[result.lane]
				lane.cancel()
				lane.running = false
			}
			sameEpoch := result.epoch == s.epoch
			current := sameEpoch && !s.shutdown
			if lane != nil {
				current = s.requestCurrent(*lane.active)
			}
			valid := s.catalog.valid(result.proofs)
			if current && valid && !result.canceled && (lane == nil || !lane.canceled) {
				if result.err != nil {
					fmt.Fprintln(log, "LSP worker:", result.err)
					return 2
				}
				if !result.background {
					if _, err := out.Write(result.output); err != nil {
						return 2
					}
					if editMethod(lane.active.message.Method) {
						s.actions = result.state.actions
						s.actionSequence = result.state.actionSequence
					}
					if sameEpoch {
						s.queryResults = result.state.queryResults
						s.queryBytes = result.state.queryBytes
					}
				}
				if sameEpoch && result.state.externalFacts != nil {
					s.externalFacts = result.state.externalFacts
				}
				if sameEpoch && result.facts != nil {
					s.catalog.retainFacts(result.facts, result.proofs)
				}
			} else if lane != nil && !lane.canceled {
				code, reason := -32800, "request timed out or cancelled"
				if !current || !valid {
					code, reason = -32801, "workspace input changed during request"
				}
				_ = s.failure(lane.active.message.ID, code, reason)
			}
			// No request/session retains a second persistent batch beside the
			// catalog's aggregate fact store, including rejected worker results.
			result.facts = nil
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
			if lane != nil {
				lane.active = nil
			}
			if !s.shutdown && len(s.pending) > 0 && due.IsZero() {
				due = time.Now().Add(120 * time.Millisecond)
			}
		case <-s.catalog.events:
			uris, lines := s.catalog.notifications()
			for _, line := range lines {
				fmt.Fprintln(log, line)
			}
			if !s.shutdown && len(uris) > 0 {
				s.externalFacts = nil
				for _, uri := range uris {
					if _, opened := s.buffers[uri]; opened && !clearedForeign[uri] {
						s.pending[uri] = true
					}
				}
				if due.IsZero() {
					due = time.Now().Add(120 * time.Millisecond)
				}
			}
		case <-timer.C:
		}
	}
}
