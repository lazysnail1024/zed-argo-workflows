package lsp

import (
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"path/filepath"
	"strings"

	"github.com/lazysnail1024/k8s-crd-toolkit/edit"
	"github.com/lazysnail1024/k8s-crd-toolkit/index"
	"github.com/lazysnail1024/k8s-crd-toolkit/source"
	"github.com/lazysnail1024/zed-argo-workflows/compat"
	"github.com/lazysnail1024/zed-argo-workflows/internal/analysis"
	"github.com/lazysnail1024/zed-argo-workflows/internal/helmsource"
	"github.com/lazysnail1024/zed-argo-workflows/internal/workspace"
)

const explainCommand = "argo-workflows.explainAnalysis"

type explanationArgument struct {
	URI       string             `json:"uri"`
	Version   int32              `json:"version"`
	Epoch     uint64             `json:"epoch"`
	RootEpoch uint64             `json:"rootEpoch"`
	Position  *analysis.Position `json:"position,omitempty"`
}

func operationRefusal(operation string, err error) analysis.OperationAvailability {
	var refusal *analysis.Refusal
	if !errors.As(err, &refusal) {
		refusal = analysis.Refuse("SAFETY_UNPROVEN", err.Error())
	}
	return analysis.OperationAvailability{Operation: operation, Allowed: false, Refusal: refusal}
}

func (s *session) failureError(id json.RawMessage, code int, operation string, err error) error {
	if len(id) == 0 {
		return nil
	}
	data := operationRefusal(operation, err)
	return s.write(map[string]any{"jsonrpc": "2.0", "id": id, "error": map[string]any{"code": code, "message": err.Error(), "data": data}})
}

// prepareRenameSelection is shared by the actual LSP operation and its read-only
// explanation. An explanation never substitutes for the operation's fresh proof.
func (s *session) prepareRenameSelection(w workspace.Workspace, d workspace.Document, pos source.Position, resourceName bool) (any, error) {
	result, files, err := proveRenameSelection(s.context(), w, d, pos, resourceName)
	if err == nil && resourceName {
		err = s.requireResourceBuffers(w, files)
	}
	return result, err
}

// The binding proof is independent of edit capability and buffer-open policy.
// Both navigation certainty and edits need its domain facts; only edits require
// client edit support and every affected buffer to be open.
func proveRenameSelection(ctx context.Context, w workspace.Workspace, d workspace.Document, pos source.Position, resourceName bool) (any, []source.URI, error) {
	if err := analysis.SafeScopePlan(ctx, w, d, edit.Plan{BaseRevision: d.Snapshot.Revision()}); err != nil {
		return nil, nil, err
	}
	if !resourceName {
		result, err := analysis.PrepareRename(ctx, d.Snapshot, d.Target.Version, pos)
		return result, nil, err
	}
	return analysis.PrepareResourceRename(ctx, w, string(d.Snapshot.URI()), pos)
}

func (s *session) explainAnalysis(uri string, position *analysis.Position) (analysis.AnalysisExplanation, error) {
	b, opened := s.buffers[uri]
	if !opened {
		return analysis.AnalysisExplanation{}, analysis.Refuse("AFFECTED_FILE_NOT_OPEN", "analysis explanation requires an open document", analysis.Location{URI: uri})
	}
	if b.unavailable != "" {
		status := s.explainUnavailable(uri, b, analysis.Refuse("ANALYSIS_UNAVAILABLE", b.unavailable))
		status.InputIdentity.SHA256, status.InputIdentity.Revision = "", 0
		return status, nil
	}
	name, _ := filePath(uri)
	if workspace.HelmTemplate(s.root(uri), name) && helmsource.HasActions(b.snapshot.Text()) {
		return s.explainUnavailable(uri, b, analysis.Refuse("DYNAMIC_REFERENCE", "Helm source requires the explicit render and check saved chart action before rendered-result analysis.")), nil
	}
	d, err := s.bufferDocument(uri)
	if err != nil {
		return s.explainUnavailable(uri, b, analysis.Refuse("ANALYSIS_UNAVAILABLE", err.Error())), nil
	}
	complete, readinessProof := s.catalog.explanationReadiness(s.root(uri), d.Target.ID)
	s.proofs = append(s.proofs, readinessProof)
	status := analysis.ExplainDocument(s.context(), d, "buffer", &b.version, complete)
	status.Roots = append([]string(nil), s.roots...)
	if !complete && status.Completeness.Document {
		status.Phase = s.explanationPhase(uri)
	}
	if position == nil {
		return status, nil
	}
	status.References = &analysis.ReferenceStatus{Complete: false}
	if !complete {
		status.References.Refusal = analysis.Refuse("INDEX_INCOMPLETE", "reference search requires the complete selected scope")
		status.Operations["rename"] = operationRefusal("rename", analysis.Refuse("INDEX_INCOMPLETE", "rename requires the complete selected scope"))
		return status, nil
	}
	w, err := s.resourceEditWorkspace(d)
	if err != nil {
		status.Completeness.Workspace = false
		status.Phase = "incomplete"
		status.Operations["rename"] = operationRefusal("rename", analysis.Refuse("INDEX_INCOMPLETE", err.Error()))
		return status, nil
	}
	if w.IncompleteScopes[d.Target.ID] {
		status.Completeness.Workspace = false
		status.Phase = "indexing"
	}
	pos := source.Position{Line: position.Line, UTF16Column: position.Character}
	status.References.Count = len(analysis.ScopeReferences(s.context(), w, uri, pos, false))
	status.References.Complete = status.Completeness.Document && status.Completeness.Workspace
	resource, proofErr := analysis.ResourceRenameAt(s.context(), d.Snapshot, pos)
	var files []source.URI
	if proofErr == nil {
		_, files, proofErr = proveRenameSelection(s.context(), w, d, pos, resource)
	}
	if proofErr != nil {
		status.References.Complete = false
		status.References.Refusal = operationRefusal("references", proofErr).Refusal
	}
	status.Operations["rename"] = s.renameAvailability(w, d, files, proofErr)
	return status, nil
}

