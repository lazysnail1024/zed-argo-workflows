package lsp

import (
	"context"
	"encoding/json"

	"github.com/lazysnail1024/k8s-crd-toolkit/source"
)

// The two fixed lanes keep local editor queries independent of workspace edits
// and navigation. Admission happens before copying session state or starting a
// worker; cancellation does not release a running slot until that worker exits.
type requestLane struct {
	cancel   context.CancelFunc
	active   *queuedRequest
	running  bool
	canceled bool
	queue    []queuedRequest
}

func laneFor(method string) int {
	switch method {
	case "textDocument/hover", "textDocument/completion", "textDocument/documentSymbol":
		return 0
	default:
		return 1
	}
}

type requestInput struct {
	uri, root, override string
	readOnly, opened    bool
	revision            source.Revision
	version             int32
	unavailable         string
	rootState           *catalogRoot
	rootEpoch           uint64
}

func (s *session) queueRequest(m message) queuedRequest {
	q := queuedRequest{message: m, epoch: s.epoch}
	var params struct {
		TextDocument struct{ URI string }   `json:"textDocument"`
		Data         struct{ Token string } `json:"data"`
	}
	if json.Unmarshal(m.Params, &params) != nil {
		return q // The handler will report the malformed parameters.
	}
	q.input.uri = params.TextDocument.URI
	if m.Method == "codeAction/resolve" {
		q.input.uri = s.actions[params.Data.Token].uri
	}
	switch m.Method {
	case "textDocument/hover", "textDocument/completion", "textDocument/documentSymbol", "textDocument/definition", "textDocument/references":
		q.input.readOnly = q.input.uri != ""
	}
	if !q.input.readOnly {
		return q
	}
	q.input.root, q.input.override = s.root(q.input.uri), s.override
	b, opened := s.buffers[q.input.uri]
	q.input.opened, q.input.revision = opened, b.snapshot.Revision()
	q.input.version, q.input.unavailable = b.version, b.unavailable
	s.catalog.mu.Lock()
	q.input.rootState = s.catalog.roots[q.input.root]
	if q.input.rootState != nil {
		q.input.rootEpoch = q.input.rootState.inputEpoch
	}
	s.catalog.mu.Unlock()
	return q
}

// Read results prove their actual input, not the latest unrelated edit in the
// session. Their catalog dependency proofs are checked separately on delivery.
// Editing requests retain the stricter session-wide revision fence.
func (s *session) requestCurrent(q queuedRequest) bool {
	if s.shutdown {
		return false
	}
	input := q.input
	if !input.readOnly {
		return q.epoch == s.epoch
	}
	if s.root(input.uri) != input.root || s.override != input.override {
		return false
	}
	b, opened := s.buffers[input.uri]
	if opened != input.opened || opened && (b.snapshot.Revision() != input.revision || b.version != input.version || b.unavailable != input.unavailable) {
		return false
	}
	s.catalog.mu.Lock()
	defer s.catalog.mu.Unlock()
	root := s.catalog.roots[input.root]
	return root == input.rootState && (root == nil || root.inputEpoch == input.rootEpoch)
}
