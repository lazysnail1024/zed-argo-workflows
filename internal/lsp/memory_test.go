package lsp

import (
	"io"
	"runtime/debug"
	"testing"
)

type memoryObserver struct{ limit int64 }

func (r *memoryObserver) Read([]byte) (int, error) {
	r.limit = debug.SetMemoryLimit(-1)
	return 0, io.EOF
}

func TestServeMemoryLimitPreservesLowerPolicyAndRestoresCaller(t *testing.T) {
	original := debug.SetMemoryLimit(-1)
	defer debug.SetMemoryLimit(original)
	for _, limit := range []int64{1<<63 - 1, 128 * 1024 * 1024} {
		debug.SetMemoryLimit(limit)
		reader := &memoryObserver{}
		if code := Serve(reader, io.Discard, io.Discard); code != 2 {
			t.Fatalf("unexpected empty-transport exit code %d", code)
		}
		if reader.limit != min(limit, 288*1024*1024) {
			t.Fatalf("serve changed the lower policy: got %d from %d", reader.limit, limit)
		}
		if current := debug.SetMemoryLimit(-1); current != limit {
			t.Fatalf("serve did not restore caller policy: got %d, want %d", current, limit)
		}
	}
}