func (c *catalog) explanationReadiness(rootName, target string) (bool, catalogProof) {
	c.mu.Lock()
	defer c.mu.Unlock()
	root := c.roots[rootName]
	proof := catalogProof{root: rootName, states: map[index.Scope]uint64{}}
	if root == nil {
		return false, proof
	}
	proof.epoch = root.epoch
	ready := c.scopeReadyLocked(root, target)
	if ready {
		proof.states[index.Scope(target)] = c.scopeGeneration(root, target)
	}
	return ready, proof
}

func (s *session) explainUnavailable(uri string, b buffer, reason *analysis.Refusal) analysis.AnalysisExplanation {
	return analysis.AnalysisExplanation{Root: s.root(uri), Roots: append([]string(nil), s.roots...), Scope: "unknown",
		InputIdentity: analysis.ExplainInput(b.snapshot, "buffer", &b.version, ""), Phase: "unavailable",
		Reasons: []*analysis.Refusal{reason}, References: &analysis.ReferenceStatus{},
		Operations: map[string]analysis.OperationAvailability{"rename": operationRefusal("rename", reason)}}
}

func (s *session) explanationPhase(uri string) string {
	s.catalog.mu.Lock()
	defer s.catalog.mu.Unlock()
	r := s.catalog.roots[s.root(uri)]
	if r == nil || r.problem != "" {
		return "configuration"
	}
	if !r.done {
		return "discovering"
	}
	if r.pending != 0 {
		return "indexing"
	}
	return "limited"
}

func (s *session) renameAvailability(w workspace.Workspace, d workspace.Document, files []source.URI, proofErr error) analysis.OperationAvailability {
	if !s.versionedEdits {
		return operationRefusal("rename", analysis.Refuse("CLIENT_CAPABILITY_MISSING", "client does not support versioned documentChanges"))
	}
	if !compat.Accepted(d.Target.Version) {
		return operationRefusal("rename", analysis.Refuse("TARGET_UNKNOWN", "experimental target edits are disabled"))
	}
	if proofErr != nil {
		return operationRefusal("rename", proofErr)
	}
	if err := s.checkResourceBuffers(w, files); err != nil {
		return operationRefusal("rename", err)
	}
	return analysis.OperationAvailability{Operation: "rename", Allowed: true}
}

func (s *session) withExplanationAction(uri string, actions []map[string]any) []map[string]any {
	b, opened := s.buffers[uri]
	name, _ := filePath(uri)
	if !s.explainActionAllowed || !opened || s.root(uri) == "" || filepath.Base(name) == ".argo-workflows-ls.yaml" {
		return actions
	}
	rootEpoch := s.explanationRootEpoch(uri)
	title := "Argo Workflows: explain current analysis"
	return append(actions, map[string]any{"title": title, "kind": "source.explainAnalysis", "command": map[string]any{
		"title": title, "command": explainCommand, "arguments": []explanationArgument{{URI: uri, Version: b.version, Epoch: s.epoch, RootEpoch: rootEpoch, Position: s.explainPosition}},
	}})
}

func (s *session) explanationRootEpoch(uri string) uint64 {
	s.catalog.mu.Lock()
	defer s.catalog.mu.Unlock()
	if root := s.catalog.roots[s.root(uri)]; root != nil {
		return root.epoch
	}
	return 0
}

