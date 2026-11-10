package lsp

import (
	"fmt"
	"path/filepath"

	"github.com/lazysnail1024/zed-argo-workflows/internal/analysis"
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
	if workspace.HelmTemplate(root, name) {
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

func (s *session) publishEmpty(uri string) error {
	params := map[string]any{"uri": uri, "diagnostics": []map[string]any{}}
	if b, opened := s.buffers[uri]; opened {
		params["version"] = b.version
	}
	return s.write(map[string]any{"jsonrpc": "2.0", "method": "textDocument/publishDiagnostics", "params": params})
}
