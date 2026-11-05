// Package buildinfo holds the native identity stamped by the release builder.
// Unstamped contributor builds remain explicitly marked as development builds.
package buildinfo

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
