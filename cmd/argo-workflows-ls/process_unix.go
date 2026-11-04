//go:build !windows

package main

import (
	"os"
	"os/exec"
	"syscall"
)

func startTool(cmd *exec.Cmd) (func(), error) {
	cmd.SysProcAttr = &syscall.SysProcAttr{Setpgid: true}
	cmd.Cancel = func() error {
		if cmd.Process == nil {
			return os.ErrProcessDone
		}
		return syscall.Kill(-cmd.Process.Pid, syscall.SIGKILL)
	}
	if err := cmd.Start(); err != nil {
		return func() {}, err
	}
	return func() { _ = syscall.Kill(-cmd.Process.Pid, syscall.SIGKILL) }, nil
}
