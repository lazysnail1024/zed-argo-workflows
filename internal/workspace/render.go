package workspace

import (
	"context"
	"crypto/sha256"
	"encoding/hex"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"os"
	"path/filepath"
	"reflect"
	"strings"
)

var ErrRenderInputsChanged = errors.New("render inputs changed; save inputs and request a new render action")

// RenderProfile is declarative. Paths are relative to the configuration root;
// values order and duplicates have the same meaning as repeated Helm flags.
type RenderProfile struct {
	Chart       string   `json:"chart"`
	Values      []string `json:"values,omitempty"`
	ReleaseName string   `json:"releaseName,omitempty"`
	Namespace   string   `json:"namespace,omitempty"`
	KubeVersion string   `json:"kubeVersion,omitempty"`
	APIVersions []string `json:"apiVersions,omitempty"`
}

func profilePath(path string) bool {
	if path == "" || filepath.IsAbs(path) || strings.ContainsAny(path, "\\\x00\r\n") {
		return false
	}
	for _, part := range strings.Split(path, "/") {
		if part == ".." || part == "" {
			return false
		}
	}
	return true
}

func validateRenderProfile(name string, profile RenderProfile) error {
	validName := len(name) >= 1 && len(name) <= 64
	for i, char := range name {
		alphanumeric := char >= 'a' && char <= 'z' || char >= 'A' && char <= 'Z' || char >= '0' && char <= '9'
		validName = validName && (alphanumeric || i > 0 && (char == '.' || char == '-' || char == '_'))
	}
	if !validName {
		return fmt.Errorf("render profile names must be 1–64 ASCII letters/digits/dot/dash/underscore, starting with a letter or digit")
	}
	if !profilePath(profile.Chart) {
		return fmt.Errorf("render profile %q chart must be a root-relative path without ..", name)
	}
	for _, path := range profile.Values {
		if !profilePath(path) || path == "." {
			return fmt.Errorf("render profile %q values must be root-relative file paths without ..", name)
		}
	}
	return validateRenderParameters(profile.ReleaseName, profile.Namespace, profile.KubeVersion, profile.APIVersions)
}

func validateRenderParameters(release, namespace, kube string, apis []string) error {
	if strings.HasPrefix(release, "-") {
		return fmt.Errorf("releaseName must not start with a flag prefix")
	}
	for _, value := range append([]string{release, namespace, kube}, apis...) {
		if strings.ContainsAny(value, "\x00\r\n") {
			return fmt.Errorf("render parameters must be single-line values")
		}
	}
	return nil
}

type RenderOptions struct {
	Root, File, Scope, TargetVersion, Profile  string
	Chart, ReleaseName, Namespace, KubeVersion string
	Values, APIVersions                        []string
	RequireChartInRoot                         bool
}

type RenderValue struct {
	Path   string `json:"path"`
	SHA256 string `json:"sha256"`
}

// RenderRequest is the saved-input decision shared by CLI and editor. Resolution
// reads bounded local files only; it never discovers or launches Helm. Identity
// includes order, configuration, scope, selected source and resolved paths.
type RenderRequest struct {
	Root            string            `json:"root"`
	RootRealPath    string            `json:"rootRealPath"`
	SelectedFile    string            `json:"selectedFile,omitempty"`
	Chart           string            `json:"chart"`
	ChartRealPath   string            `json:"chartRealPath"`
	Scope           string            `json:"scope"`
	TargetVersion   string            `json:"targetVersion"`
	Profile         string            `json:"profile"`
	ReleaseName     string            `json:"releaseName"`
	Namespace       string            `json:"namespace"`
	KubeVersion     string            `json:"kubeVersion"`
	APIVersions     []string          `json:"apiVersions"`
	Values          []RenderValue     `json:"values"`
	InputSHA256     map[string]string `json:"inputSha256"`
	InputRealPaths  map[string]string `json:"inputRealPaths"`
	RequestIdentity string            `json:"requestIdentity"`
	options         RenderOptions
}

func within(root, name string) bool {
	rel, err := filepath.Rel(root, name)
	return err == nil && rel != ".." && !strings.HasPrefix(rel, ".."+string(filepath.Separator))
}

func confined(root, name string) error {
	realRoot, err := filepath.EvalSymlinks(root)
	if err != nil {
		return err
	}
	realName, err := filepath.EvalSymlinks(name)
	if err != nil {
		return err
	}
	if !within(root, name) || !within(realRoot, realName) {
		return fmt.Errorf("render input is outside workspace: %s", name)
	}
	return nil
}

