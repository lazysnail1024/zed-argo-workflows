// Package lsp owns stdio framing and adapts standard LSP messages to immutable inputs.
package lsp

import (
	"bufio"
	"context"
	"crypto/sha256"
	"encoding/hex"
	"encoding/json"
	"fmt"
	"github.com/lazysnail1024/k8s-crd-toolkit/source"
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
type externalResult struct {
	diagnostics []analysis.Diagnostic
	completion  map[source.URI]analysis.ExternalCompletion
}
type session struct {
	acquiredInputs  map[workspaceKey]string
	usedInputs      map[workspaceKey]bool
	queryResults    map[string]json.RawMessage
	queryBytes      int
	queryKey        string
	externalFacts   *analysis.ExternalFacts
	externalResults map[string]externalResult
	facts           *analysis.Facts
	factInput       *workspace.Workspace
	factBoundary    string
	factMixed       bool
	workspaces      map[workspaceKey]workspace.Workspace
	ctx             context.Context
	pending         map[string]bool
	out             io.Writer
	log             io.Writer
	roots           []string
	override        string
	buffers         map[string]buffer
	bufferBytes     int
	initialized     bool
	shutdown        bool
	epoch           uint64
	versionedEdits  bool
	actions         map[string]pendingAction
	actionSequence  uint64
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

func (s *session) workspace(root, selected string) (workspace.Workspace, error) {
	if s.usedInputs != nil {
		// Each publication records its own dependencies, including cache hits.
		s.usedInputs[workspaceKey{root, selected}] = true
	}
	if w, ok := s.workspaces[workspaceKey{root, selected}]; ok && s.context().Err() == nil {
		return w, nil
	}
	uri, err := fileuri.FromPath(filepath.Join(root, ".argo-workflows-ls.yaml"))
	if err != nil {
		return workspace.Workspace{}, err
	}
	var overlay *string
	if b, ok := s.buffers[uri]; ok {
		if b.unavailable != "" {
			return workspace.Workspace{}, fmt.Errorf("%s", b.unavailable)
		}
		text := b.snapshot.Text()
		overlay = &text
	}
	w, err := s.workspaceWithConfig(root, selected, overlay)
	if s.acquiredInputs != nil {
		if err != nil {
			s.acquiredInputs[workspaceKey{root, selected}] = "error:" + err.Error()
		} else {
			encoded, _ := json.Marshal(struct {
				Content    string
				Config     workspace.Config
				Targets    []workspace.Target
				Incomplete map[string]bool
				Notes      []string
			}{fingerprint(w), w.Config, w.Targets, w.IncompleteScopes, w.NotEvaluated})
			sum := sha256.Sum256(encoded)
			s.acquiredInputs[workspaceKey{root, selected}] = hex.EncodeToString(sum[:])
		}
	}
	if err == nil && s.context().Err() == nil && s.workspaces != nil {
		s.workspaces[workspaceKey{root, selected}] = w
		boundary := analysis.FactBoundary(w)
		if boundary == "" || s.factBoundary != "" && s.factBoundary != boundary {
			s.factMixed = true
			s.factInput = nil
		}
		if !s.factMixed && s.factInput == nil {
			analysis.RestoreFacts(s.context(), s.facts, w)
			s.factBoundary = boundary
			s.factInput = &w
		}
	}
	return w, err
}

func (s *session) workspaceWithConfig(root, selected string, overlay *string) (workspace.Workspace, error) {
	uri, err := fileuri.FromPath(filepath.Join(root, ".argo-workflows-ls.yaml"))
	if err != nil {
		return workspace.Workspace{}, err
	}
	buffers := map[string]source.Snapshot{}
	for key, b := range s.buffers {
		if s.root(key) == root {
			if b.unavailable != "" {
				empty, _ := source.New(source.URI(key), source.Revision(b.version), "")
				buffers[key] = empty
			} else {
				buffers[key] = b.snapshot
			}
		}
	}
	w, err := workspace.LoadEditor(s.context(), root, selected, s.override, overlay, s.roots, buffers)
	if err != nil {
		return w, err
	}
	seen := map[string]bool{}
	for i, d := range w.Documents {
		uri := string(d.Snapshot.URI())
		seen[uri] = true
		if b, ok := s.buffers[uri]; ok && b.unavailable == "" {
			w.Documents[i].Snapshot = b.snapshot
		}
	}
	for other, b := range s.buffers {
		if other != uri && !seen[other] && s.root(other) == root {
			name, _ := filePath(other)
			if workspace.HelmTemplate(root, name) {
				continue
			}
			snapshot := b.snapshot
			if b.unavailable != "" {
				snapshot, _ = source.New(source.URI(other), source.Revision(b.version), "")
			}
			d, err := workspace.OpenDocument(w, snapshot, s.override)
			if err != nil {
				return w, err
			}
			if selected == "" || d.Target.Scope == selected {
				w.Documents = append(w.Documents, d)
			}
		}
	}
	for uri, b := range s.buffers {
		if b.unavailable != "" && s.root(uri) == root {
			if d, err := s.bufferDocument(uri); err == nil && (selected == "" || d.Target.Scope == selected) {
				w.IncompleteScopes[d.Target.ID] = true
				w.NotEvaluated = append(w.NotEvaluated, uri+": "+b.unavailable)
			}
		}
	}
	return w, nil
}
func (s *session) publishAffected(uri string) error {
	root := s.root(uri)
	name, _ := filePath(uri)
	isConfig := filepath.Base(name) == ".argo-workflows-ls.yaml"
	if isConfig {
		if err := s.publish(uri); err != nil {
			return err
		}
	}
	changed, changedErr := s.bufferDocument(uri)
	for other := range s.buffers {
		otherName, _ := filePath(other)
		if s.root(other) == root && filepath.Base(otherName) != ".argo-workflows-ls.yaml" {
			if !isConfig && changedErr == nil {
				otherDocument, err := s.bufferDocument(other)
				if err == nil && otherDocument.Target.ID != changed.Target.ID {
					continue
				}
			}
			if err := s.publish(other); err != nil {
				return err
			}
		}
	}
	if _, opened := s.buffers[uri]; !opened && !isConfig {
		return s.publish(uri)
	}
	return nil
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
	selected := ""
	if target, err := s.bufferDocument(uri); err == nil && !strings.HasPrefix(target.Target.Scope, "opened:") {
		selected = target.Target.Scope
	}
	input, err := s.workspace(root, selected)
	if err != nil {
		fmt.Fprintln(s.log, "workspace incomplete:", err)
		params["diagnostics"] = []map[string]any{analysisStatus("WF_ANALYSIS_UNAVAILABLE", err.Error())}
		return s.write(map[string]any{"jsonrpc": "2.0", "method": "textDocument/publishDiagnostics", "params": params})
	}
	var document *workspace.Document
	for _, d := range input.Documents {
		if string(d.Snapshot.URI()) == uri {
			copy := d
			document = &copy
			break
		}
	}
	if opened {
		if document == nil {
			d, err := workspace.OpenDocument(input, b.snapshot, s.override)
			if err != nil {
				fmt.Fprintln(s.log, "buffer target unresolved:", err)
			} else {
				document = &d
			}
		} else {
			document.Snapshot = b.snapshot
		}
	}
	if document != nil {
		if !compat.Accepted(document.Target.Version) {
			diagnostics = append(diagnostics, map[string]any{"range": analysis.Range{}, "code": "WF_EXPERIMENTAL_TARGET", "source": "argo-workflows", "severity": 2, "message": compat.ExperimentalReason(document.Target.Version)})
		}
		ctx, cancel := context.WithTimeout(analysis.WithLogicalResult(s.context()), 5*time.Second)
		result := analysis.AnalyzeSchema(ctx, document.Snapshot, document.Target.Version)
		external, cached := s.externalResults[document.Target.ID]
		if !cached {
			external.diagnostics, external.completion = analysis.ExternalDiagnostics(ctx, scopeWorkspace(input, document.Target.ID))
			if ctx.Err() == nil && s.externalResults != nil {
				s.externalResults[document.Target.ID] = external
			}
		}
		externalDiagnostics, completion := external.diagnostics, external.completion
		if !completion[document.Snapshot.URI()].Completed {
			result.NotEvaluated = append(result.NotEvaluated, "external reference analysis incomplete")
		}
		if input.IncompleteScopes[document.Target.ID] {
			result.NotEvaluated = append(result.NotEvaluated, "scope input is incomplete")
		}
		result.NotEvaluated = append(result.NotEvaluated, input.NotEvaluated...)
		for _, d := range externalDiagnostics {
			if d.URI == uri {
				result.Diagnostics = append(result.Diagnostics, d)
			}
		}
		cancel()
		for _, d := range result.Diagnostics {
			severity := 1
			switch d.Severity {
			case "Warning":
				severity = 2
			case "Information":
				severity = 3
			case "Hint":
				severity = 4
			}
			diagnostics = append(diagnostics, map[string]any{"range": d.Range, "code": d.Code, "source": d.Source, "severity": severity, "message": d.Message})
		}
		if len(result.NotEvaluated) > 0 {
			diagnostics = append(diagnostics, analysisStatus("WF_ANALYSIS_INCOMPLETE", strings.Join(result.NotEvaluated, "; ")))
		}
		for _, note := range result.NotEvaluated {
			fmt.Fprintln(s.log, "analysis incomplete:", note)
		}
	}
	params["diagnostics"] = diagnostics
	return s.write(map[string]any{"jsonrpc": "2.0", "method": "textDocument/publishDiagnostics", "params": params})
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
	w, err := s.workspace(root, "")
	if err != nil {
		return workspace.Document{}, err
	}
	for _, d := range w.Documents {
		if string(d.Snapshot.URI()) == uri {
			return d, nil
		}
	}
	return workspace.Document{}, fmt.Errorf("document not available")
}

// Check retained text before constructing a snapshot. Refused updates retain
// identity/version only for safe recovery; queries never fall back to old or disk data.
func (s *session) bufferBudget(uri string, size int) string {
	limit := 5 * 1024 * 1024
	name, _ := filePath(uri)
	if filepath.Base(name) == ".argo-workflows-ls.yaml" {
		limit = 1024 * 1024
	}
	if size > limit {
		return fmt.Sprintf("buffer exceeds %d MiB", limit/(1024*1024))
	}
	if size > workspace.MaxSnapshotBytes-(s.bufferBytes-len(s.buffers[uri].snapshot.Text())) {
		return "retained open-buffer text exceeds 128 MiB"
	}
	return ""
}
func (s *session) refuseBuffer(uri string, version int32, reason string) error {
	s.epoch++
	b, ok := s.buffers[uri]
	if !ok {
		b.snapshot, _ = source.New(source.URI(uri), source.Revision(s.epoch), "")
	}
	b.version = version
	b.unavailable = reason
	s.buffers[uri] = b
	fmt.Fprintln(s.log, reason)
	return s.publishAffected(uri)
}

// appendWorkspaceSymbols reuses the CLI's bounded immutable CST preparse.
// Domain evaluation, expansion admission and result ordering remain sequential
// in this request's invocation; workers never share mutable analysis state.
func appendWorkspaceSymbols(ctx context.Context, result []map[string]any, documents []workspace.Document, query string) []map[string]any {
	analysis.VisitPreparsedDocuments(ctx, documents, func(d workspace.Document) {
		for _, symbol := range analysis.DomainSymbols(ctx, d.Snapshot, d.Target.Version) {
			if strings.Contains(symbol.Name, query) {
				result = append(result, map[string]any{"name": symbol.Name, "kind": symbol.Kind, "containerName": symbol.Container, "location": symbol.Location})
			}
		}
	})
	return result
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
		}
		if json.Unmarshal(m.Params, &p) == nil {
			uri := string(p.TextDocument.URI)
			if d, err := s.queryDocument(uri); err == nil {
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
		result := []map[string]any{}
		for _, root := range s.roots {
			w, err := s.workspace(root, "")
			if err != nil {
				return false, 0, s.failure(m.ID, -32803, err.Error())
			}
			result = appendWorkspaceSymbols(s.context(), result, w.Documents, p.Query)
		}
		if s.context().Err() != nil {
			return false, 0, s.failure(m.ID, -32800, "request timed out or cancelled")
		}
		if err := analysis.ExpansionError(s.context()); err != nil {
			return false, 0, s.failure(m.ID, -32803, err.Error())
		}
		return false, 0, s.reply(m.ID, result)
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
				external, externalErr := analysis.CompleteExternalNames(ctx, scopeWorkspace(w, document.Target.ID), uri, pos)
				if externalErr != nil {
					err = externalErr
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
		snapshot, err := source.New(source.URI(uri), source.Revision(s.epoch), p.TextDocument.Text)
		if err != nil {
			return false, 0, err
		}
		s.bufferBytes += len(snapshot.Text()) - len(s.buffers[uri].snapshot.Text())
		s.buffers[uri] = buffer{snapshot: snapshot, version: p.TextDocument.Version}
		return false, 0, s.publishAffected(uri)
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
		snapshot := b.snapshot
		available := b.unavailable == ""
		for _, change := range p.ContentChanges {
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
			s.epoch++
			next, err := snapshot.Apply(source.Revision(s.epoch), []source.Change{{Range: span, Text: text}})
			if err != nil {
				fmt.Fprintln(s.log, err)
				return false, 0, nil
			}
			snapshot = next
		}
		s.bufferBytes += len(snapshot.Text()) - len(b.snapshot.Text())
		s.buffers[uri] = buffer{snapshot: snapshot, version: p.TextDocument.Version}
		return false, 0, s.publishAffected(uri)
	case "textDocument/didClose":
		var p protocol.DidCloseTextDocumentParams
		if err := json.Unmarshal(m.Params, &p); err != nil {
			return false, 0, nil
		}
		uri := string(p.TextDocument.URI)
		s.bufferBytes -= len(s.buffers[uri].snapshot.Text())
		delete(s.buffers, uri)
		for token, action := range s.actions {
			if action.uri == uri {
				delete(s.actions, token)
			}
		}
		s.epoch++
		return false, 0, s.publishAffected(uri)
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
			for key := range s.acquiredInputs {
				if key.root == path {
					delete(s.acquiredInputs, key)
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
		for uri := range s.buffers {
			if err := s.publish(uri); err != nil {
				return false, 0, err
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
	return map[string]any{"range": analysis.Range{}, "code": code, "source": "argo-workflows", "severity": 2, "message": "Analysis did not complete: " + reason + ". Correct the input or configuration to retry."}
}
