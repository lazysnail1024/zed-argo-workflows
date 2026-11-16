package lsp

import (
	"context"
	"crypto/sha256"
	"encoding/json"
	"errors"
	"fmt"
	"path/filepath"
	"strings"

	"github.com/lazysnail1024/k8s-crd-toolkit/index"
	"github.com/lazysnail1024/k8s-crd-toolkit/source"
	"github.com/lazysnail1024/zed-argo-workflows/internal/analysis"
	"github.com/lazysnail1024/zed-argo-workflows/internal/helmsource"
	"github.com/lazysnail1024/zed-argo-workflows/internal/workspace"
)

const referenceCandidateCommand = "argo-workflows.openReferenceCandidate"
const referenceCandidateActionKind = "source.openReferenceCandidate"
const referenceCandidateRequestPrefix = "argo-workflows-candidate:"

type referenceCandidateTarget struct {
	Key      string            `json:"key"`
	Symbol   string            `json:"symbol"`
	Revision source.Revision   `json:"revision"`
	SHA256   string            `json:"sha256"`
	Location analysis.Location `json:"location"`
}

type referenceCandidateArgument struct {
	explanationArgument
	Revision          source.Revision          `json:"revision"`
	Root              string                   `json:"root"`
	Scope             string                   `json:"scope"`
	ConfigurationHash string                   `json:"configurationHash"`
	Target            referenceCandidateTarget `json:"target"`
}

type referenceCandidateSet struct {
	explanation *analysis.ReferenceCandidateExplanation
	targets     []referenceCandidateTarget
}

func candidateConfigurationHash(w workspace.Workspace, override string) string {
	return fmt.Sprintf("%x", sha256.Sum256([]byte(catalogIdentity(w, override))))
}

func sourceActionAllowed(only []string, kind string) bool {
	if len(only) == 0 {
		return true
	}
	for _, requested := range only {
		if requested == "" || requested == kind || strings.HasPrefix(kind, requested+".") {
			return true
		}
	}
	return false
}

// Candidate input remains in the ordinary YAML path and uses the current
// catalog configuration. It never broadens a root or an isolated opened scope.
func (s *session) referenceCandidateInput(uri string) (workspace.Document, workspace.Workspace, error) {
	b, opened := s.buffers[uri]
	name, _ := filePath(uri)
	if !opened || b.unavailable != "" || s.root(uri) == "" || filepath.Base(name) == ".argo-workflows-ls.yaml" {
		return workspace.Document{}, workspace.Workspace{}, analysis.Refuse("STALE_INPUT", "resource candidate source is unavailable")
	}
	if workspace.HelmTemplate(s.root(uri), name) && helmsource.HasActions(b.snapshot.Text()) {
		return workspace.Document{}, workspace.Workspace{}, analysis.Refuse("UNMODELED_BINDING", "resource candidates require ordinary YAML")
	}
	document, err := s.queryDocument(uri)
	if err != nil {
		return document, workspace.Workspace{}, err
	}
	s.catalog.mu.Lock()
	defer s.catalog.mu.Unlock()
	root := s.catalog.roots[s.root(uri)]
	if root == nil || root.problem != "" {
		return document, workspace.Workspace{}, analysis.Refuse("ANALYSIS_UNAVAILABLE", "resource candidate configuration is unavailable")
	}
	header := root.header
	header.Documents = []workspace.Document{document}
	s.proofs = append(s.proofs, catalogProof{root: s.root(uri), epoch: root.epoch})
	return document, header, nil
}

