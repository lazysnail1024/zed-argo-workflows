package workspace

import "context"

// ValidateConfig checks configuration and effective targets without acquiring
// resource documents. An editor overlay remains authoritative when supplied.
func ValidateConfig(ctx context.Context, root, override string, overlay *string) error {
	_, err := Describe(ctx, root, override, overlay)
	return err
}