func ResolveRenderRequest(ctx context.Context, options RenderOptions) (RenderRequest, error) {
	r := RenderRequest{options: options, Profile: options.Profile, Values: []RenderValue{}, APIVersions: append([]string{}, options.APIVersions...)}
	header, err := Describe(ctx, options.Root, options.TargetVersion, nil)
	if err != nil {
		return r, err
	}
	r.Root = header.Root
	r.RootRealPath, err = filepath.EvalSymlinks(r.Root)
	if err != nil {
		return r, err
	}
	chart, values := options.Chart, append([]string{}, options.Values...)
	r.ReleaseName, r.Namespace, r.KubeVersion = options.ReleaseName, options.Namespace, options.KubeVersion
	if options.Profile != "" {
		profile, ok := header.Config.RenderProfiles[options.Profile]
		if !ok {
			return r, fmt.Errorf("unknown render profile %q", options.Profile)
		}
		chart = filepath.Join(r.Root, filepath.FromSlash(profile.Chart))
		values = nil
		for _, name := range profile.Values {
			path := filepath.Join(r.Root, filepath.FromSlash(name))
			if err := confined(r.Root, path); err != nil {
				return r, err
			}
			values = append(values, path)
		}
		r.ReleaseName, r.Namespace, r.KubeVersion = profile.ReleaseName, profile.Namespace, profile.KubeVersion
		r.APIVersions = append([]string{}, profile.APIVersions...)
	}
	if r.ReleaseName == "" {
		r.ReleaseName = "argo-ls"
	}
	if r.Namespace == "" {
		r.Namespace = "default"
	}
	if r.KubeVersion == "" {
		r.KubeVersion = "1.31.0"
	}
	if err := validateRenderParameters(r.ReleaseName, r.Namespace, r.KubeVersion, r.APIVersions); err != nil {
		return r, err
	}
	if options.File != "" {
		r.SelectedFile, err = filepath.Abs(options.File)
		if err != nil {
			return r, err
		}
		if err := confined(r.Root, r.SelectedFile); err != nil {
			return r, err
		}
	}
	if chart == "" {
		start := r.SelectedFile
		if start == "" {
			start = r.Root
		}
		chart, err = NearestChart(start)
		if err != nil {
			return r, err
		}
	}
	r.Chart, err = filepath.Abs(chart)
	if err != nil {
		return r, err
	}
	if options.RequireChartInRoot || options.Profile != "" {
		if err := confined(r.Root, r.Chart); err != nil {
			return r, err
		}
	}
	r.ChartRealPath, err = filepath.EvalSymlinks(r.Chart)
	if err != nil {
		return r, err
	}
	if info, err := os.Stat(filepath.Join(r.Chart, "Chart.yaml")); err != nil || !info.Mode().IsRegular() {
		return r, fmt.Errorf("selected chart requires a regular Chart.yaml")
	}
	if r.SelectedFile != "" {
		selectedChart, err := NearestChart(r.SelectedFile)
		if err != nil {
			return r, err
		}
		if filepath.Clean(selectedChart) != filepath.Clean(r.Chart) {
			return r, fmt.Errorf("render profile/chart does not own the selected file")
		}
	}
	membershipFile := r.SelectedFile
	if membershipFile == "" && options.Profile != "" {
		// Without a selected source, the configured chart supplies membership.
		// A caller cannot borrow another scope's target for this profile.
		membershipFile = filepath.Join(r.Chart, "Chart.yaml")
	}
	if membershipFile != "" {
		target, err := documentTarget(header, membershipFile, options.TargetVersion)
		if err != nil {
			return r, err
		}
		if options.Scope != "" && options.Scope != target.Scope {
			return r, fmt.Errorf("requested scope %q does not own the selected file (%s)", options.Scope, target.Scope)
		}
		r.Scope, r.TargetVersion = target.Scope, target.Version
	} else {
		scope := options.Scope
		if scope == "" {
			if len(header.Config.Scopes) > 1 {
				return r, fmt.Errorf("multiple scopes require --file or explicit --scope")
			}
			scope = "root"
			if len(header.Config.Scopes) == 1 {
				scope = header.Config.Scopes[0].Name
			}
		}
		found := false
		for _, target := range header.Targets {
			if target.Scope == scope {
				r.Scope, r.TargetVersion, found = scope, target.Version, true
				break
			}
		}
		if !found {
			return r, fmt.Errorf("unknown scope %q", scope)
		}
	}
	for _, name := range values {
		absolute, err := filepath.Abs(name)
		if err != nil {
			return r, err
		}
		r.Values = append(r.Values, RenderValue{Path: absolute})
	}
	r.InputSHA256, r.InputRealPaths, err = r.Inputs(ctx, "")
	if err != nil {
		return r, err
	}
	for i := range r.Values {
		r.Values[i].SHA256 = r.InputSHA256[r.Values[i].Path]
	}
	data, err := json.Marshal(r)
	if err != nil {
		return r, err
	}
	sum := sha256.Sum256(data)
	r.RequestIdentity = hex.EncodeToString(sum[:])
	return r, nil
}

