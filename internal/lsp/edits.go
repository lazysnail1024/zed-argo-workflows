package lsp

import (
	"context"
	"crypto/sha256"
	"encoding/hex"
	"encoding/json"
	"fmt"
	"github.com/lazysnail1024/k8s-crd-toolkit/edit"
	"github.com/lazysnail1024/k8s-crd-toolkit/source"
	"github.com/lazysnail1024/zed-argo-workflows/compat"
	"github.com/lazysnail1024/zed-argo-workflows/internal/analysis"
	"github.com/lazysnail1024/zed-argo-workflows/internal/workspace"
	protocol "github.com/tliron/glsp/protocol_3_16"
	"path/filepath"
	"sort"
	"time"
)

type pendingAction struct {
	uri, title, version, fingerprint string
	epoch                            uint64
	revision                         source.Revision
	plan                             edit.Plan
}

func fingerprint(w workspace.Workspace) string {
	parts := []string{}
	for _, d := range w.Documents {
		parts = append(parts, string(d.Snapshot.URI())+"\n"+d.Target.ConfigHash+"\n"+d.Snapshot.Text())
	}
	sort.Strings(parts)
	h := sha256.New()
	for _, part := range parts {
		fmt.Fprintf(h, "%d:%s", len(part), part)
	}
	return hex.EncodeToString(h.Sum(nil))
}
func versionedEdit(uri string, b buffer, plan edit.Plan) any {
	return map[string]any{"documentChanges": []any{map[string]any{"textDocument": map[string]any{"uri": uri, "version": b.version}, "edits": analysis.PlanEdits(b.snapshot, plan)}}}
}
func (s *session) handleEdit(m message) (bool, int, error) {
	if !s.versionedEdits {
		return false, 0, s.failure(m.ID, -32602, "client does not support versioned documentChanges")
	}
	ctx, cancel := context.WithTimeout(s.context(), 5*time.Second)
	defer cancel()
	ctx = analysis.WithInvocation(ctx)
	if m.Method == "codeAction/resolve" {
		var p struct {
			Data struct {
				Token string `json:"token"`
			} `json:"data"`
		}
		if err := json.Unmarshal(m.Params, &p); err != nil {
			return false, 0, s.failure(m.ID, -32602, err.Error())
		}
		action, ok := s.actions[p.Data.Token]
		if !ok {
			return false, 0, s.failure(m.ID, -32801, "code action is stale")
		}
		if action.version != "" && !compat.Accepted(action.version) {
			return false, 0, s.failure(m.ID, -32602, "experimental target edits are disabled")
		}
		b, opened := s.buffers[action.uri]
		w, err := s.workspace(s.root(action.uri), "")
		if err != nil || !opened || s.epoch != action.epoch || b.snapshot.Revision() != action.revision || fingerprint(w) != action.fingerprint {
			return false, 0, s.failure(m.ID, -32801, "code action input changed")
		}
		if _, err := edit.Apply(b.snapshot, b.snapshot.Revision()+1, action.plan); err != nil {
			return false, 0, s.failure(m.ID, -32801, err.Error())
		}
		return false, 0, s.reply(m.ID, map[string]any{"title": action.title, "kind": "quickfix", "edit": versionedEdit(action.uri, b, action.plan), "data": map[string]any{"token": p.Data.Token}})
	}
	var p struct {
		TextDocument protocol.TextDocumentIdentifier `json:"textDocument"`
		Position     protocol.Position               `json:"position"`
		Range        analysis.Range                  `json:"range"`
		NewName      string                          `json:"newName"`
	}
	if err := json.Unmarshal(m.Params, &p); err != nil {
		return false, 0, s.failure(m.ID, -32602, err.Error())
	}
	uri := string(p.TextDocument.URI)
	b, opened := s.buffers[uri]
	if !opened {
		return false, 0, s.failure(m.ID, -32602, "edits require an opened versioned document")
	}
	name, _ := filePath(uri)
	if m.Method == "textDocument/codeAction" && name == filepath.Join(s.root(uri), ".argo-workflows-ls.yaml") {
		result := []map[string]any{}
		w, err := s.workspace(s.root(uri), "")
		if err != nil {
			return false, 0, s.reply(m.ID, result)
		}
		fix, err := analysis.ConfigPin(ctx, b.snapshot, w, s.override, p.Range)
		if err != nil {
			return false, 0, s.reply(m.ID, result)
		}
		next, err := edit.Apply(b.snapshot, b.snapshot.Revision()+1, fix.Plan)
		if err != nil {
			return false, 0, s.reply(m.ID, result)
		}
		text := next.Text()
		after, err := s.workspaceWithConfig(s.root(uri), "", &text)
		if err != nil || len(after.Documents) != len(w.Documents) {
			return false, 0, s.reply(m.ID, result)
		}
		for _, d := range w.Documents {
			found := false
			for _, other := range after.Documents {
				if other.Snapshot.URI() == d.Snapshot.URI() && other.Target.ID == d.Target.ID && other.Target.Version == d.Target.Version {
					found = true
					break
				}
			}
			if !found || analysis.SafeScopePlan(ctx, w, d, edit.Plan{BaseRevision: d.Snapshot.Revision()}) != nil {
				return false, 0, s.reply(m.ID, result)
			}
		}
		s.actionSequence++
		token := fmt.Sprintf("%d:%d", s.epoch, s.actionSequence)
		s.actions[token] = pendingAction{uri: uri, title: fix.Title, fingerprint: fingerprint(w), epoch: s.epoch, revision: b.snapshot.Revision(), plan: fix.Plan}
		result = append(result, map[string]any{"title": fix.Title, "kind": "quickfix", "data": map[string]any{"token": token}})
		return false, 0, s.reply(m.ID, result)
	}
	d, err := s.queryDocument(uri)
	if err != nil {
		return false, 0, s.failure(m.ID, -32803, err.Error())
	}
	if !compat.Accepted(d.Target.Version) {
		if m.Method == "textDocument/codeAction" {
			return false, 0, s.reply(m.ID, []any{})
		}
		return false, 0, s.failure(m.ID, -32602, "experimental target edits are disabled")
	}
	pos := source.Position{Line: p.Position.Line, UTF16Column: p.Position.Character}
	switch m.Method {
	case "textDocument/prepareRename":
		w, err := s.workspace(s.root(uri), "")
		if err != nil {
			return false, 0, s.failure(m.ID, -32602, err.Error())
		}
		if err := analysis.SafeScopePlan(ctx, w, d, edit.Plan{BaseRevision: b.snapshot.Revision()}); err != nil {
			return false, 0, s.failure(m.ID, -32602, err.Error())
		}
		result, err := analysis.PrepareRename(ctx, b.snapshot, d.Target.Version, pos)
		if err != nil {
			return false, 0, s.failure(m.ID, -32602, err.Error())
		}
		return false, 0, s.reply(m.ID, result)
	case "textDocument/rename":
		plan, err := analysis.RenameLocal(ctx, b.snapshot, d.Target.Version, pos, p.NewName)
		if err != nil {
			return false, 0, s.failure(m.ID, -32602, err.Error())
		}
		w, err := s.workspace(s.root(uri), "")
		if err != nil {
			return false, 0, s.failure(m.ID, -32602, err.Error())
		}
		if err := analysis.SafeScopePlan(ctx, w, d, plan); err != nil {
			return false, 0, s.failure(m.ID, -32602, err.Error())
		}
		return false, 0, s.reply(m.ID, versionedEdit(uri, b, plan))
	case "textDocument/codeAction":
		w, err := s.workspace(s.root(uri), "")
		if err != nil {
			return false, 0, s.failure(m.ID, -32803, err.Error())
		}
		result := []map[string]any{}
		if len(s.actions) > 128 {
			s.actions = map[string]pendingAction{}
		}
		fixes := analysis.FieldFixes(ctx, b.snapshot, d.Target.Version, p.Range)
		fixes = append(fixes, analysis.ValueFixes(ctx, w, d, p.Range)...)
		requestFingerprint := fingerprint(w)
		for _, fix := range fixes {
			if fix.Code == "FIX_FIELD_NAME" && analysis.SafeScopePlan(ctx, w, d, fix.Plan) != nil {
				continue
			}
			s.actionSequence++
			token := fmt.Sprintf("%d:%d", s.epoch, s.actionSequence)
			s.actions[token] = pendingAction{uri: uri, title: fix.Title, version: d.Target.Version, fingerprint: requestFingerprint, epoch: s.epoch, revision: b.snapshot.Revision(), plan: fix.Plan}
			result = append(result, map[string]any{"title": fix.Title, "kind": "quickfix", "data": map[string]any{"token": token}})
		}
		return false, 0, s.reply(m.ID, result)
	}
	return false, 0, s.failure(m.ID, -32601, "unknown edit method")
}
