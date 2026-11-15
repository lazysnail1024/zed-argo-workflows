package compat

import (
	"crypto/sha256"
	_ "embed"
	"fmt"
	"sync"
)

//go:embed modeled-rules.json
var modeledRules []byte

var modeledRulesHash = sync.OnceValue(func() string { return fmt.Sprintf("%x", sha256.Sum256(modeledRules)) })

// ModeledRulesHash identifies the independent semantic provenance. Equal CRD
// hashes do not imply equal controller validation functions or call conditions.
func ModeledRulesHash() string { return modeledRulesHash() }
