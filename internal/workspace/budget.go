package workspace

import (
	"context"
	"fmt"
)

const MaxSnapshotBytes = 128 * 1024 * 1024

type snapshotBudgetKey struct{}
type snapshotBudget struct{ bytes int }

// WithSnapshotBudget owns the retained text allowance of one sequential analysis
// across its loaded roots/scopes. Open buffers are reserved once and then borrowed.
func WithSnapshotBudget(ctx context.Context, reserved int) context.Context {
	return context.WithValue(ctx, snapshotBudgetKey{}, &snapshotBudget{bytes: reserved})
}
func (b *snapshotBudget) check(additional int) error {
	if additional < 0 || b.bytes > MaxSnapshotBytes-additional {
		return fmt.Errorf("retained input snapshot text exceeds 128 MiB")
	}
	return nil
}