// Current re-resolves policy and ordered inputs; ordinary background indexing
// never affects this identity. LSP additionally binds the accepted buffer life.
func (r RenderRequest) Current(ctx context.Context) bool {
	current, err := ResolveRenderRequest(ctx, r.options)
	return err == nil && current.RequestIdentity == r.RequestIdentity
}

// Inputs binds the complete local chart, selected values/config and optionally
// the actual executable. Symlinks inside charts hide dependencies and are
// refused. Values aliases are recorded by both path and canonical identity.
func (r RenderRequest) Inputs(ctx context.Context, executable string) (map[string]string, map[string]string, error) {
	result, realPaths := map[string]string{}, map[string]string{}
	remaining := int64(128 * 1024 * 1024)
	add := func(name string) error {
		if err := ctx.Err(); err != nil {
			return err
		}
		absolute, err := filepath.Abs(name)
		if err != nil {
			return err
		}
		if _, exists := result[absolute]; exists {
			return nil
		}
		if len(result) >= 20000 {
			return fmt.Errorf("render input provenance exceeds 20000 files")
		}
		real, err := filepath.EvalSymlinks(absolute)
		if err != nil {
			return err
		}
		before, err := os.Stat(absolute)
		if err != nil {
			return err
		}
		if !before.Mode().IsRegular() {
			return fmt.Errorf("render input is not a regular file: %s", absolute)
		}
		if before.Size() > remaining {
			return fmt.Errorf("render input provenance exceeds 128 MiB")
		}
		file, err := os.Open(absolute)
		if err != nil {
			return err
		}
		defer file.Close()
		opened, err := file.Stat()
		if err != nil || !os.SameFile(before, opened) {
			return fmt.Errorf("render input changed while opening: %s", absolute)
		}
		hash := sha256.New()
		count, err := io.Copy(hash, io.LimitReader(file, remaining+1))
		if err != nil {
			return err
		}
		remaining -= count
		if remaining < 0 {
			return fmt.Errorf("render input provenance exceeds 128 MiB")
		}
		after, err := os.Stat(absolute)
		afterReal, realErr := filepath.EvalSymlinks(absolute)
		if err != nil || realErr != nil || real != afterReal || !os.SameFile(opened, after) || opened.Size() != after.Size() || opened.ModTime() != after.ModTime() {
			return fmt.Errorf("render input changed while reading: %s", absolute)
		}
		result[absolute], realPaths[absolute] = hex.EncodeToString(hash.Sum(nil)), real
		return nil
	}
	err := filepath.WalkDir(r.Chart, func(path string, entry os.DirEntry, err error) error {
		if err != nil {
			return err
		}
		if err := ctx.Err(); err != nil {
			return err
		}
		if entry.Type()&os.ModeSymlink != 0 {
			return fmt.Errorf("chart symlinks prevent complete input provenance: %s", path)
		}
		if entry.IsDir() {
			return nil
		}
		return add(path)
	})
	if err != nil {
		return nil, nil, err
	}
	for _, value := range r.Values {
		if err := add(value.Path); err != nil {
			return nil, nil, err
		}
	}
	if r.SelectedFile != "" {
		if err := add(r.SelectedFile); err != nil {
			return nil, nil, err
		}
	}
	if executable != "" {
		if err := add(executable); err != nil {
			return nil, nil, err
		}
	}
	config := filepath.Join(r.Root, ".argo-workflows-ls.yaml")
	if _, err := os.Stat(config); os.IsNotExist(err) {
		result[config], realPaths[config] = "absent", config
	} else if err != nil {
		return nil, nil, err
	} else if err := add(config); err != nil {
		return nil, nil, err
	}
	return result, realPaths, nil
}

func (r RenderRequest) InputsEqual(ctx context.Context, executable string, hashes, paths map[string]string) bool {
	current, real, err := r.Inputs(ctx, executable)
	return err == nil && reflect.DeepEqual(hashes, current) && reflect.DeepEqual(paths, real) && r.Current(ctx)
}

func (r RenderRequest) Arguments() []string {
	args := []string{"template", r.ReleaseName, r.Chart, "--namespace", r.Namespace, "--kube-version", r.KubeVersion, "--include-crds"}
	for _, value := range r.Values {
		args = append(args, "--values", value.Path)
	}
	for _, api := range r.APIVersions {
		args = append(args, "--api-versions", api)
	}
	return args
}
