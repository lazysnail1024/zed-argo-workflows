package workspace

import (
	"context"
	"crypto/sha256"
	"encoding/hex"
	"encoding/json"
	"fmt"
	"github.com/lazysnail1024/k8s-crd-toolkit/source"
	"github.com/lazysnail1024/zed-argo-workflows/compat"
	"github.com/lazysnail1024/zed-argo-workflows/internal/fileuri"
	"io"
	"os"
	"path"
	"path/filepath"
	yaml "sigs.k8s.io/yaml"
	"strings"
)

type Scope struct {
	Name          string   `json:"name"`
	Include       []string `json:"include"`
	TargetVersion string   `json:"targetVersion"`
}
type Config struct {
	targetSource     string
	SchemaVersion    int      `json:"schemaVersion"`
	TargetVersion    string   `json:"targetVersion"`
	ReferencePolicy  string   `json:"referencePolicy"`
	DefaultNamespace string   `json:"defaultNamespace"`
	Exclude          []string `json:"exclude"`
	Scopes           []Scope  `json:"scopes"`
}
type Target struct {
	ID               string  `json:"id"`
	Root             string  `json:"root"`
	Scope            string  `json:"scope"`
	Version          string  `json:"version"`
	Source           string  `json:"source"`
	SchemaHash       string  `json:"schemaHash"`
	RulesetID        *string `json:"rulesetId"`
	ConfigHash       string  `json:"configHash"`
	AnalysisComplete bool    `json:"analysisComplete"`
	Experimental     bool    `json:"experimental"`
}
type Document struct {
	Snapshot source.Snapshot
	Target   Target
}
type Workspace struct {
	Root             string
	Config           Config
	Documents        []Document
	Targets          []Target
	NotEvaluated     []string
	IncompleteScopes map[string]bool
}

// Glob supports portable slash-separated patterns with ** as a whole segment.
func glob(pattern, name string) (bool, error) {
	if pattern == "" || strings.HasPrefix(pattern, "/") || strings.Contains(pattern, "\\") {
		return false, fmt.Errorf("invalid relative glob %q", pattern)
	}
	parts := strings.Split(pattern, "/")
	for _, p := range parts {
		if p == ".." || p == "" {
			return false, fmt.Errorf("invalid glob %q", pattern)
		}
		if p != "**" {
			if _, e := path.Match(p, ""); e != nil {
				return false, e
			}
			if strings.Contains(p, "**") {
				return false, fmt.Errorf("** must occupy a segment")
			}
		}
	}
	var match func([]string, []string) bool
	match = func(p, n []string) bool {
		if len(p) == 0 {
			return len(n) == 0
		}
		if p[0] == "**" {
			if match(p[1:], n) {
				return true
			}
			return len(n) > 0 && match(p, n[1:])
		}
		if len(n) == 0 {
			return false
		}
		ok, _ := path.Match(p[0], n[0])
		return ok && match(p[1:], n[1:])
	}
	return match(parts, strings.Split(name, "/")), nil
}
func matches(patterns []string, name string) bool {
	for _, p := range patterns {
		// Configuration validates every pattern. Reject a literal prefix mismatch
		// before repeating glob parsing, without skipping scope ambiguity checks.
		literal := p
		if wildcard := strings.IndexAny(p, "*?["); wildcard >= 0 {
			literal = p[:wildcard]
		}
		if !strings.HasPrefix(name, literal) {
			continue
		}
		ok, _ := glob(p, name)
		if ok {
			return true
		}
	}
	return false
}
func loadConfig(root string, overlay *string) (Config, []byte, error) {
	c := Config{SchemaVersion: 1, TargetVersion: compat.DefaultVersion, ReferencePolicy: "open", targetSource: "default"}
	var raw []byte
	var err error
	if overlay != nil {
		raw = []byte(*overlay)
	} else {
		var f *os.File
		f, err = openConfig(filepath.Join(root, ".argo-workflows-ls.yaml"))
		if err == nil {
			defer f.Close()
			info, statErr := f.Stat()
			if statErr != nil {
				return c, nil, statErr
			}
			if !info.Mode().IsRegular() {
				return c, nil, fmt.Errorf("configuration must be a regular file")
			}
			if info.Size() > 1024*1024 {
				return c, nil, fmt.Errorf("configuration exceeds 1 MiB")
			}
			raw, err = io.ReadAll(io.LimitReader(f, 1024*1024+1))
		}
	}
	if os.IsNotExist(err) {
		return c, nil, nil
	}
	if err != nil {
		return c, nil, err
	}
	if len(raw) > 1024*1024 {
		return c, nil, fmt.Errorf("configuration exceeds 1 MiB")
	}
	if err = yaml.UnmarshalStrict(raw, &c); err != nil {
		return c, nil, err
	}
	// Presence matters: an explicit pin equal to the default still comes from
	// root configuration, while a configuration containing only policy does not.
	var selection struct {
		TargetVersion *string `json:"targetVersion"`
	}
	if err = yaml.Unmarshal(raw, &selection); err != nil {
		return c, nil, err
	}
	if selection.TargetVersion != nil {
		c.targetSource = "root"
	}
	if c.SchemaVersion != 1 {
		return c, nil, fmt.Errorf("unsupported configuration schemaVersion")
	}
	if c.ReferencePolicy != "open" && c.ReferencePolicy != "closed" {
		return c, nil, fmt.Errorf("invalid referencePolicy")
	}
	names := map[string]bool{}
	for _, s := range c.Scopes {
		if s.Name == "" || s.Name == "unassigned" || names[s.Name] || len(s.Include) == 0 {
			return c, nil, fmt.Errorf("scope names must be unique and include must be nonempty")
		}
		names[s.Name] = true
		for _, p := range s.Include {
			if _, e := glob(p, ""); e != nil {
				return c, nil, e
			}
		}
	}
	for _, p := range c.Exclude {
		if _, e := glob(p, ""); e != nil {
			return c, nil, e
		}
	}
	return c, raw, nil
}
func LoadContext(ctx context.Context, root, selected, override string) (Workspace, error) {
	header, err := Describe(ctx, root, override, nil)
	if err != nil {
		return header, err
	}
	if selected != "" {
		found := selected == "unassigned" || selected == "root" && len(header.Config.Scopes) == 0
		for _, scope := range header.Config.Scopes {
			found = found || scope.Name == selected
		}
		if !found {
			return header, fmt.Errorf("unknown scope %q", selected)
		}
	}
	return readDocuments(ctx, header, selected, override)
}

