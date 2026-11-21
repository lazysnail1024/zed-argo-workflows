// Package compat resolves the offline schema catalog and automatic compatibility range.
package compat

import (
	"crypto/sha256"
	_ "embed"
	"encoding/hex"
	"encoding/json"
	"errors"
	"fmt"
	"slices"
	"sort"
	"strconv"
	"strings"
	"sync"
)

//go:embed catalog.lock.json
var catalog []byte

type catalogVersion struct {
	Version string `json:"version"`
	Commit  string `json:"upstreamCommit"`
	Hash    string `json:"schemaSha256"`
	Sources []struct {
		Kind              string   `json:"kind"`
		URL               string   `json:"url"`
		AvailableVersions []string `json:"availableVersions"`
		Schemas           map[string]struct {
			File           string     `json:"file"`
			BranchVersions [][]string `json:"branchVersions"`
		} `json:"normalizedSchemas"`
	} `json:"sources"`
}

// The embedded catalog is immutable for the process lifetime; no workspace
// input, cancellation or failed analysis is stored in this index.
var catalogIndex = sync.OnceValues(func() (map[string]catalogVersion, error) {
	var decoded struct {
		Versions  []catalogVersion `json:"versions"`
		Automatic catalogVersion   `json:"automatic"`
	}
	if err := json.Unmarshal(catalog, &decoded); err != nil {
		return nil, err
	}
	index := make(map[string]catalogVersion, len(decoded.Versions))
	for _, v := range decoded.Versions {
		index[v.Version] = v
	}
	index[DefaultVersion] = decoded.Automatic
	return index, nil
})
var catalogHash = sync.OnceValue(func() string { sum := sha256.Sum256(catalog); return hex.EncodeToString(sum[:]) })

// DefaultVersion accepts a document when one complete supported release schema
// accepts it. Users may still select an exact version for deployment-specific checks.
const DefaultVersion = "auto"

func Accepted(version string) bool { return Resolve(version) == nil }
func ExperimentalReason(version string) string {
	return "target " + version + " is outside the embedded supported release catalog"
}

type parsedVersion struct {
	raw   string
	parts [3]int
}

func parseVersionTuple(v string) [3]int {
	var parts [3]int
	rest := v
	for i := range parts {
		dot := strings.IndexByte(rest, '.')
		seg := rest
		if dot >= 0 {
			seg = rest[:dot]
			rest = rest[dot+1:]
		} else {
			rest = ""
		}
		parts[i], _ = strconv.Atoi(seg)
		if rest == "" {
			break
		}
	}
	return parts
}

var supportedCatalog = sync.OnceValue(func() (versions []string) {
	index, err := catalogIndex()
	if err != nil {
		return nil
	}
	parsed := make([]parsedVersion, 0, len(index)-1)
	for version := range index {
		if version != DefaultVersion {
			parsed = append(parsed, parsedVersion{raw: version, parts: parseVersionTuple(version)})
		}
	}
	sort.Slice(parsed, func(i, j int) bool {
		for k := range parsed[i].parts {
			if parsed[i].parts[k] != parsed[j].parts[k] {
				return parsed[i].parts[k] < parsed[j].parts[k]
			}
		}
		return false
	})
	versions = make([]string, len(parsed))
	for i, p := range parsed {
		versions[i] = p.raw
	}
	return versions
})

var supportedMajors = sync.OnceValue(func() []int {
	versions := supportedCatalog()
	majors := []int{}
	for _, version := range versions {
		major := parseVersionTuple(version)[0]
		if len(majors) == 0 || majors[len(majors)-1] != major {
			majors = append(majors, major)
		}
	}
	return majors
})

func SupportedVersions() []string {
	return slices.Clone(supportedCatalog())
}
func SupportedMajors() []int {
	return slices.Clone(supportedMajors())
}
func SupportedRange() map[string]string {
	versions := supportedCatalog()
	if len(versions) == 0 {
		return nil
	}
	return map[string]string{"minimum": versions[0], "maximum": versions[len(versions)-1]}
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
		return fmt.Errorf("unknown target version: %s; use auto or an exact supported version from compat/catalog.lock.json; remove an editor targetVersion override to restore the file pin", version)
	}
	return nil
}

// ErrSchemaUnavailable distinguishes an absent GVK from an invalid catalog or schema.
var ErrSchemaUnavailable = errors.New("no schema")

func SchemaFile(version, apiVersion, kind string) (string, error) {
	index, err := catalogIndex()
	if err != nil {
		return "", err
	}
	target, ok := index[version]
	if !ok {
		return "", fmt.Errorf("unknown target version: %s", version)
	}
	for _, s := range target.Sources {
		if s.Kind == kind {
			if schema, ok := s.Schemas[apiVersion]; ok {
				return schema.File, nil
			}
		}
	}
	return "", fmt.Errorf("%w for target %s GVK %s/%s", ErrSchemaUnavailable, version, apiVersion, kind)
}
func SchemaHash(version string) string {
	index, err := catalogIndex()
	if err != nil {
		return ""
	}
	return index[version].Hash
}

// SchemaBranchVersions maps the compiled automatic AnyOf branches to complete
// release contracts. It is generated with branch interning, not inferred from
// exact-file hashes or permissive editor query selection.
func SchemaBranchVersions(apiVersion, kind string) ([][]string, error) {
	index, err := catalogIndex()
	if err != nil {
		return nil, err
	}
	for _, source := range index[DefaultVersion].Sources {
		if source.Kind == kind {
			if schema, ok := source.Schemas[apiVersion]; ok {
				return schema.BranchVersions, nil
			}
		}
	}
	return nil, fmt.Errorf("%w for automatic GVK %s/%s", ErrSchemaUnavailable, apiVersion, kind)
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
				if version == DefaultVersion {
					versions := source.AvailableVersions
					if len(versions) == 0 {
						versions = SupportedVersions()
					}
					return fmt.Sprintf("[supported releases %s–%s](%s)", versions[0], versions[len(versions)-1], source.URL), nil
				}
				return fmt.Sprintf("[%s](%s)", target.Commit, source.URL), nil
			}
		}
	}
	return "", fmt.Errorf("no schema source for target %s GVK %s/%s", version, apiVersion, kind)
}
