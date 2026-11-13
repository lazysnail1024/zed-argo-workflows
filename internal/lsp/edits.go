package lsp

import (
	"context"
	"crypto/sha256"
	"encoding/hex"
	"encoding/json"
	"fmt"
	"github.com/lazysnail1024/k8s-crd-toolkit/edit"
	"github.com/lazysnail1024/k8s-crd-toolkit/index"
	"github.com/lazysnail1024/k8s-crd-toolkit/source"
	"github.com/lazysnail1024/zed-argo-workflows/compat"
	"github.com/lazysnail1024/zed-argo-workflows/internal/analysis"
	"github.com/lazysnail1024/zed-argo-workflows/internal/workspace"
	protocol "github.com/tliron/glsp/protocol_3_16"
	"path/filepath"
	"sort"
	"strings"
	"time"
)

type pendingAction struct {
	uri, title, version, fingerprint, scope string
	epoch                                   uint64
	revision                                source.Revision
	plan                                    edit.Plan
	local                                   bool
}

func detachedPlan(plan edit.Plan) edit.Plan {
	plan.Replacements = append([]edit.Replacement(nil), plan.Replacements...)
	for i := range plan.Replacements {
		plan.Replacements[i].ExpectedText = strings.Clone(plan.Replacements[i].ExpectedText)
		plan.Replacements[i].NewText = strings.Clone(plan.Replacements[i].NewText)
	}
	return plan
}

