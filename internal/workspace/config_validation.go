package workspace

import (
	"context"
	"path/filepath"

	"github.com/lazysnail1024/zed-argo-workflows/compat"
)

// ValidateConfig checks configuration syntax and the configured effective
// targets without acquiring or analyzing any resource documents. An editor
// buffer is authoritative when overlay is provided, just as in LoadEditor.
func ValidateConfig(ctx context.Context, root, override string, overlay *string) error {
	if err := ctx.Err(); err != nil {
		return err
	}
	absolute, err := filepath.Abs(root)
	if err != nil {
		return err
	}
	config, _, err := loadConfig(absolute, overlay)
	if err != nil {
		return err
	}
	// Match the effective-target validation used by the workspace loader,
	// including unused configured scopes and an editor target override.
	scopes := config.Scopes
	if len(scopes) == 0 {
		scopes = []Scope{{}}
	}
	for _, scope := range scopes {
		if err := ctx.Err(); err != nil {
			return err
		}
		version := scope.TargetVersion
		if version == "" {
			version = config.TargetVersion
		}
		if override != "" {
			version = override
		}
		if err := compat.Resolve(version); err != nil {
			return err
		}
	}
	return nil
}
