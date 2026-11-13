package lsp

import "github.com/lazysnail1024/zed-argo-workflows/internal/analysis"

// Retention uses the same proof fence as result publication. Recheck while
// holding the catalog lock so an older worker cannot replace a newer entry
// after a source observation or configuration change.
func (c *catalog) retainFacts(facts *analysis.Facts, proofs []catalogProof) {
	c.mu.Lock()
	defer c.mu.Unlock()
	if c.validLocked(proofs) {
		c.facts.Store(facts)
	}
}
