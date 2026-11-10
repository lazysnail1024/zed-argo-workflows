// Package buildinfo holds the native identity stamped by the release builder.
// Unstamped contributor builds remain explicitly marked as development builds.
package buildinfo

import "runtime/debug"

var (
	ServerVersion = "0.0.1-alpha.1+dev"
	SourceCommit  = ""
	Dirty         = "true"
	BuildMode     = "development"
)

func Commit() any {
	if SourceCommit == "" {
		return nil
	}
	return SourceCommit
}

func IsDirty() bool { return Dirty != "false" }

// ToolkitVersion reports the dependency linked into this executable.
func ToolkitVersion() string {
	if info, ok := debug.ReadBuildInfo(); ok {
		for _, dependency := range info.Deps {
			if dependency.Path == "github.com/lazysnail1024/k8s-crd-toolkit" {
				if dependency.Replace != nil {
					return dependency.Replace.Version
				}
				return dependency.Version
			}
		}
	}
	return ""
}