// Only displayed rows acquire source text, at most twenty. Explanation can
// supply already acquired snapshots; other rows use the same bounded catalog
// reader and proof as normal queries, including unsaved target buffers.
func (s *session) collectReferenceCandidates(document workspace.Document, w workspace.Workspace, position *analysis.Position) (*referenceCandidateSet, error) {
	if position == nil {
		return nil, nil
	}
	// Capture completeness before traversal. A later generation must not be
	// attached to an earlier candidate list and certify a missing new row.
	root := s.root(string(document.Snapshot.URI()))
	ready, proof := s.catalog.explanationReadiness(root, document.Target.ID)
	pos := source.Position{Line: position.Line, UTF16Column: position.Character}
	status, rows, err := analysis.QueryReferenceCandidates(s.context(), document, w.Config, pos, s.catalog.index)
	if err != nil || status == nil {
		return nil, err
	}
	s.proofs = append(s.proofs, proof)
	status.ScopeComplete = ready && !w.IncompleteScopes[document.Target.ID]
	status.Complete = status.Complete && status.ScopeComplete
	result := &referenceCandidateSet{explanation: status}
	documents := map[source.URI]workspace.Document{}
	for _, input := range w.Documents {
		documents[input.Snapshot.URI()] = input
	}
	for _, row := range rows {
		candidate, err := s.referenceCandidateDocument(row, document.Target.ID, documents)
		if err != nil {
			return nil, err
		}
		item, exact, err := analysis.ReferenceCandidateLocation(s.context(), candidate, w.Config, row)
		if err != nil {
			return nil, err
		}
		if !exact {
			status.Complete = false
			continue
		}
		status.Items = append(status.Items, item)
		result.targets = append(result.targets, referenceCandidateTarget{Key: string(row.Symbol.Key), Symbol: string(row.Symbol.ID),
			Revision: row.Revision, SHA256: fmt.Sprintf("%x", sha256.Sum256([]byte(candidate.Snapshot.Text()))), Location: item.Location})
	}
	proof, current := s.catalog.candidateProof(root, rows)
	if !current {
		return nil, analysis.Refuse("STALE_INPUT", "resource candidates changed during inspection")
	}
	s.proofs = append(s.proofs, proof)
	return result, s.context().Err()
}

func (s *session) referenceCandidateDocument(row index.Definition, scope string, documents map[source.URI]workspace.Document) (workspace.Document, error) {
	document, found := documents[row.URI]
	if !found {
		current, proof, err := s.catalog.document(s.context(), string(row.URI))
		if err != nil {
			return document, err
		}
		s.proofs = append(s.proofs, proof)
		document = current
		documents[row.URI] = document
	}
	if document.Target.ID != scope || document.Snapshot.Revision() != row.Revision {
		return document, analysis.Refuse("STALE_INPUT", "resource candidate source or scope changed")
	}
	return document, nil
}

func candidateDescription(root string, item analysis.ReferenceCandidate) string {
	namespace := item.Namespace
	if namespace == "" {
		namespace = "unknown"
	}
	file := item.Location.URI
	if name, err := filePath(file); err == nil {
		if relative, err := filepath.Rel(root, name); err == nil {
			file = filepath.ToSlash(relative)
		}
	}
	return fmt.Sprintf("%s %s · namespace: %s · %s:%d", item.Kind, item.Name, namespace, file, item.Location.Range.Start.Line+1)
}

func (s *session) withReferenceCandidateActions(uri string, actions []map[string]any) ([]map[string]any, error) {
	if !s.candidateActionAllowed || s.explainPosition == nil {
		return actions, nil
	}
	document, header, err := s.referenceCandidateInput(uri)
	if err != nil {
		return actions, nil // Unavailable/non-YAML inputs have no candidate action.
	}
	result, err := s.collectReferenceCandidates(document, header, s.explainPosition)
	if err != nil || result == nil {
		return actions, err
	}
	b := s.buffers[uri]
	for i, item := range result.explanation.Items {
		title := "Argo Workflows: open candidate " + candidateDescription(s.root(uri), item) + " (binding unproven)"
		argument := referenceCandidateArgument{
			explanationArgument: explanationArgument{URI: uri, Version: b.version, Epoch: s.epoch, RootEpoch: s.explanationRootEpoch(uri), Position: s.explainPosition},
			Revision:            b.snapshot.Revision(), Root: s.root(uri), Scope: document.Target.ID,
			ConfigurationHash: candidateConfigurationHash(header, s.override), Target: result.targets[i],
		}
		actions = append(actions, map[string]any{"title": title, "kind": referenceCandidateActionKind, "command": map[string]any{
			"title": title, "command": referenceCandidateCommand, "arguments": []referenceCandidateArgument{argument},
		}})
	}
	return actions, nil
}

