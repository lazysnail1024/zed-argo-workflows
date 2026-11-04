//go:build !windows

package workspace

import (
	"os"
	"syscall"
)

// Nonblocking open prevents a FIFO replacement from blocking before f.Stat.
func openConfig(path string) (*os.File, error) {
	return os.OpenFile(path, os.O_RDONLY|syscall.O_NONBLOCK, 0)
}
