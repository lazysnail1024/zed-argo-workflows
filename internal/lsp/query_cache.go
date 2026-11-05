package lsp

import (
	"crypto/sha256"
	"encoding/hex"
	"encoding/json"

	"github.com/lazysnail1024/zed-argo-workflows/internal/workspace"
)

// cachedScopeQuery is called only after acquiring the scope a query needs.
// Fresh input remains authoritative even when the client missed a file event.
func (s *session) cachedScopeQuery(m message, w workspace.Workspace) (json.RawMessage, bool) {
	if s.context().Err() != nil || len(w.NotEvaluated) != 0 {
		return nil, false
	}
	for _, incomplete := range w.IncompleteScopes {
		if incomplete {
			return nil, false
		}
	}
	// Editor-only documents do not include these effective options in their
	// Target.ConfigHash. They can change reference resolution without changing
	// any document text, URI or selected target version.
	configuration, _ := json.Marshal(struct {
		ReferencePolicy, DefaultNamespace string
	}{w.Config.ReferencePolicy, w.Config.DefaultNamespace})
	sum := sha256.Sum256([]byte(m.Method + string(m.Params) + fingerprint(w) + string(configuration)))
	s.queryKey = hex.EncodeToString(sum[:])
	raw, ok := s.queryResults[s.queryKey]
	return raw, ok
}
