// Package fileuri translates local absolute paths and LSP file URIs on the host OS.
package fileuri

import (
	"fmt"
	"net/url"
	"path/filepath"
	"runtime"
	"strings"
)

// FromPath uses an empty URI authority, including for Windows drive paths.
// A drive must be in the URI path (/C:/...), never in its authority (file://C:).
func FromPath(name string) (string, error) {
	if !filepath.IsAbs(name) || strings.ContainsRune(name, '\x00') {
		return "", fmt.Errorf("file path must be absolute and contain no NUL bytes")
	}
	path := filepath.ToSlash(filepath.Clean(name))
	if runtime.GOOS == "windows" {
		if !drivePath(path) {
			return "", fmt.Errorf("only local Windows drive paths are supported; UNC and device paths are unsupported")
		}
		path = "/" + path
	}
	return (&url.URL{Scheme: "file", Path: path}).String(), nil
}

// Path accepts local file URIs with either an empty or localhost authority.
// Filesystem traversal is normalized here; callers still enforce workspace roots.
func Path(raw string) (string, error) {
	uri, err := url.Parse(raw)
	if err != nil || uri.Scheme != "file" || uri.Opaque != "" || uri.User != nil ||
		uri.Host != "" && uri.Host != "localhost" || uri.RawQuery != "" || uri.ForceQuery || uri.Fragment != "" {
		return "", fmt.Errorf("only local file URIs without query or fragment are supported")
	}
	path := uri.Path
	if strings.ContainsRune(path, '\x00') {
		return "", fmt.Errorf("file URI must contain no NUL bytes")
	}
	if runtime.GOOS == "windows" {
		if !strings.HasPrefix(path, "/") || !drivePath(path[1:]) || strings.Contains(path, "\\") {
			return "", fmt.Errorf("Windows file URI must contain a local absolute drive path; UNC and device paths are unsupported")
		}
		path = path[1:]
	}
	name := filepath.Clean(filepath.FromSlash(path))
	if !filepath.IsAbs(name) {
		return "", fmt.Errorf("file URI must be absolute")
	}
	return name, nil
}

func drivePath(path string) bool {
	return len(path) >= 3 && ((path[0] >= 'A' && path[0] <= 'Z') || (path[0] >= 'a' && path[0] <= 'z')) &&
		path[1] == ':' && path[2] == '/'
}
