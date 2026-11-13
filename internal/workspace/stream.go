package workspace

import (
	"context"
	"crypto/sha256"
	"encoding/hex"
	"io"
	"os"
	"path/filepath"
	"strings"
	"unicode/utf8"

	"github.com/lazysnail1024/zed-argo-workflows/compat"
)

// Describe resolves configuration and all configured targets without enumerating
// resource files. Targets describe policy, not discovery or analysis completion.
func Describe(ctx context.Context, root, override string, overlay *string) (Workspace, error) {
	var result Workspace
	if err := ctx.Err(); err != nil {
		return result, err
	}
	absolute, err := filepath.Abs(root)
	if err != nil {
		return result, err
	}
	result.Root = absolute
	config, raw, err := loadConfig(absolute, overlay)
	if err != nil {
		return result, err
	}
	result.Config = config
	result.IncompleteScopes = map[string]bool{}
	scopes := append([]Scope(nil), config.Scopes...)
	if len(scopes) == 0 {
		scopes = []Scope{{Name: "root"}}
	} else {
		// Unassigned files use the root target. An invalid root default can be
		// harmless when every actual file belongs to an explicitly valid scope;
		// retain the CLI's deferred error for an actual unassigned document.
		version := config.TargetVersion
		if override != "" {
			version = override
		}
		if compat.Resolve(version) == nil {
			scopes = append(scopes, Scope{Name: "unassigned"})
		}
	}
	for _, scope := range scopes {
		if err := ctx.Err(); err != nil {
			return result, err
		}
		version, from := config.TargetVersion, config.targetSource
		if scope.TargetVersion != "" {
			version, from = scope.TargetVersion, "scope"
		}
		if override != "" {
			version, from = override, "cli"
		}
		if err := compat.Resolve(version); err != nil {
			return result, err
		}
		sum := sha256.Sum256(append(append([]byte{}, raw...), []byte("\n"+absolute+"\n"+scope.Name+"\n"+version)...))
		result.Targets = append(result.Targets, Target{
			ID: absolute + "#" + scope.Name, Root: absolute, Scope: scope.Name,
			Version: version, Source: from, SchemaHash: compat.SchemaHash(version),
			Experimental: !compat.Accepted(version), ConfigHash: hex.EncodeToString(sum[:]),
		})
	}
	return result, nil
}

// Candidate applies the disk membership policy to a discovered or watched path.
// An explicit editor buffer is admitted separately by OpenDocument, including
// the existing isolated scope for excluded files.
func Candidate(w Workspace, filename string, roots []string) (bool, error) {
	relative, err := candidatePath(w.Root, filename, roots)
	return relative != "" && !matches(w.Config.Exclude, relative), err
}

func candidatePath(root, filename string, roots []string) (string, error) {
	absolute, err := filepath.Abs(filename)
	if err != nil {
		return "", err
	}
	relative, err := filepath.Rel(root, absolute)
	if err != nil {
		return "", err
	}
	if relative == "." || relative == ".." || strings.HasPrefix(relative, ".."+string(filepath.Separator)) {
		return "", nil
	}
	for _, nested := range roots {
		if filepath.Clean(nested) == filepath.Clean(root) {
			continue
		}
		child, err := filepath.Rel(root, nested)
		if err != nil || child == ".." || strings.HasPrefix(child, ".."+string(filepath.Separator)) {
			continue
		}
		inside, err := filepath.Rel(nested, absolute)
		if err == nil && inside != ".." && !strings.HasPrefix(inside, ".."+string(filepath.Separator)) {
			return "", nil
		}
	}
	if ext := filepath.Ext(absolute); ext != ".yaml" && ext != ".yml" {
		return "", nil
	}
	if filepath.Base(absolute) == ".argo-workflows-ls.yaml" {
		return "", nil
	}
	parts := strings.Split(relative, string(filepath.Separator))
	for _, part := range parts {
		switch part {
		case ".git", "node_modules", "vendor", ".argo-ls":
			return "", nil
		}
	}
	// Watcher paths must obey the scanner's no-child-symlink policy too. Missing
	// ancestors remain candidates so the caller can process authoritative deletes.
	directory := root
	for _, part := range parts[:len(parts)-1] {
		directory = filepath.Join(directory, part)
		info, err := os.Lstat(directory)
		if os.IsNotExist(err) {
			break
		}
		if err != nil {
			return "", err
		}
		if info.Mode()&os.ModeSymlink != 0 {
			return "", nil
		}
	}
	return filepath.ToSlash(relative), nil
}

// inputProblem identifies a bounded, unusable file without turning a CLI
// collection into a successful empty analysis or discarding other valid inputs.
type inputProblem string

func (p inputProblem) Error() string { return string(p) }

// ReadText reads one regular, nonsymlink input with the existing 5 MiB limit.
// Identity and stat checks reject observed replacements and concurrent writes;
// they do not turn filesystem reads into a transactional snapshot. The caller
// still validates its Documents read ticket before accepting the returned text.
func ReadText(ctx context.Context, filename string) (string, error) {
	if err := ctx.Err(); err != nil {
		return "", err
	}
	before, err := os.Lstat(filename)
	if err != nil {
		return "", err
	}
	if !before.Mode().IsRegular() {
		return "", inputProblem("input is not a regular file")
	}
	const limit = 5 * 1024 * 1024
	if before.Size() > limit {
		return "", inputProblem("input exceeds 5 MiB")
	}
	// A request reserves every open buffer once. Disk reads add to that same
	// allowance across dependency closures; background discovery has only this
	// bounded single-file lifetime and does not retain closed source text.
	budget, _ := ctx.Value(snapshotBudgetKey{}).(*snapshotBudget)
	if budget == nil {
		budget = &snapshotBudget{}
	}
	if err := budget.check(int(before.Size())); err != nil {
		return "", err
	}
	f, err := openConfig(filename)
	if err != nil {
		return "", err
	}
	defer f.Close()
	opened, err := f.Stat()
	if err != nil {
		return "", err
	}
	if !opened.Mode().IsRegular() || !os.SameFile(before, opened) || opened.Size() != before.Size() || !opened.ModTime().Equal(before.ModTime()) {
		return "", inputProblem("input grew or changed during read")
	}
	raw, err := io.ReadAll(io.LimitReader(f, limit+1))
	if err != nil {
		return "", err
	}
	if err := ctx.Err(); err != nil {
		return "", err
	}
	after, err := f.Stat()
	if err != nil {
		return "", err
	}
	current, err := os.Lstat(filename)
	if err != nil {
		return "", err
	}
	if len(raw) > limit || !current.Mode().IsRegular() || !os.SameFile(opened, current) ||
		after.Size() != opened.Size() || !after.ModTime().Equal(opened.ModTime()) ||
		current.Size() != after.Size() || !current.ModTime().Equal(after.ModTime()) || int64(len(raw)) != after.Size() {
		return "", inputProblem("input grew or changed during read")
	}
	if err := ctx.Err(); err != nil {
		return "", err
	}
	if !utf8.Valid(raw) {
		return "", inputProblem("source: text is not valid UTF-8")
	}
	if err := budget.check(len(raw)); err != nil {
		return "", err
	}
	budget.bytes += len(raw)
	return string(raw), nil
}
