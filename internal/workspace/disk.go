// Package workspace reads immutable disk snapshots for explicit CLI checks.
package workspace

import (
	"context"
	"fmt"
	"github.com/lazysnail1024/k8s-crd-toolkit/source"
	"io"
	"io/fs"
	"net/url"
	"os"
	"path/filepath"
	"strings"
)

type Input struct {
	Snapshots       []source.Snapshot
	NotEvaluated    []string
	IncompleteFiles []string
	ScopeNames      map[source.URI]string
}

func Read(root string) (Input, error) {
	return readInput(context.Background(), root, nil, nil, Config{}, "")
}
func readInput(ctx context.Context, root string, nested []string, buffers map[string]source.Snapshot, config Config, selected string) (Input, error) {
	result := Input{ScopeNames: map[source.URI]string{}}
	budget, _ := ctx.Value(snapshotBudgetKey{}).(*snapshotBudget)
	if budget == nil {
		budget = &snapshotBudget{}
		for _, snapshot := range buffers {
			budget.bytes += len(snapshot.Text())
		}
	}
	if err := budget.check(0); err != nil {
		return result, err
	}
	absolute, err := filepath.Abs(root)
	if err != nil {
		return result, err
	}
	info, err := os.Stat(absolute)
	if err != nil {
		return result, err
	}
	if !info.IsDir() {
		return result, fmt.Errorf("root is not a directory")
	}
	count := 0
	err = filepath.WalkDir(absolute, func(path string, d fs.DirEntry, err error) error {
		if err := ctx.Err(); err != nil {
			return err
		}
		if err != nil {
			return err
		}
		for _, child := range nested {
			if path == child && path != absolute {
				return filepath.SkipDir
			}
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
		if filepath.Ext(path) != ".yaml" && filepath.Ext(path) != ".yml" {
			return nil
		}
		if d.Name() == ".argo-workflows-ls.yaml" {
			return nil
		}
		if HelmTemplate(absolute, path) {
			return nil
		}
		count++
		if count > 50000 {
			return fmt.Errorf("workspace exceeds 50000 YAML files")
		}
		relative, _ := filepath.Rel(absolute, path)
		relative = filepath.ToSlash(relative)
		if matches(config.Exclude, relative) {
			return nil
		}
		scopeName := "root"
		if len(config.Scopes) > 0 {
			scopeName = "unassigned"
			matched := 0
			for _, scope := range config.Scopes {
				if matches(scope.Include, relative) {
					matched++
					scopeName = scope.Name
				}
			}
			if matched > 1 {
				return fmt.Errorf("file %s matches multiple scopes", relative)
			}
		}
		if selected != "" && scopeName != selected {
			return nil
		}
		uri := (&url.URL{Scheme: "file", Path: filepath.ToSlash(path)}).String()
		result.ScopeNames[source.URI(uri)] = scopeName
		if snapshot, ok := buffers[uri]; ok {
			result.Snapshots = append(result.Snapshots, snapshot)
			return nil
		}
		f, err := openConfig(path)
		if err != nil {
			return err
		}
		defer f.Close()
		info, err := f.Stat()
		if err != nil {
			return err
		}
		if !info.Mode().IsRegular() || info.Size() > 5*1024*1024 {
			result.IncompleteFiles = append(result.IncompleteFiles, path)
			reason := "input exceeds 5 MiB"
			if !info.Mode().IsRegular() {
				reason = "input is not a regular file"
			}
			result.NotEvaluated = append(result.NotEvaluated, path+": "+reason)
			return nil
		}
		if err := budget.check(int(info.Size())); err != nil {
			return err
		}
		raw, err := io.ReadAll(io.LimitReader(f, 5*1024*1024+1))
		if err != nil {
			return err
		}
		after, err := f.Stat()
		if err != nil {
			return err
		}
		if len(raw) > 5*1024*1024 || after.Size() != info.Size() || !after.ModTime().Equal(info.ModTime()) {
			result.IncompleteFiles = append(result.IncompleteFiles, path)
			result.NotEvaluated = append(result.NotEvaluated, path+": input grew or changed during read")
			return nil
		}
		if err := budget.check(len(raw)); err != nil {
			return err
		}
		snapshot, err := source.New(source.URI(uri), 1, string(raw))
		if err != nil {
			result.IncompleteFiles = append(result.IncompleteFiles, path)
			result.NotEvaluated = append(result.NotEvaluated, path+": "+err.Error())
			return nil
		}
		budget.bytes += len(snapshot.Text())
		result.Snapshots = append(result.Snapshots, snapshot)
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
