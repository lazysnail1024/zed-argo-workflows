package workspace

import (
	"context"
	"crypto/sha256"
	"encoding/hex"
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
	return load(ctx, root, selected, override, nil, nil, nil)
}
func Load(root, selected, override string) (Workspace, error) {
	return load(context.Background(), root, selected, override, nil, nil, nil)
}
func LoadEditor(ctx context.Context, root, selected, override string, overlay *string, roots []string, buffers map[string]source.Snapshot) (Workspace, error) {
	return load(ctx, root, selected, override, overlay, roots, buffers)
}
func load(ctx context.Context, root, selected, override string, overlay *string, roots []string, buffers map[string]source.Snapshot) (Workspace, error) {
	var w Workspace
	if err := ctx.Err(); err != nil {
		return w, err
	}
	absolute, err := filepath.Abs(root)
	if err != nil {
		return w, err
	}
	w.Root = absolute
	config, raw, err := loadConfig(absolute, overlay)
	if err != nil {
		return w, err
	}
	w.Config = config
	if selected != "" {
		found := selected == "unassigned" || (selected == "root" && len(config.Scopes) == 0)
		for _, scope := range config.Scopes {
			if scope.Name == selected {
				found = true
			}
		}
		if !found {
			return w, fmt.Errorf("unknown scope %q", selected)
		}
	}
	// Resolve every configured target before reading files; an invalid scope cannot hide behind a filter.
	for _, s := range config.Scopes {
		if err := ctx.Err(); err != nil {
			return w, err
		}
		v := s.TargetVersion
		if v == "" {
			v = config.TargetVersion
		}
		if override != "" {
			v = override
		}
		if err := compat.Resolve(v); err != nil {
			return w, err
		}
	}
	if len(config.Scopes) == 0 {
		v := config.TargetVersion
		if override != "" {
			v = override
		}
		if err := compat.Resolve(v); err != nil {
			return w, err
		}
	}
	input, err := readInput(ctx, absolute, roots, buffers, config, selected)
	if err != nil {
		return w, err
	}
	w.NotEvaluated = input.NotEvaluated
	w.IncompleteScopes = map[string]bool{}
	for _, file := range input.IncompleteFiles {
		rel, _ := filepath.Rel(absolute, file)
		rel = filepath.ToSlash(rel)
		if matches(config.Exclude, rel) {
			continue
		}
		name := "root"
		if len(config.Scopes) > 0 {
			name = "unassigned"
			for _, scope := range config.Scopes {
				if matches(scope.Include, rel) {
					name = scope.Name
				}
			}
		}
		w.IncompleteScopes[absolute+"#"+name] = true
	}
	byScope := map[string]Target{}
	for _, snapshot := range input.Snapshots {
		if err := ctx.Err(); err != nil {
			return w, err
		}
		filename, err := fileuri.Path(string(snapshot.URI()))
		if err != nil {
			return w, err
		}
		relative, err := filepath.Rel(absolute, filename)
		if err != nil {
			return w, err
		}
		relative = filepath.ToSlash(relative)
		if matches(config.Exclude, relative) {
			continue
		}
		name := input.ScopeNames[snapshot.URI()]
		version := config.TargetVersion
		from := config.targetSource
		for _, scope := range config.Scopes {
			if scope.Name == name && scope.TargetVersion != "" {
				version = scope.TargetVersion
				from = "scope"
				break
			}
		}
		if override != "" {
			version = override
			from = "cli"
		}
		if err := compat.Resolve(version); err != nil {
			return w, err
		}
		if selected != "" && name != selected {
			continue
		}
		target, ok := byScope[name]
		if !ok {
			sum := sha256.Sum256(append(append([]byte{}, raw...), []byte("\n"+absolute+"\n"+name+"\n"+version)...))
			target = Target{ID: absolute + "#" + name, Root: absolute, Scope: name, Version: version, Source: from, SchemaHash: compat.SchemaHash(version), Experimental: !compat.Accepted(version), ConfigHash: hex.EncodeToString(sum[:])}
			byScope[name] = target
			w.Targets = append(w.Targets, target)
		}
		w.Documents = append(w.Documents, Document{Snapshot: snapshot, Target: target})
	}
	return w, nil
}

// OpenDocument assigns an explicit editor buffer without broadening the disk index.
func OpenDocument(w Workspace, snapshot source.Snapshot, override string) (Document, error) {
	filename, err := fileuri.Path(string(snapshot.URI()))
	if err != nil {
		return Document{}, err
	}
	rel, err := filepath.Rel(w.Root, filename)
	if err != nil || rel == ".." || strings.HasPrefix(rel, ".."+string(filepath.Separator)) {
		return Document{}, fmt.Errorf("buffer is outside root")
	}
	relative := filepath.ToSlash(rel)
	if HelmTemplate(w.Root, filename) {
		return Document{}, fmt.Errorf("Helm template source is excluded")
	}
	name := "root"
	version := w.Config.TargetVersion
	from := w.Config.targetSource
	if len(w.Config.Scopes) > 0 {
		name = "unassigned"
		count := 0
		for _, s := range w.Config.Scopes {
			if matches(s.Include, relative) {
				count++
				name = s.Name
				if s.TargetVersion != "" {
					version = s.TargetVersion
					from = "scope"
				}
			}
		}
		if count > 1 {
			return Document{}, fmt.Errorf("buffer matches multiple scopes")
		}
	}
	if override != "" {
		version = override
		from = "cli"
	}
	if err := compat.Resolve(version); err != nil {
		return Document{}, err
	}
	excluded := matches(w.Config.Exclude, relative)
	for _, part := range strings.Split(relative, "/") {
		if part == ".git" || part == "vendor" || part == "node_modules" || part == ".argo-ls" {
			excluded = true
		}
	}
	if excluded {
		name = "opened:" + string(snapshot.URI())
	}
	sum := sha256.Sum256([]byte(w.Root + "\n" + name + "\n" + version))
	target := Target{ID: w.Root + "#" + name, Root: w.Root, Scope: name, Version: version, Source: from, SchemaHash: compat.SchemaHash(version), Experimental: !compat.Accepted(version), ConfigHash: hex.EncodeToString(sum[:])}
	return Document{Snapshot: snapshot, Target: target}, nil
}

// OpenBuffer resolves the current configuration and target without scanning disk
// inputs that cannot affect a document-local query. Scope-wide callers still load
// the complete workspace separately.
func OpenBuffer(ctx context.Context, root, override string, overlay *string, snapshot source.Snapshot) (Document, error) {
	if err := ctx.Err(); err != nil {
		return Document{}, err
	}
	absolute, err := filepath.Abs(root)
	if err != nil {
		return Document{}, err
	}
	config, _, err := loadConfig(absolute, overlay)
	if err != nil {
		return Document{}, err
	}
	if len(config.Scopes) == 0 {
		version := config.TargetVersion
		if override != "" {
			version = override
		}
		if err := compat.Resolve(version); err != nil {
			return Document{}, err
		}
	} else {
		for _, scope := range config.Scopes {
			if err := ctx.Err(); err != nil {
				return Document{}, err
			}
			version := scope.TargetVersion
			if version == "" {
				version = config.TargetVersion
			}
			if override != "" {
				version = override
			}
			if err := compat.Resolve(version); err != nil {
				return Document{}, err
			}
		}
	}
	return OpenDocument(Workspace{Root: absolute, Config: config}, snapshot, override)
}
