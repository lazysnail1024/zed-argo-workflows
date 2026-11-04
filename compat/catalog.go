// Package compat resolves exact versions from the collected upstream catalog.
// Collection and local-contract acceptance are separate.
package compat

import (
	"crypto/sha256"
	_ "embed"
	"encoding/hex"
	"encoding/json"
	"fmt"
	"sync"
)

//go:embed catalog.lock.json
var catalog []byte

type catalogVersion struct {
	Version string `json:"version"`
	Commit  string `json:"upstreamCommit"`
	Hash    string `json:"schemaSha256"`
	Sources []struct {
		Kind    string `json:"kind"`
		URL     string `json:"url"`
		Schemas map[string]struct {
			File string `json:"file"`
		} `json:"normalizedSchemas"`
	} `json:"sources"`
}

// The embedded catalog is immutable for the process lifetime; no workspace
// input, cancellation or failed analysis is stored in this index.
var catalogIndex = sync.OnceValues(func() (map[string]catalogVersion, error) {
	var decoded struct {
		Versions []catalogVersion `json:"versions"`
	}
	if err := json.Unmarshal(catalog, &decoded); err != nil {
		return nil, err
	}
	index := make(map[string]catalogVersion, len(decoded.Versions))
	for _, v := range decoded.Versions {
		index[v.Version] = v
	}
	return index, nil
})
var catalogHash = sync.OnceValue(func() string { sum := sha256.Sum256(catalog); return hex.EncodeToString(sum[:]) })

// DefaultVersion is the accepted local-editing target. Other collected versions
// remain available for experimental read-only analysis.
const DefaultVersion = "3.6.0"

func Accepted(version string) bool { return version == DefaultVersion }
func ExperimentalReason(version string) string {
	return "target " + version + " is experimental; only 3.6.0 has completed local-contract acceptance"
}

func Hash() string { return catalogHash() }
func Resolve(version string) error {
	if version == "" {
		return fmt.Errorf("target version must be resolved before analysis")
	}
	index, err := catalogIndex()
	if err != nil {
		return fmt.Errorf("invalid embedded catalog: %w", err)
	}
	if _, ok := index[version]; !ok {
		return fmt.Errorf("unknown target version: %s", version)
	}
	return nil
}
func SchemaFile(version, apiVersion, kind string) (string, error) {
	index, err := catalogIndex()
	if err != nil {
		return "", err
	}
	for _, s := range index[version].Sources {
		if s.Kind == kind {
			if schema, ok := s.Schemas[apiVersion]; ok {
				return schema.File, nil
			}
		}
	}
	return "", fmt.Errorf("no schema for target %s GVK %s/%s", version, apiVersion, kind)
}
func SchemaHash(version string) string {
	index, err := catalogIndex()
	if err != nil {
		return ""
	}
	return index[version].Hash
}

// SchemaSource identifies the fixed upstream input of the current target/GVK.
func SchemaSource(version, apiVersion, kind string) (string, error) {
	index, err := catalogIndex()
	if err != nil {
		return "", err
	}
	target := index[version]
	for _, source := range target.Sources {
		if source.Kind == kind {
			if _, ok := source.Schemas[apiVersion]; ok {
				return fmt.Sprintf("[%s](%s)", target.Commit, source.URL), nil
			}
		}
	}
	return "", fmt.Errorf("no schema source for target %s GVK %s/%s", version, apiVersion, kind)
}
