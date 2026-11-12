// Package lsp owns stdio framing and adapts standard LSP messages to immutable inputs.
package lsp

import (
	"bufio"
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"github.com/lazysnail1024/k8s-crd-toolkit/index"
	"github.com/lazysnail1024/k8s-crd-toolkit/source"
	shared "github.com/lazysnail1024/k8s-crd-toolkit/workspace"
	"github.com/lazysnail1024/zed-argo-workflows/compat"
	"github.com/lazysnail1024/zed-argo-workflows/internal/analysis"
	"github.com/lazysnail1024/zed-argo-workflows/internal/buildinfo"
	"github.com/lazysnail1024/zed-argo-workflows/internal/fileuri"
	"github.com/lazysnail1024/zed-argo-workflows/internal/workspace"
	protocol "github.com/tliron/glsp/protocol_3_16"
	"io"
	"path/filepath"
	"strconv"
	"strings"
	"time"
)

type message struct {
	JSONRPC string          `json:"jsonrpc"`
	ID      json.RawMessage `json:"id"`
	Method  string          `json:"method"`
	Params  json.RawMessage `json:"params"`
}
type buffer struct {
	snapshot    source.Snapshot
	version     int32
	unavailable string
}
type session struct {
	catalog           *catalog
	focus, method     string
	callerDefinitions bool
	proofs            []catalogProof
	localPublication  bool
	queryResults      map[string]json.RawMessage
	queryBytes        int
	queryKey          string
	externalFacts     *analysis.ExternalFacts
	facts             *analysis.Facts
	factInput         *workspace.Workspace
	factBoundary      string
	factMixed         bool
	workspaces        map[workspaceKey]workspace.Workspace
	ctx               context.Context
	pending           map[string]bool
	out               io.Writer
	log               io.Writer
	roots             []string
	override          string
	buffers           map[string]buffer
	initialized       bool
	shutdown          bool
	epoch             uint64
	versionedEdits    bool
	actions           map[string]pendingAction
	actionSequence    uint64
}

func read(r *bufio.Reader) (message, error) {
	var m message
	length := -1
	size := 0
	for {
		line, err := r.ReadString('\n')
		if err != nil {
			return m, err
		}
		size += len(line)
		if size > 8192 {
			return m, fmt.Errorf("header exceeds limit")
		}
		if line == "\r\n" {
			break
		}
		key, value, ok := strings.Cut(strings.TrimSpace(line), ":")
		if !ok {
			return m, fmt.Errorf("invalid header")
		}
		if strings.EqualFold(key, "Content-Length") {
			if length != -1 {
				return m, fmt.Errorf("duplicate Content-Length")
			}
			length, err = strconv.Atoi(strings.TrimSpace(value))
			if err != nil {
				return m, err
			}
		}
	}
	if length < 0 || length > 10*1024*1024 {
		return m, fmt.Errorf("invalid Content-Length")
	}
	data := make([]byte, length)
	if _, err := io.ReadFull(r, data); err != nil {
		return m, err
	}
	if err := json.Unmarshal(data, &m); err != nil {
		return m, err
	}
	if m.JSONRPC != "2.0" || m.Method == "" {
		return m, fmt.Errorf("invalid JSON-RPC request")
	}
	return m, nil
}
func (s *session) write(value any) error {
	data, err := json.Marshal(value)
	if err != nil {
		return err
	}
	if len(data) > 10*1024*1024 {
		return fmt.Errorf("output exceeds limit")
	}
	_, err = fmt.Fprintf(s.out, "Content-Length: %d\r\n\r\n%s", len(data), data)
	return err
}
func (s *session) reply(id json.RawMessage, result any) error {
	if s.queryKey != "" && s.context().Err() == nil {
		raw, err := json.Marshal(result)
		if err == nil && len(raw) <= 4*1024*1024 {
			if len(s.queryResults) >= 16 || s.queryBytes+len(raw) > 4*1024*1024 {
				s.queryResults = map[string]json.RawMessage{}
				s.queryBytes = 0
			}
			if _, exists := s.queryResults[s.queryKey]; !exists {
				s.queryResults[s.queryKey] = raw
				s.queryBytes += len(raw)
			}
		}
	}
	return s.write(map[string]any{"jsonrpc": "2.0", "id": id, "result": result})
}
func (s *session) failure(id json.RawMessage, code int, text string) error {
	if len(id) == 0 {
		return nil
	}
	return s.write(map[string]any{"jsonrpc": "2.0", "id": id, "error": map[string]any{"code": code, "message": text}})
}
func filePath(uri string) (string, error) {
	return fileuri.Path(uri)
}
func (s *session) root(uri string) string {
	name, err := filePath(uri)
	if err != nil {
		return ""
	}
	best := ""
	for _, root := range s.roots {
		rel, err := filepath.Rel(root, name)
		if err == nil && rel != ".." && !strings.HasPrefix(rel, ".."+string(filepath.Separator)) && len(root) > len(best) {
			best = root
		}
	}
	return best
}

