package lsp

import (
	"crypto/sha256"
	"encoding/hex"
	"encoding/json"

	"github.com/lazysnail1024/k8s-crd-toolkit/index"
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
	// Resource-name completion reads detached candidates beyond the materialized
	// dependency closure. Their scope generation must also enter query identity.
	generations := map[string]uint64{}
	for _, document := range w.Documents {
		scope := document.Target.ID
		if _, seen := generations[scope]; seen {
			continue
		}
		_, state := s.catalog.index.Definitions(index.Scope(scope), "")
		generations[scope] = state.Generation
	}
	configuration, _ := json.Marshal(struct {
		ReferencePolicy, DefaultNamespace string
		Generations                       map[string]uint64
	}{w.Config.ReferencePolicy, w.Config.DefaultNamespace, generations})
	sum := sha256.Sum256([]byte(m.Method + string(m.Params) + fingerprint(w) + string(configuration)))
	s.queryKey = hex.EncodeToString(sum[:])
	raw, ok := s.queryResults[s.queryKey]
	return raw, ok
}