func Load(root, selected, override string) (Workspace, error) {
	return LoadContext(context.Background(), root, selected, override)
}

// scopeName selects membership before resource I/O or target-version resolution.
// This keeps an unselected unassigned file from activating an invalid root default.
func scopeName(config Config, relative string) (string, error) {
	if len(config.Scopes) == 0 {
		return "root", nil
	}
	name, count := "unassigned", 0
	for _, scope := range config.Scopes {
		if matches(scope.Include, relative) {
			name = scope.Name
			count++
		}
	}
	if count > 1 {
		return "", fmt.Errorf("file %s matches multiple scopes", relative)
	}
	return name, nil
}

// OpenDocument assigns an explicit editor buffer without broadening the disk index.
func OpenDocument(w Workspace, snapshot source.Snapshot, override string) (Document, error) {
	filename, err := fileuri.Path(string(snapshot.URI()))
	if err != nil {
		return Document{}, err
	}
	target, err := documentTarget(w, filename, override)
	return Document{Snapshot: snapshot, Target: target}, err
}

func documentTarget(w Workspace, filename, override string) (Target, error) {
	rel, err := filepath.Rel(w.Root, filename)
	if err != nil || rel == ".." || strings.HasPrefix(rel, ".."+string(filepath.Separator)) {
		return Target{}, fmt.Errorf("buffer is outside root")
	}
	relative := filepath.ToSlash(rel)
	if HelmTemplate(w.Root, filename) {
		return Target{}, fmt.Errorf("Helm template source is excluded")
	}
	name, err := scopeName(w.Config, relative)
	if err != nil {
		return Target{}, err
	}
	excluded := matches(w.Config.Exclude, relative)
	for _, part := range strings.Split(relative, "/") {
		if part == ".git" || part == "vendor" || part == "node_modules" || part == ".argo-ls" {
			excluded = true
		}
	}
	if excluded {
		address, err := fileuri.FromPath(filename)
		if err != nil {
			return Target{}, err
		}
		name = "opened:" + address
	}
	return targetForScope(w, name, override)
}

// CLI discovery already resolved membership once. Editor buffers call this
// after applying their independent excluded-file and Helm ownership rules.
func targetForScope(w Workspace, name, override string) (Target, error) {
	version, from := w.Config.TargetVersion, w.Config.targetSource
	for _, scope := range w.Config.Scopes {
		if scope.Name == name && scope.TargetVersion != "" {
			version, from = scope.TargetVersion, "scope"
		}
	}
	if override != "" {
		version = override
		from = "cli"
	}
	if err := compat.Resolve(version); err != nil {
		return Target{}, err
	}
	// A streamed metadata header already carries the exact configuration
	// identity. Reuse it rather than replacing it with an identity that omits
	// namespace and reference policy changes.
	for _, target := range w.Targets {
		if target.Root == w.Root && target.Scope == name && target.Version == version && target.Source == from {
			return target, nil
		}
	}
	identity, _ := json.Marshal(struct {
		Config                       Config
		Root, Scope, Version, Source string
	}{w.Config, w.Root, name, version, from})
	sum := sha256.Sum256(identity)
	target := Target{ID: w.Root + "#" + name, Root: w.Root, Scope: name, Version: version, Source: from, SchemaHash: compat.SchemaHash(version), Experimental: !compat.Accepted(version), ConfigHash: hex.EncodeToString(sum[:])}
	return target, nil
}

// OpenBuffer resolves the current configuration and target without scanning disk
// inputs that cannot affect a document-local query. Scope-wide callers still load
// the complete workspace separately.
func OpenBuffer(ctx context.Context, root, override string, overlay *string, snapshot source.Snapshot) (Document, error) {
	header, err := Describe(ctx, root, override, overlay)
	if err != nil {
		return Document{}, err
	}
	return OpenDocument(header, snapshot, override)
}