type workspaceKey struct{ root, scope string }

// workspace materializes only the dependency inputs needed by this request.
// Catalog summaries and discovery outlive workers; this cache lasts one query.
func (s *session) workspace(root, selected string) (workspace.Workspace, error) {
	if w, ok := s.workspaces[workspaceKey{root, selected}]; ok && s.context().Err() == nil {
		return w, nil
	}
	reverse := s.method == "textDocument/references" || editMethod(s.method) || s.callerDefinitions
	focus := s.focus
	if path, err := filePath(focus); err == nil && filepath.Base(path) == ".argo-workflows-ls.yaml" {
		focus = ""
	}
	all := focus == ""
	w, proof, err := s.catalog.workspace(s.context(), root, selected, focus, reverse, all, s.method == "textDocument/codeAction" || s.method == "codeAction/resolve")
	if err != nil {
		return w, err
	}
	s.proofs = append(s.proofs, proof)
	// Catalog documents and their proofs are one read. Replacing a dependency
	// with an older worker buffer would attach a fresh proof to stale source.
	if s.context().Err() == nil && s.workspaces != nil {
		s.workspaces[workspaceKey{root, selected}] = w
		boundary := analysis.FactBoundary(w)
		if boundary == "" || s.factBoundary != "" && s.factBoundary != boundary {
			s.factMixed, s.factInput = true, nil
		}
		if !s.factMixed && s.factInput == nil {
			analysis.RestoreFacts(s.context(), s.facts, w)
			s.factBoundary, s.factInput = boundary, &w
		}
	}
	return w, nil
}