// Recompute candidates instead of retaining a second action/catalog state.
// The argument selects one current row; it cannot introduce an arbitrary URI.
func (s *session) currentReferenceCandidate(arg referenceCandidateArgument) (analysis.ReferenceCandidate, error) {
	b, opened := s.buffers[arg.URI]
	if !opened || arg.Position == nil || b.version != arg.Version || b.snapshot.Revision() != arg.Revision ||
		s.epoch != arg.Epoch || s.root(arg.URI) != arg.Root || s.explanationRootEpoch(arg.URI) != arg.RootEpoch {
		return analysis.ReferenceCandidate{}, analysis.Refuse("STALE_INPUT", "resource candidate action input changed")
	}
	document, header, err := s.referenceCandidateInput(arg.URI)
	if err != nil {
		return analysis.ReferenceCandidate{}, err
	}
	if document.Target.ID != arg.Scope || candidateConfigurationHash(header, s.override) != arg.ConfigurationHash {
		return analysis.ReferenceCandidate{}, analysis.Refuse("STALE_INPUT", "resource candidate configuration changed")
	}
	configuration := &editConfiguration{arg.Root, s.override, catalogIdentity(header, s.override)}
	if err := s.checkEditConfiguration(s.context(), configuration); err != nil {
		return analysis.ReferenceCandidate{}, err
	}
	result, err := s.collectReferenceCandidates(document, header, arg.Position)
	if err != nil {
		return analysis.ReferenceCandidate{}, err
	}
	if result != nil {
		for i, target := range result.targets {
			if target == arg.Target {
				// The owner rechecks this configuration and all source proofs
				// before publishing the response or a showDocument request.
				s.editConfiguration = configuration
				return result.explanation.Items[i], nil
			}
		}
	}
	return analysis.ReferenceCandidate{}, analysis.Refuse("STALE_INPUT", "resource candidate is no longer present at its original location")
}

func (s *session) referenceCandidateFailure(id json.RawMessage, err error) error {
	if errors.Is(err, context.Canceled) || errors.Is(err, context.DeadlineExceeded) {
		return s.failure(id, -32800, err.Error())
	}
	var refusal *analysis.Refusal
	if errors.Is(err, index.ErrQueryChanged) || errors.As(err, &refusal) && refusal.ReasonCode == "STALE_INPUT" {
		return s.failure(id, -32801, err.Error())
	}
	return s.failureError(id, -32803, referenceCandidateCommand, err)
}

func (s *session) referenceCandidateRequest(m message) (bool, error) {
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
	var identity struct{ Command string }
	if json.Unmarshal(params, &identity) != nil || identity.Command != referenceCandidateCommand {
		return false, nil
	}
	var command struct {
		Arguments []referenceCandidateArgument `json:"arguments"`
	}
	if json.Unmarshal(params, &command) != nil || len(command.Arguments) != 1 || command.Arguments[0].Position == nil {
		return true, s.failure(m.ID, -32602, "invalid resource candidate arguments")
	}
	item, err := s.currentReferenceCandidate(command.Arguments[0])
	if err != nil {
		return true, s.referenceCandidateFailure(m.ID, err)
	}
	if m.Method == "codeAction/resolve" {
		return true, s.reply(m.ID, json.RawMessage(m.Params))
	}
	message := "Resource candidate: " + candidateDescription(command.Arguments[0].Root, item) + ". Binding unproven: " + item.Message
	if !s.showDocument {
		message += " This client does not support showDocument; open the returned location manually."
	}
	if err := s.reply(m.ID, map[string]any{"location": item.Location, "message": message}); err != nil {
		return true, err
	}
	if err := s.write(map[string]any{"jsonrpc": "2.0", "method": "window/showMessage", "params": map[string]any{"type": 3, "message": message}}); err != nil {
		return true, err
	}
	if s.showDocument {
		return true, s.write(map[string]any{"jsonrpc": "2.0", "id": referenceCandidateRequestPrefix + string(m.ID), "method": "window/showDocument",
			"params": map[string]any{"uri": item.Location.URI, "selection": item.Location.Range, "external": false, "takeFocus": true}})
	}
	return true, nil
}
