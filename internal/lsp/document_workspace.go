package lsp

import (
	"fmt"

	"github.com/lazysnail1024/zed-argo-workflows/internal/workspace"
)

// documentWorkspace reacquires the selected input for a document, including
// editor-only scopes that cannot be selected by the disk workspace loader.
func (s *session) documentWorkspace(uri, scope string) (workspace.Workspace, error) {
	root := s.root(uri)
	if root == "" {
		return workspace.Workspace{}, fmt.Errorf("document is outside workspace")
	}
	if scope != "opened:"+uri {
		return s.workspace(root, scope)
	}
	// Excluded files acquire an isolated scope only when their editor buffers are
	// added to the workspace. Reacquire the current input, then apply the same
	// target filter used by diagnostics and action creation. A changed exclusion
	// or target must not silently bind an action to a different scope.
	w, err := s.workspace(root, "")
	if err != nil {
		return w, err
	}
	for _, d := range w.Documents {
		if string(d.Snapshot.URI()) == uri && d.Target.Scope == scope {
			return scopeWorkspace(w, d.Target.ID), nil
		}
	}
	return workspace.Workspace{}, fmt.Errorf("document scope changed")
}