// Configuration quick fixes retarget the already proven current input. They do
// not start an independent directory walk or overwrite the live catalog header.
func (s *session) workspaceWithConfig(root, selected string, overlay *string) (workspace.Workspace, error) {
	before, err := s.workspace(root, selected)
	if err != nil {
		return before, err
	}
	after, err := workspace.Describe(s.context(), root, s.override, overlay)
	if err != nil {
		return after, err
	}
	for _, d := range before.Documents {
		address := string(d.Snapshot.URI())
		if _, opened := s.buffers[address]; !opened {
			path, pathErr := filePath(address)
			if pathErr != nil {
				return after, pathErr
			}
			candidate, candidateErr := workspace.Candidate(after, path, s.roots)
			if candidateErr != nil {
				return after, candidateErr
			}
			if !candidate {
				continue
			}
		}
		next, err := workspace.OpenDocument(after, d.Snapshot, s.override)
		if err != nil {
			return after, err
		}
		if selected == "" || next.Target.Scope == selected {
			after.Documents = append(after.Documents, next)
		}
	}
	after.IncompleteScopes = before.IncompleteScopes
	after.NotEvaluated = before.NotEvaluated
	return after, nil
}
func (s *session) publish(uri string) error {
	if s.pending != nil {
		s.pending[uri] = true
		return nil
	}
	root := s.root(uri)
	diagnostics := []map[string]any{}
	params := map[string]any{"uri": uri, "diagnostics": diagnostics}
	b, opened := s.buffers[uri]
	if opened {
		params["version"] = b.version
	}
	if opened && b.unavailable != "" {
		fmt.Fprintln(s.log, b.unavailable)
		params["diagnostics"] = []map[string]any{analysisStatus("WF_ANALYSIS_UNAVAILABLE", b.unavailable)}
		return s.write(map[string]any{"jsonrpc": "2.0", "method": "textDocument/publishDiagnostics", "params": params})
	}
	if root == "" {
		fmt.Fprintln(s.log, "document is outside workspace roots")
		return s.write(map[string]any{"jsonrpc": "2.0", "method": "textDocument/publishDiagnostics", "params": params})
	}
	if configPath, err := filePath(uri); err == nil && filepath.Base(configPath) == ".argo-workflows-ls.yaml" {
		var overlay *string
		if opened {
			text := b.snapshot.Text()
			overlay = &text
		}
		if err := workspace.ValidateConfig(s.context(), filepath.Dir(configPath), s.override, overlay); err != nil {
			params["diagnostics"] = []map[string]any{{"range": analysis.Range{}, "code": "WF_CONFIGURATION_ERROR", "source": "argo-workflows", "severity": 1, "message": err.Error()}}
		}
		return s.write(map[string]any{"jsonrpc": "2.0", "method": "textDocument/publishDiagnostics", "params": params})
	}
	return s.publishIndexed(uri, params)
}
func (s *session) bufferDocument(uri string) (workspace.Document, error) {
	root := s.root(uri)
	if root == "" {
		return workspace.Document{}, fmt.Errorf("document is outside workspace")
	}
	snapshot, err := source.New(source.URI(uri), 0, "")
	if err != nil {
		return workspace.Document{}, err
	}
	if b, ok := s.buffers[uri]; ok {
		snapshot = b.snapshot
	}
	if s.catalog != nil {
		// Capture the metadata revision actually read by this worker. Resolving
		// pending configuration is not itself another client input change.
		s.catalog.mu.Lock()
		if current := s.catalog.roots[root]; current != nil && current.problem == "" {
			header := current.header
			s.proofs = append(s.proofs, catalogProof{root: root, epoch: current.epoch})
			s.catalog.mu.Unlock()
			return workspace.OpenDocument(header, snapshot, s.override)
		}
		s.catalog.mu.Unlock()
	}
	configURI, err := fileuri.FromPath(filepath.Join(root, ".argo-workflows-ls.yaml"))
	if err != nil {
		return workspace.Document{}, err
	}
	var overlay *string
	if config, ok := s.buffers[configURI]; ok {
		if config.unavailable != "" {
			return workspace.Document{}, fmt.Errorf("%s", config.unavailable)
		}
		text := config.snapshot.Text()
		overlay = &text
	}
	return workspace.OpenBuffer(s.context(), root, s.override, overlay, snapshot)
}

func (s *session) queryDocument(uri string) (workspace.Document, error) {
	root := s.root(uri)
	if root == "" {
		return workspace.Document{}, fmt.Errorf("document is outside workspace")
	}
	if _, ok := s.buffers[uri]; ok {
		return s.bufferDocument(uri)
	}
	document, proof, err := s.catalog.document(s.context(), uri)
	if err == nil {
		// Symbols use the freshly read source, not indexed file contents.
		// Keep its content proof, while allowing an index scan to catch up
		// to precisely these same bytes after a missed file watch.
		if s.method == "textDocument/documentSymbol" {
			proof.allowIndexCatchUp = true
		}
		s.proofs = append(s.proofs, proof)
	}
	return document, err
}