func explanationMessage(status analysis.AnalysisExplanation) string {
	lines := []string{fmt.Sprintf("Argo Workflows analysis (%s input, version %v): root %s; scope %s; document complete: %t; workspace scope complete: %t.",
		status.InputIdentity.Source, *status.InputIdentity.Version, status.Root, status.Scope, status.Completeness.Document, status.Completeness.Workspace)}
	for _, reason := range status.Reasons {
		lines = append(lines, reason.ReasonCode+": "+reason.Message+" "+reason.NextStep)
		for _, location := range reason.RelatedLocations {
			lines = append(lines, "Related file: "+location.URI)
		}
	}
	if status.References != nil {
		lines = append(lines, fmt.Sprintf("References found: %d; complete static reference proof: %t.", status.References.Count, status.References.Complete))
		if status.References.Refusal != nil {
			lines = append(lines, status.References.ReasonCode+": "+status.References.Message)
		}
	}
	if operation, ok := status.Operations["rename"]; ok {
		if operation.Allowed {
			lines = append(lines, "Rename is available for the selected symbol; execution revalidates its inputs.")
		} else {
			lines = append(lines, operation.ReasonCode+": "+operation.Message+" "+operation.NextStep)
			for _, location := range operation.RelatedLocations {
				lines = append(lines, "Affected file: "+location.URI)
			}
		}
	}
	return strings.Join(lines, "\n")
}

func (s *session) explanationRequest(m message) (bool, error) {
	if m.Method == "textDocument/codeAction" {
		var p struct {
			Range   struct{ Start analysis.Position } `json:"range"`
			Context struct{ Only []string }           `json:"context"`
		}
		if json.Unmarshal(m.Params, &p) == nil {
			s.explainPosition = &p.Range.Start
			s.explainActionAllowed = len(p.Context.Only) == 0
			for _, kind := range p.Context.Only {
				s.explainActionAllowed = s.explainActionAllowed || kind == "source" || kind == "source.explainAnalysis"
			}
			if len(p.Context.Only) == 1 && p.Context.Only[0] == "source.explainAnalysis" {
				return true, s.reply(m.ID, []map[string]any{})
			}
		}
		if !s.versionedEdits {
			return true, s.reply(m.ID, s.withRenderAction(s.focus, []map[string]any{}))
		}
		return false, nil
	}
	if m.Method == "argo-workflows/explainAnalysis" {
		var p struct {
			TextDocument struct{ URI string } `json:"textDocument"`
			Position     *analysis.Position   `json:"position"`
		}
		if err := json.Unmarshal(m.Params, &p); err != nil {
			return true, s.failure(m.ID, -32602, err.Error())
		}
		status, err := s.explainAnalysis(p.TextDocument.URI, p.Position)
		if err != nil {
			return true, s.failureError(m.ID, -32803, "explainAnalysis", err)
		}
		return true, s.reply(m.ID, status)
	}
	if m.Method != "workspace/executeCommand" && m.Method != "codeAction/resolve" {
		return false, nil
	}
	params := m.Params
	if m.Method == "codeAction/resolve" {
		var action struct{ Command json.RawMessage }
		if json.Unmarshal(params, &action) != nil || len(action.Command) == 0 {
			return false, nil
		}
		params = action.Command
	}
	var command struct {
		Command   string                `json:"command"`
		Arguments []explanationArgument `json:"arguments"`
	}
	// Other commands may have unrelated argument types; inspect the name first.
	var identity struct{ Command string }
	if json.Unmarshal(params, &identity) != nil || identity.Command != explainCommand {
		return false, nil
	}
	if json.Unmarshal(params, &command) != nil || len(command.Arguments) != 1 {
		return true, s.failure(m.ID, -32602, "invalid explanation arguments")
	}
	arg := command.Arguments[0]
	b, opened := s.buffers[arg.URI]
	if !opened || b.version != arg.Version || s.epoch != arg.Epoch || s.explanationRootEpoch(arg.URI) != arg.RootEpoch {
		return true, s.failureError(m.ID, -32801, "explainAnalysis", analysis.Refuse("STALE_INPUT", "analysis explanation input changed"))
	}
	if m.Method == "codeAction/resolve" {
		return true, s.reply(m.ID, json.RawMessage(m.Params))
	}
	status, err := s.explainAnalysis(arg.URI, arg.Position)
	if err != nil {
		return true, s.failureError(m.ID, -32803, "explainAnalysis", err)
	}
	if err := s.reply(m.ID, status); err != nil {
		return true, err
	}
	return true, s.write(map[string]any{"jsonrpc": "2.0", "method": "window/showMessage", "params": map[string]any{"type": 3, "message": explanationMessage(status)}})
}
