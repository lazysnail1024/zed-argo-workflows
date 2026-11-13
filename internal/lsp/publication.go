package lsp

import (
	"context"
	"fmt"
	"path/filepath"
	"strings"
	"time"

	"github.com/lazysnail1024/zed-argo-workflows/compat"
	"github.com/lazysnail1024/zed-argo-workflows/internal/analysis"
	"github.com/lazysnail1024/zed-argo-workflows/internal/helmsource"
	"github.com/lazysnail1024/zed-argo-workflows/internal/workspace"
)

// Run on the background snapshot, before any workspace acquisition. Unknown
// identities are logged, not diagnosed as Workflows input errors. The generic
// YAML server owns syntax errors in files we cannot identify.
func (s *session) ownsPublication(uri string) bool {
	b, opened := s.buffers[uri]
	root := s.root(uri)
	if !opened || root == "" {
		return false
	}
	name, err := filePath(uri)
	if err != nil {
		return false
	}
	if filepath.Base(name) == ".argo-workflows-ls.yaml" {
		return true
	}
	if workspace.HelmTemplate(root, name) && helmsource.HasActions(b.snapshot.Text()) {
		return false
	}
	if b.unavailable != "" {
		return true // Preserve explicit resource-refusal and recovery diagnostics.
	}
	owned, err := analysis.OwnsSnapshot(s.context(), b.snapshot)
	if err != nil {
		fmt.Fprintln(s.log, "diagnostic ownership unresolved:", uri, err)
	}
	return owned
}

// The current immutable buffer is always checked first. Workspace discovery is
// independent of this worker's deadline; absence and unique external bindings
// are evaluated only after the catalog has completed its input pass.
func (s *session) publishIndexed(uri string, params map[string]any) error {
	document, err := s.bufferDocument(uri)
	if err != nil {
		params["diagnostics"] = []map[string]any{analysisStatus("WF_ANALYSIS_UNAVAILABLE", err.Error())}
		return s.write(map[string]any{"jsonrpc": "2.0", "method": "textDocument/publishDiagnostics", "params": params})
	}
	ctx, cancel := context.WithTimeout(analysis.WithLogicalResult(s.context()), 5*time.Second)
	defer cancel()
	s.catalog.facts.Restore(ctx, workspace.Workspace{Documents: []workspace.Document{document}})
	result := analysis.AnalyzeSchema(ctx, document.Snapshot, document.Target.Version)
	_, _, headerErr := s.catalog.header(s.root(uri))
	complete := s.catalog.scopeReady(s.root(uri), document.Target.ID)
	s.localPublication = true
	if complete || strings.HasPrefix(document.Target.Scope, "opened:") {
		input, err := s.workspace(s.root(uri), document.Target.Scope)
		if err == nil {
			external, completion := analysis.ExternalDiagnostics(ctx, scopeWorkspace(input, document.Target.ID))
			for _, diagnostic := range external {
				if diagnostic.URI == uri {
					result.Diagnostics = append(result.Diagnostics, diagnostic)
				}
			}
			if !completion[document.Snapshot.URI()].Completed || input.IncompleteScopes[document.Target.ID] {
				result.NotEvaluated = append(result.NotEvaluated, "external reference analysis incomplete")
			}
			result.NotEvaluated = append(result.NotEvaluated, input.NotEvaluated...)
			s.localPublication = input.IncompleteScopes[document.Target.ID]
		} else {
			result.NotEvaluated = append(result.NotEvaluated, err.Error())
		}
	} else if headerErr != nil && headerErr.Error() != "configuration pending" {
		result.NotEvaluated = append(result.NotEvaluated, headerErr.Error())
	} else if problem := s.catalog.limited(s.root(uri), document.Target.ID); problem != "" {
		result.NotEvaluated = append(result.NotEvaluated, problem)
	}
	diagnostics := []map[string]any{}
	if !compat.Accepted(document.Target.Version) {
		diagnostics = append(diagnostics, map[string]any{"range": analysis.Range{}, "code": "WF_EXPERIMENTAL_TARGET", "source": "argo-workflows", "severity": 2, "message": compat.ExperimentalReason(document.Target.Version)})
	}
	for _, diagnostic := range result.Diagnostics {
		severity := map[string]int{"Warning": 2, "Information": 3, "Hint": 4}[diagnostic.Severity]
		if severity == 0 {
			severity = 1
		}
		diagnostics = append(diagnostics, map[string]any{"range": diagnostic.Range, "code": diagnostic.Code, "source": diagnostic.Source, "severity": severity, "message": diagnostic.Message})
	}
	if len(result.NotEvaluated) > 0 {
		diagnostics = append(diagnostics, analysisStatus("WF_ANALYSIS_INCOMPLETE", strings.Join(result.NotEvaluated, "; ")))
	}
	params["diagnostics"] = diagnostics
	return s.write(map[string]any{"jsonrpc": "2.0", "method": "textDocument/publishDiagnostics", "params": params})
}

func (s *session) publishEmpty(uri string) error {
	params := map[string]any{"uri": uri, "diagnostics": []map[string]any{}}
	if b, opened := s.buffers[uri]; opened {
		params["version"] = b.version
	}
	return s.write(map[string]any{"jsonrpc": "2.0", "method": "textDocument/publishDiagnostics", "params": params})
}