// Check retained text before constructing a snapshot. Refused updates keep the
// last accepted snapshot unavailable; queries never fall back to old or disk data.
func (s *session) bufferBudget(uri string, size int) string {
	limit := 5 * 1024 * 1024
	name, _ := filePath(uri)
	if filepath.Base(name) == ".argo-workflows-ls.yaml" {
		limit = 1024 * 1024
	}
	if size > limit {
		return fmt.Sprintf("buffer exceeds %d MiB", limit/(1024*1024))
	}
	if size > workspace.MaxSnapshotBytes-(s.catalog.documents.OpenBytes()-len(s.buffers[uri].snapshot.Text())) {
		return "retained open-buffer text exceeds 128 MiB"
	}
	return ""
}
func (s *session) refuseBuffer(uri string, version int32, reason string) error {
	s.epoch++
	b, ok := s.buffers[uri]
	if !ok {
		var err error
		b.snapshot, err = s.catalog.documents.Open(source.URI(uri), "")
		if errors.Is(err, shared.ErrLimit) {
			// No source was accepted. Keep only protocol identity until a full
			// replacement can claim a released document slot.
			b.snapshot, _ = source.New(source.URI(uri), 0, "")
		} else if err != nil {
			return err
		}
	}
	b.version = version
	b.unavailable = reason
	s.buffers[uri] = b
	fmt.Fprintln(s.log, reason)
	return s.publish(uri)
}

