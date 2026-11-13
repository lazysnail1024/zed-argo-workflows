package workspace

import (
	"fmt"
	"os"
	"path/filepath"
)

// NearestChart walks ancestors of a saved file or directory, never descendants.
func NearestChart(start string) (string, error) {
	path, err := filepath.Abs(start)
	if err != nil {
		return "", err
	}
	info, err := os.Stat(path)
	if err != nil {
		return "", err
	}
	if !info.IsDir() {
		path = filepath.Dir(path)
	}
	for {
		if info, err := os.Stat(filepath.Join(path, "Chart.yaml")); err == nil && info.Mode().IsRegular() {
			return path, nil
		}
		parent := filepath.Dir(path)
		if parent == path {
			break
		}
		path = parent
	}
	return "", fmt.Errorf("no Chart.yaml in ancestors of %s; select a chart file or pass --chart", start)
}