func editMethod(method string) bool {
	return method == "textDocument/codeAction" || method == "codeAction/resolve" || method == "textDocument/prepareRename" || method == "textDocument/rename"
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

func (s *session) actionFingerprint(w workspace.Workspace) string {
	// Isolated documents depend on their effective target, policy and namespace.
	// A configuration change to another scope cannot affect this safety proof.
	w.Documents = append([]workspace.Document(nil), w.Documents...)
	generations := map[string]uint64{}
	incomplete := map[string]bool{}
	for n, document := range w.Documents {
		if w.IncompleteScopes[document.Target.ID] {
			incomplete[document.Target.ID] = true
		}
		if strings.HasPrefix(document.Target.Scope, "opened:") {
			w.Documents[n].Target.ConfigHash = document.Target.ID + "\n" + document.Target.Version
			continue
		}
		_, state := s.catalog.index.Definitions(index.Scope(document.Target.ID), "")
		generations[document.Target.ID] = state.Generation
	}
	input, _ := json.Marshal(struct {
		Documents        string
		ReferencePolicy  string
		DefaultNamespace string
		Generations      map[string]uint64
		Incomplete       map[string]bool
	}{fingerprint(w), w.Config.ReferencePolicy, w.Config.DefaultNamespace, generations, incomplete})
	sum := sha256.Sum256(input)
	return hex.EncodeToString(sum[:])
}

func versionedEdit(uri string, b buffer, plan edit.Plan) any {
	return map[string]any{"documentChanges": []any{map[string]any{"textDocument": map[string]any{"uri": uri, "version": b.version}, "edits": analysis.PlanEdits(b.snapshot, plan)}}}
}

// Local fixes depend only on the opened document's effective target and policy.
// They do not acquire the indexed dependency closure or wait for discovery.
func (s *session) localEditWorkspace(d workspace.Document) (workspace.Workspace, error) {
	w, _, err := s.catalog.header(s.root(string(d.Snapshot.URI())))
	if err != nil {
		return w, err
	}
	w.Documents = []workspace.Document{d}
	w.IncompleteScopes = map[string]bool{d.Target.ID: true}
	return w, nil
}

func localActionFingerprint(w workspace.Workspace) string {
	w.Documents = append([]workspace.Document(nil), w.Documents...)
	for n, d := range w.Documents {
		if strings.HasPrefix(d.Target.Scope, "opened:") {
			w.Documents[n].Target.ConfigHash = d.Target.ID + "\n" + d.Target.Version
		}
	}
	input, _ := json.Marshal([]string{fingerprint(w), w.Config.ReferencePolicy, w.Config.DefaultNamespace})
	sum := sha256.Sum256(input)
	return hex.EncodeToString(sum[:])
}

func (s *session) rememberFix(d workspace.Document, input string, fix analysis.Fix, local bool) map[string]any {
	s.actionSequence++
	token := fmt.Sprintf("%d:%d", s.epoch, s.actionSequence)
	s.actions[token] = pendingAction{uri: string(d.Snapshot.URI()), title: fix.Title, version: d.Target.Version,
		scope: d.Target.Scope, fingerprint: input, epoch: s.epoch, revision: d.Snapshot.Revision(), plan: detachedPlan(fix.Plan), local: local}
	return map[string]any{"title": fix.Title, "kind": "quickfix", "data": map[string]any{"token": token}}
}

func (s *session) resourceEditWorkspace(d workspace.Document) (workspace.Workspace, error) {
	w, proof, err := s.catalog.workspace(s.context(), s.root(string(d.Snapshot.URI())), d.Target.Scope, "", true, true, true)
	if err != nil {
		return w, err
	}
	s.proofs = append(s.proofs, proof)
	return scopeWorkspace(w, d.Target.ID), nil
}

func (s *session) discardIndexProofForRefusal() {
	// A refused resource edit produces no changes. Index progress after didClose
	// must not mask a known invalid resource scope or buffer with -32801.
	// Keep root configuration proofs and the request's input-epoch check.
	for i := range s.proofs {
		s.proofs[i].states = nil
		s.proofs[i].files = nil
	}
}

func (s *session) requireResourceBuffers(w workspace.Workspace, files []source.URI) error {
	documents := make(map[source.URI]workspace.Document, len(w.Documents))
	for _, d := range w.Documents {
		documents[d.Snapshot.URI()] = d
	}
	for _, uri := range files {
		b, opened := s.buffers[string(uri)]
		if !opened || b.unavailable != "" {
			s.discardIndexProofForRefusal()
			return fmt.Errorf("resource rename requires every affected file to be open and versioned")
		}
		d, found := documents[uri]
		if !found || d.Snapshot.Revision() != b.snapshot.Revision() || d.Snapshot.Text() != b.snapshot.Text() || !compat.Accepted(d.Target.Version) {
			s.discardIndexProofForRefusal()
			return fmt.Errorf("resource rename input no longer matches an accepted buffer")
		}
	}
	return nil
}

func (s *session) handleEdit(m message) (bool, int, error) {
	if !s.versionedEdits {
		return false, 0, s.failure(m.ID, -32602, "client does not support versioned documentChanges")
	}
	if m.Method == "textDocument/codeAction" && len(s.actions) >= 128 {
		s.actions = map[string]pendingAction{}
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
		var w workspace.Workspace
		var err error
		if action.local {
			var d workspace.Document
			d, err = s.queryDocument(action.uri)
			if err == nil {
				w, err = s.localEditWorkspace(d)
			}
		} else {
			w, err = s.documentWorkspace(action.uri, action.scope)
		}
		var currentFingerprint string
		if action.local {
			currentFingerprint = localActionFingerprint(w)
		} else {
			currentFingerprint = s.actionFingerprint(w)
		}
		if err != nil || !opened || b.snapshot.Revision() != action.revision || currentFingerprint != action.fingerprint {
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
		for _, incomplete := range w.IncompleteScopes {
			if incomplete {
				return false, 0, s.reply(m.ID, result)
			}
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
		s.actions[token] = pendingAction{uri: uri, title: fix.Title, fingerprint: s.actionFingerprint(w), epoch: s.epoch, revision: b.snapshot.Revision(), plan: detachedPlan(fix.Plan)}
		result = append(result, map[string]any{"title": fix.Title, "kind": "quickfix", "data": map[string]any{"token": token}})
		return false, 0, s.reply(m.ID, result)
	}
	d, err := s.queryDocument(uri)
	if err != nil {
		return false, 0, s.failure(m.ID, -32803, err.Error())
	}
	if !compat.Accepted(d.Target.Version) {
		if m.Method == "textDocument/codeAction" {
			return false, 0, s.reply(m.ID, s.withRenderAction(uri, nil))
		}
		return false, 0, s.failure(m.ID, -32602, "experimental target edits are disabled")
	}
	pos := source.Position{Line: p.Position.Line, UTF16Column: p.Position.Character}
	resourceName := false
	if m.Method == "textDocument/prepareRename" || m.Method == "textDocument/rename" {
		resourceName, err = analysis.ResourceRenameAt(ctx, b.snapshot, pos)
		if err != nil {
			return false, 0, s.failure(m.ID, -32602, err.Error())
		}
	}
	switch m.Method {
	case "textDocument/prepareRename":
		w, err := s.resourceEditWorkspace(d)
		if err != nil {
			return false, 0, s.failure(m.ID, -32602, err.Error())
		}
		if err := analysis.SafeScopePlan(ctx, w, d, edit.Plan{BaseRevision: b.snapshot.Revision()}); err != nil {
			return false, 0, s.failure(m.ID, -32602, err.Error())
		}
		var result any
		if resourceName {
			var files []source.URI
			result, files, err = analysis.PrepareResourceRename(ctx, w, uri, pos)
			if err == nil {
				err = s.requireResourceBuffers(w, files)
			}
		} else {
			result, err = analysis.PrepareRename(ctx, b.snapshot, d.Target.Version, pos)
		}
		if err != nil {
			return false, 0, s.failure(m.ID, -32602, err.Error())
		}
		return false, 0, s.reply(m.ID, result)
	case "textDocument/rename":
		w, err := s.resourceEditWorkspace(d)
		if err != nil {
			return false, 0, s.failure(m.ID, -32602, err.Error())
		}
		if resourceName {
			plans, err := analysis.RenameResource(ctx, w, uri, pos, p.NewName)
			if err != nil {
				s.discardIndexProofForRefusal()
				return false, 0, s.failure(m.ID, -32602, err.Error())
			}
			files := make([]source.URI, 0, len(plans))
			for file := range plans {
				files = append(files, file)
			}
			sort.Slice(files, func(i, j int) bool { return files[i] < files[j] })
			if err := s.requireResourceBuffers(w, files); err != nil {
				return false, 0, s.failure(m.ID, -32602, err.Error())
			}
			changes := []any{}
			for _, file := range files {
				buffer := s.buffers[string(file)]
				changes = append(changes, map[string]any{"textDocument": map[string]any{"uri": file, "version": buffer.version}, "edits": analysis.PlanEdits(buffer.snapshot, plans[file])})
			}
			return false, 0, s.reply(m.ID, map[string]any{"documentChanges": changes})
		}
		plan, err := analysis.RenameLocal(ctx, b.snapshot, d.Target.Version, pos, p.NewName)
		if err != nil {
			return false, 0, s.failure(m.ID, -32602, err.Error())
		}
		if err := analysis.SafeScopePlan(ctx, w, d, plan); err != nil {
			return false, 0, s.failure(m.ID, -32602, err.Error())
		}
		return false, 0, s.reply(m.ID, versionedEdit(uri, b, plan))
	case "textDocument/codeAction":
		local, err := s.localEditWorkspace(d)
		if err != nil {
			return false, 0, s.failure(m.ID, -32803, err.Error())
		}
		result := []map[string]any{}
		localFixes := analysis.FieldFixes(analysis.WithLogicalResult(ctx), b.snapshot, d.Target.Version, p.Range)
		localFixes = append(localFixes, analysis.ValueFixes(analysis.WithLogicalResult(ctx), local, d, p.Range)...)
		localFingerprint := localActionFingerprint(local)
		for _, fix := range localFixes {
			if analysis.SafeLocalPlan(ctx, local, d, fix.Plan) == nil {
				result = append(result, s.rememberFix(d, localFingerprint, fix, true))
			}
		}
		if len(result) != 0 {
			return false, 0, s.reply(m.ID, s.withRenderAction(uri, result))
		}
		w, err := s.workspace(s.root(uri), "")
		if err != nil {
			return false, 0, s.failure(m.ID, -32803, err.Error())
		}
		if w.IncompleteScopes[d.Target.ID] {
			return false, 0, s.reply(m.ID, s.withRenderAction(uri, result))
		}
		fixes := analysis.FieldFixes(analysis.WithLogicalResult(ctx), b.snapshot, d.Target.Version, p.Range)
		fixes = append(fixes, analysis.ValueFixes(analysis.WithLogicalResult(ctx), w, d, p.Range)...)
		scoped := scopeWorkspace(w, d.Target.ID)
		requestFingerprint := s.actionFingerprint(scoped)
		for _, fix := range fixes {
			if fix.Code == "FIX_FIELD_NAME" && analysis.SafeScopePlan(ctx, w, d, fix.Plan) != nil {
				continue
			}
			result = append(result, s.rememberFix(d, requestFingerprint, fix, false))
		}
		return false, 0, s.reply(m.ID, s.withRenderAction(uri, result))
	}
	return false, 0, s.failure(m.ID, -32601, "unknown edit method")
}