func (s *session) handle(m message) (bool, int, error) {
	if m.Method == "exit" {
		if s.shutdown {
			return true, 0, nil
		}
		return true, 1, nil
	}
	if m.Method == "initialize" {
		if s.initialized {
			return false, 0, s.failure(m.ID, -32600, "already initialized")
		}
		var p struct {
			RootURI      string `json:"rootUri"`
			Capabilities struct {
				Workspace struct {
					WorkspaceEdit struct {
						DocumentChanges bool `json:"documentChanges"`
					} `json:"workspaceEdit"`
				} `json:"workspace"`
			} `json:"capabilities"`
			WorkspaceFolders []struct {
				URI string `json:"uri"`
			} `json:"workspaceFolders"`
			InitializationOptions struct {
				TargetVersion string `json:"targetVersion"`
			} `json:"initializationOptions"`
		}
		if err := json.Unmarshal(m.Params, &p); err != nil {
			return false, 0, s.failure(m.ID, -32602, err.Error())
		}
		s.override = p.InitializationOptions.TargetVersion
		s.versionedEdits = p.Capabilities.Workspace.WorkspaceEdit.DocumentChanges
		for _, f := range p.WorkspaceFolders {
			root, err := filePath(f.URI)
			if err != nil {
				return false, 0, s.failure(m.ID, -32602, err.Error())
			}
			exists := false
			for _, known := range s.roots {
				if known == root {
					exists = true
				}
			}
			if !exists {
				s.roots = append(s.roots, root)
			}
		}
		if len(s.roots) == 0 && p.RootURI != "" {
			root, err := filePath(p.RootURI)
			if err != nil {
				return false, 0, s.failure(m.ID, -32602, err.Error())
			}
			s.roots = []string{root}
		}
		s.initialized = true
		capabilities := map[string]any{"workspace": map[string]any{"workspaceFolders": map[string]any{"supported": true, "changeNotifications": true}}, "positionEncoding": "utf-16", "completionProvider": map[string]any{"resolveProvider": false}, "hoverProvider": true, "definitionProvider": true, "referencesProvider": true, "documentSymbolProvider": true, "workspaceSymbolProvider": true, "textDocumentSync": map[string]any{"openClose": true, "change": 2, "save": map[string]any{"includeText": false}}}
		if s.versionedEdits {
			capabilities["codeActionProvider"] = map[string]any{"resolveProvider": true, "codeActionKinds": []string{"quickfix"}}
			capabilities["renameProvider"] = map[string]any{"prepareProvider": true}
		}
		return false, 0, s.reply(m.ID, map[string]any{"capabilities": capabilities, "serverInfo": map[string]any{"name": "argo-workflows-ls", "version": buildinfo.ServerVersion}})
	}
	if !s.initialized {
		return false, 0, s.failure(m.ID, -32002, "server not initialized")
	}
	if s.shutdown {
		return false, 0, s.failure(m.ID, -32600, "server is shutting down")
	}
	if len(m.ID) > 0 && strings.HasPrefix(m.Method, "textDocument/") {
		var p struct {
			TextDocument struct {
				URI string `json:"uri"`
			} `json:"textDocument"`
		}
		if json.Unmarshal(m.Params, &p) == nil {
			if b, ok := s.buffers[p.TextDocument.URI]; ok && b.unavailable != "" {
				return false, 0, s.failure(m.ID, -32602, b.unavailable)
			}
		}
	}
	// Cross-file results require fresh scope input. Completion first checks its
	// local fields and names below, so local queries never acquire a whole scope.
	if m.Method == "textDocument/definition" || m.Method == "textDocument/references" {
		var p struct {
			TextDocument protocol.TextDocumentIdentifier `json:"textDocument"`
			Position     protocol.Position               `json:"position"`
		}
		if json.Unmarshal(m.Params, &p) == nil {
			uri := string(p.TextDocument.URI)
			if d, err := s.queryDocument(uri); err == nil {
				if m.Method == "textDocument/definition" {
					s.callerDefinitions = analysis.DefinitionNeedsCallers(s.context(), d.Snapshot, d.Target.Version, source.Position{Line: p.Position.Line, UTF16Column: p.Position.Character})
				}
				if w, err := s.workspace(s.root(uri), d.Target.Scope); err == nil {
					if raw, ok := s.cachedScopeQuery(m, w); ok {
						return false, 0, s.reply(m.ID, raw)
					}
				}
			}
		}
	}
	switch m.Method {
	case "textDocument/codeAction", "codeAction/resolve", "textDocument/prepareRename", "textDocument/rename":
		return s.handleEdit(m)
	case "textDocument/definition", "textDocument/references":
		var p struct {
			protocol.TextDocumentPositionParams
			Context struct {
				IncludeDeclaration bool `json:"includeDeclaration"`
			} `json:"context"`
		}
		if err := json.Unmarshal(m.Params, &p); err != nil {
			return false, 0, s.failure(m.ID, -32602, err.Error())
		}
		d, err := s.queryDocument(string(p.TextDocument.URI))
		if err != nil {
			return false, 0, s.failure(m.ID, -32803, err.Error())
		}
		ctx, cancel := context.WithTimeout(s.context(), 5*time.Second)
		defer cancel()
		pos := source.Position{Line: p.Position.Line, UTF16Column: p.Position.Character}
		var result []analysis.Location
		if m.Method == "textDocument/definition" {
			result = analysis.Definitions(ctx, d.Snapshot, d.Target.Version, pos)
			if w, e := s.workspace(s.root(string(d.Snapshot.URI())), d.Target.Scope); e == nil {
				result = append(result, analysis.ExternalDefinitions(ctx, scopeWorkspace(w, d.Target.ID), string(d.Snapshot.URI()), pos)...)
			}
		} else {
			result = analysis.References(ctx, d.Snapshot, d.Target.Version, pos, p.Context.IncludeDeclaration)
			if w, e := s.workspace(s.root(string(d.Snapshot.URI())), d.Target.Scope); e == nil {
				result = analysis.ScopeReferences(ctx, scopeWorkspace(w, d.Target.ID), string(d.Snapshot.URI()), pos, p.Context.IncludeDeclaration)
			} else {
				return false, 0, s.failure(m.ID, -32803, e.Error())
			}
		}
		return false, 0, s.reply(m.ID, result)
	case "textDocument/documentSymbol":
		var p struct {
			TextDocument protocol.TextDocumentIdentifier `json:"textDocument"`
		}
		if err := json.Unmarshal(m.Params, &p); err != nil {
			return false, 0, s.failure(m.ID, -32602, err.Error())
		}
		d, err := s.queryDocument(string(p.TextDocument.URI))
		if err != nil {
			return false, 0, s.failure(m.ID, -32803, err.Error())
		}
		return false, 0, s.reply(m.ID, analysis.DocumentSymbols(s.context(), d.Snapshot, d.Target.Version))
	case "workspace/symbol":
		var p struct {
			Query string `json:"query"`
		}
		if err := json.Unmarshal(m.Params, &p); err != nil {
			return false, 0, s.failure(m.ID, -32602, err.Error())
		}
		items, proofs, err := s.catalog.workspaceSymbols(s.context(), p.Query, workspaceSymbolWorkLimit)
		if err != nil {
			code := -32803
			if errors.Is(err, index.ErrQueryChanged) {
				code = -32801
			}
			return false, 0, s.failure(m.ID, code, err.Error())
		}
		s.proofs = append(s.proofs, proofs...)
		return false, 0, s.reply(m.ID, items)
	case "textDocument/completion", "textDocument/hover":
		var p protocol.TextDocumentPositionParams
		if err := json.Unmarshal(m.Params, &p); err != nil {
			return false, 0, s.failure(m.ID, -32602, err.Error())
		}
		document, err := s.queryDocument(string(p.TextDocument.URI))
		if err != nil {
			return false, 0, s.failure(m.ID, -32803, err.Error())
		}
		ctx, cancel := context.WithTimeout(s.context(), 5*time.Second)
		defer cancel()
		pos := source.Position{Line: p.Position.Line, UTF16Column: p.Position.Character}
		var result any
		if m.Method == "textDocument/completion" {
			fields, fieldErr := analysis.CompleteFields(ctx, document.Snapshot, document.Target.Version, pos)
			err = fieldErr
			names, nameErr := analysis.CompleteNames(ctx, document.Snapshot, document.Target.Version, pos)
			if nameErr != nil {
				err = nameErr
			}
			items := append(fields, names...)
			if err == nil && len(items) == 0 && analysis.ExternalNamePosition(ctx, document.Snapshot, document.Target.Version, pos) {
				uri := string(document.Snapshot.URI())
				w, workspaceErr := s.documentWorkspace(uri, document.Target.Scope)
				if workspaceErr != nil {
					return false, 0, s.failure(m.ID, -32803, workspaceErr.Error())
				}
				if raw, ok := s.cachedScopeQuery(m, w); ok {
					return false, 0, s.reply(m.ID, raw)
				}
				external, candidates, externalErr := analysis.CompleteExternalNames(ctx, scopeWorkspace(w, document.Target.ID), uri, pos, s.catalog.index)
				if externalErr != nil {
					err = externalErr
				}
				if len(candidates) > 0 {
					proof, current := s.catalog.candidateProof(s.root(uri), candidates)
					if !current {
						return false, 0, s.failure(m.ID, -32801, "resource candidates changed during completion")
					}
					s.proofs = append(s.proofs, proof)
				}
				items = append(items, external...)
			}
			result = items
		} else {
			result, err = analysis.HoverField(ctx, document.Snapshot, document.Target.Version, pos)
			if hover, ok := result.(map[string]any); ok {
				if contents, ok := hover["contents"].(map[string]any); ok {
					from := document.Target.Source
					if s.override != "" {
						from = "editor settings override"
					}
					status := "accepted local editing target"
					if !compat.Accepted(document.Target.Version) {
						status = "experimental read-only target; analysis remains incomplete"
					}
					contents["value"] = fmt.Sprint(contents["value"]) + "\n\nTarget selection: " + from + " · scope: " + document.Target.Scope + " · " + status
				}
			}
		}
		if err != nil {
			return false, 0, s.failure(m.ID, -32803, err.Error())
		}
		return false, 0, s.reply(m.ID, result)
	case "initialized", "$/cancelRequest":
		return false, 0, nil
	case "shutdown":
		s.shutdown = true
		return false, 0, s.reply(m.ID, nil)
	case "textDocument/didOpen":
		var p protocol.DidOpenTextDocumentParams
		if err := json.Unmarshal(m.Params, &p); err != nil {
			return false, 0, s.failure(m.ID, -32602, err.Error())
		}
		uri := string(p.TextDocument.URI)
		if reason := s.bufferBudget(uri, len(p.TextDocument.Text)); reason != "" {
			return false, 0, s.refuseBuffer(uri, p.TextDocument.Version, reason)
		}
		s.epoch++
		var snapshot source.Snapshot
		var err error
		if current, opened := s.catalog.documents.Snapshot(source.URI(uri)); opened {
			snapshot, err = s.catalog.documents.Change(source.URI(uri), current.Revision(), []source.Change{{Range: source.Span{End: len(current.Text())}, Text: p.TextDocument.Text}})
		} else {
			snapshot, err = s.catalog.documents.Open(source.URI(uri), p.TextDocument.Text)
		}
		if errors.Is(err, shared.ErrLimit) {
			return false, 0, s.refuseBuffer(uri, p.TextDocument.Version, "open document capacity exceeded")
		}
		if err != nil {
			return false, 0, err
		}
		s.buffers[uri] = buffer{snapshot: snapshot, version: p.TextDocument.Version}
		return false, 0, s.publish(uri)
	case "textDocument/didChange":
		var p protocol.DidChangeTextDocumentParams
		if err := json.Unmarshal(m.Params, &p); err != nil {
			fmt.Fprintln(s.log, err)
			return false, 0, nil
		}
		uri := string(p.TextDocument.URI)
		b, ok := s.buffers[uri]
		if !ok || p.TextDocument.Version <= b.version {
			fmt.Fprintln(s.log, "ignored stale or unopened buffer change")
			return false, 0, nil
		}
		if len(p.ContentChanges) == 0 {
			b.version = p.TextDocument.Version
			s.buffers[uri] = b
			return false, 0, nil
		}
		snapshot := b.snapshot
		available := b.unavailable == ""
		changes := make([]source.Change, 0, len(p.ContentChanges))
		for n, change := range p.ContentChanges {
			var span source.Span
			var text string
			switch c := change.(type) {
			case protocol.TextDocumentContentChangeEvent:
				if !available {
					return false, 0, s.refuseBuffer(uri, p.TextDocument.Version, "buffer unavailable after resource refusal; a full replacement or close/reopen is required")
				}
				start, e1 := snapshot.Offset(source.Position{Line: c.Range.Start.Line, UTF16Column: c.Range.Start.Character})
				end, e2 := snapshot.Offset(source.Position{Line: c.Range.End.Line, UTF16Column: c.Range.End.Character})
				if e1 != nil || e2 != nil {
					fmt.Fprintln(s.log, "invalid UTF-16 change boundary")
					return false, 0, nil
				}
				span = source.Span{Start: start, End: end}
				text = c.Text
			case protocol.TextDocumentContentChangeEventWhole:
				available = true
				span = source.Span{Start: 0, End: len(snapshot.Text())}
				text = c.Text
			default:
				return false, 0, fmt.Errorf("unsupported change")
			}
			if reason := s.bufferBudget(uri, len(snapshot.Text())-(span.End-span.Start)+len(text)); reason != "" {
				return false, 0, s.refuseBuffer(uri, p.TextDocument.Version, reason)
			}
			changes = append(changes, source.Change{Range: span, Text: text})
			if n+1 < len(p.ContentChanges) {
				// Later LSP ranges refer to earlier edits in this notification.
				// This temporary view translates coordinates only; Documents
				// atomically accepts all changes and owns the resulting revision.
				var err error
				snapshot, err = snapshot.Apply(snapshot.Revision()+1, changes[len(changes)-1:])
				if err != nil {
					fmt.Fprintln(s.log, err)
					return false, 0, nil
				}
			}
		}
		var err error
		if _, opened := s.catalog.documents.Snapshot(source.URI(uri)); opened {
			snapshot, err = s.catalog.documents.Change(source.URI(uri), b.snapshot.Revision(), changes)
		} else {
			snapshot, err = b.snapshot.Apply(b.snapshot.Revision()+1, changes)
			if err == nil {
				snapshot, err = s.catalog.documents.Open(source.URI(uri), snapshot.Text())
			}
		}
		if errors.Is(err, shared.ErrLimit) {
			return false, 0, s.refuseBuffer(uri, p.TextDocument.Version, "open document capacity exceeded")
		}
		if err != nil {
			fmt.Fprintln(s.log, err)
			return false, 0, nil
		}
		s.epoch++
		s.buffers[uri] = buffer{snapshot: snapshot, version: p.TextDocument.Version}
		return false, 0, s.publish(uri)
	case "textDocument/didClose":
		var p protocol.DidCloseTextDocumentParams
		if err := json.Unmarshal(m.Params, &p); err != nil {
			return false, 0, nil
		}
		uri := string(p.TextDocument.URI)
		s.catalog.documents.Close(source.URI(uri))
		delete(s.buffers, uri)
		for token, action := range s.actions {
			if action.uri == uri {
				delete(s.actions, token)
			}
		}
		s.epoch++
		return false, 0, s.publish(uri)
	case "workspace/didChangeWorkspaceFolders":
		var p struct {
			Event struct {
				Added, Removed []struct {
					URI string `json:"uri"`
				}
			}
		}
		if err := json.Unmarshal(m.Params, &p); err != nil {
			return false, 0, nil
		}
		for _, removed := range p.Event.Removed {
			path, _ := filePath(removed.URI)
			for token, action := range s.actions {
				if s.root(action.uri) == path {
					delete(s.actions, token)
				}
			}
			s.queryResults = map[string]json.RawMessage{}
			s.queryBytes = 0
			s.facts = nil
			s.externalFacts = nil
			roots := []string{}
			for _, root := range s.roots {
				if root != path {
					roots = append(roots, root)
				}
			}
			s.roots = roots
		}
		for _, added := range p.Event.Added {
			path, err := filePath(added.URI)
			if err == nil {
				exists := false
				for _, root := range s.roots {
					if root == path {
						exists = true
					}
				}
				if !exists {
					s.roots = append(s.roots, path)
				}
			}
		}
		s.epoch++
		for uri := range s.buffers {
			if err := s.publish(uri); err != nil {
				return false, 0, err
			}
		}
		return false, 0, nil
	case "textDocument/didSave", "workspace/didChangeWatchedFiles", "workspace/didChangeConfiguration":
		if m.Method == "workspace/didChangeConfiguration" {
			var p struct {
				Settings struct {
					TargetVersion string `json:"targetVersion"`
				} `json:"settings"`
			}
			if err := json.Unmarshal(m.Params, &p); err != nil {
				return false, 0, s.failure(m.ID, -32602, err.Error())
			}
			s.override = p.Settings.TargetVersion
		}
		s.epoch++
		if m.Method == "textDocument/didSave" {
			var p struct {
				TextDocument struct {
					URI string `json:"uri"`
				} `json:"textDocument"`
			}
			if json.Unmarshal(m.Params, &p) == nil {
				return false, 0, s.publish(p.TextDocument.URI)
			}
		}
		if m.Method == "workspace/didChangeConfiguration" {
			for uri := range s.buffers {
				if err := s.publish(uri); err != nil {
					return false, 0, err
				}
			}
		}
		return false, 0, nil
	default:
		return false, 0, s.failure(m.ID, -32601, "method not implemented")
	}
}
func (s *session) context() context.Context {
	if s.ctx != nil {
		return s.ctx
	}
	return context.Background()
}

func scopeWorkspace(w workspace.Workspace, target string) workspace.Workspace {
	selected := make([]workspace.Document, 0)
	for _, d := range w.Documents {
		if d.Target.ID == target {
			selected = append(selected, d)
		}
	}
	w.Documents = selected
	return w
}

func analysisStatus(code, reason string) map[string]any {
	message := "Analysis did not complete: " + reason + ". Correct the input or configuration to retry."
	if strings.Contains(reason, "deadline exceeded") {
		message = "Analysis timed out: " + reason + ". Save the document to retry; check the language server log if this persists."
	}
	return map[string]any{"range": analysis.Range{}, "code": code, "source": "argo-workflows", "severity": 2, "message": message}
}
