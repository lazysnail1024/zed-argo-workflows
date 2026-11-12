// Package workspace reads immutable disk snapshots for explicit CLI checks.
package workspace

import (
	"context"
	"fmt"
	"github.com/lazysnail1024/k8s-crd-toolkit/source"
	"github.com/lazysnail1024/zed-argo-workflows/internal/fileuri"
	"io/fs"
	"os"
	"path/filepath"
	"strings"
)

// readDocuments is the CLI's one-shot collection. Membership, target selection
// and stable bounded reads are shared with the editor's streamed input path.
func readDocuments(ctx context.Context, header Workspace, selected, override string) (Workspace, error) {
	result := header
	result.Targets = nil
	if _, ok := ctx.Value(snapshotBudgetKey{}).(*snapshotBudget); !ok {
		ctx = WithSnapshotBudget(ctx, 0)
	}
	info, err := os.Stat(header.Root)
	if err != nil {
		return result, err
	}
	if !info.IsDir() {
		return result, fmt.Errorf("root is not a directory")
	}
	count := 0
	targets := map[string]bool{}
	knownTargets := make(map[string]Target, len(header.Targets))
	for _, target := range header.Targets {
		knownTargets[target.Scope] = target
	}
	err = filepath.WalkDir(header.Root, func(path string, d fs.DirEntry, err error) error {
		if err := ctx.Err(); err != nil {
			return err
		}
		if err != nil {
			return err
		}
		if d.IsDir() {
			switch d.Name() {
			case ".git", "node_modules", "vendor", ".argo-ls":
				return filepath.SkipDir
			}
			return nil
		}
		if d.Type()&os.ModeSymlink != 0 {
			return nil
		}
		relative, err := candidatePath(header.Root, path, nil)
		if err != nil || relative == "" {
			return err
		}
		count++
		if count > 50000 {
			return fmt.Errorf("workspace exceeds 50000 YAML files")
		}
		if matches(header.Config.Exclude, relative) {
			return nil
		}
		name, err := scopeName(header.Config, relative)
		if err != nil {
			return err
		}
		if selected != "" && name != selected {
			return nil
		}
		uri, err := fileuri.FromPath(path)
		if err != nil {
			return err
		}
		text, err := ReadText(ctx, path)
		if err != nil {
			if _, unavailable := err.(inputProblem); !unavailable {
				return err
			}
		} else {
			var snapshot source.Snapshot
			snapshot, err = source.New(source.URI(uri), 1, text)
			if err == nil {
				// Membership and exclusion were resolved before reading this file.
				// Reuse the scope's target instead of repeating the scope globs and
				// version policy checks for every document.
				target, ok := knownTargets[name]
				if !ok {
					target, err = targetForScope(header, name, override)
					if err != nil {
						return err
					}
					knownTargets[name] = target
				}
				result.Documents = append(result.Documents, Document{Snapshot: snapshot, Target: target})
				if !targets[target.ID] {
					result.Targets = append(result.Targets, target)
					targets[target.ID] = true
				}
				return nil
			}
		}
		result.IncompleteScopes[header.Root+"#"+name] = true
		result.NotEvaluated = append(result.NotEvaluated, path+": "+err.Error())
		return nil
	})
	return result, err
}

// HelmTemplate identifies chart source by path, not by braces in YAML values.
func HelmTemplate(root, name string) bool {
	relative, err := filepath.Rel(root, name)
	if err != nil {
		return false
	}
	parts := strings.Split(relative, string(filepath.Separator))
	for i, p := range parts {
		if p == "templates" {
			if _, err := os.Stat(filepath.Join(append([]string{root}, append(parts[:i], "Chart.yaml")...)...)); err == nil {
				return true
			}
		}
	}
	return false
}
