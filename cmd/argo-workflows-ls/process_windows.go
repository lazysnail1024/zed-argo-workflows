package main

import (
	"fmt"
	"os/exec"
	"syscall"
	"unsafe"
)

var kernel = syscall.NewLazyDLL("kernel32.dll")
var createJob = kernel.NewProc("CreateJobObjectW")
var setJob = kernel.NewProc("SetInformationJobObject")
var assignJob = kernel.NewProc("AssignProcessToJobObject")
var terminateJob = kernel.NewProc("TerminateJobObject")
var resumeProcess = syscall.NewLazyDLL("ntdll.dll").NewProc("NtResumeProcess")

type basicJobLimit struct {
	processTime, jobTime   int64
	flags                  uint32
	minWorking, maxWorking uintptr
	active                 uint32
	affinity               uintptr
	priority, scheduling   uint32
}
type extendedJobLimit struct {
	basic                                          basicJobLimit
	io                                             [6]uint64
	processMemory, jobMemory, peakProcess, peakJob uintptr
}

func startTool(cmd *exec.Cmd) (func(), error) {
	job, _, err := createJob.Call(0, 0)
	if job == 0 {
		return func() {}, fmt.Errorf("create process job: %w", err)
	}
	cleanup := func() { terminateJob.Call(job, 1); syscall.CloseHandle(syscall.Handle(job)) }
	limits := extendedJobLimit{}
	limits.basic.flags = 0x2000
	if ok, _, e := setJob.Call(job, 9, uintptr(unsafe.Pointer(&limits)), unsafe.Sizeof(limits)); ok == 0 {
		cleanup()
		return func() {}, fmt.Errorf("set kill-on-close job: %w", e)
	}
	// Start suspended so descendants cannot escape before job assignment. The
	// explicit rights are TERMINATE, SET_QUOTA and SUSPEND_RESUME; no fallback.
	cmd.SysProcAttr = &syscall.SysProcAttr{CreationFlags: 0x4}
	cmd.Cancel = func() error {
		terminateJob.Call(job, 1)
		if cmd.Process != nil {
			return cmd.Process.Kill()
		}
		return nil
	}
	if e := cmd.Start(); e != nil {
		cleanup()
		return func() {}, e
	}
	process, e := syscall.OpenProcess(0x1|0x100|0x800, false, uint32(cmd.Process.Pid))
	if e != nil {
		cmd.Process.Kill()
		cmd.Wait()
		cleanup()
		return func() {}, fmt.Errorf("open suspended process: %w", e)
	}
	defer syscall.CloseHandle(process)
	if ok, _, e := assignJob.Call(job, uintptr(process)); ok == 0 {
		cmd.Process.Kill()
		cmd.Wait()
		cleanup()
		return func() {}, fmt.Errorf("assign process job: %w", e)
	}
	if status, _, _ := resumeProcess.Call(uintptr(process)); status != 0 {
		cmd.Process.Kill()
		cmd.Wait()
		cleanup()
		return func() {}, fmt.Errorf("resume assigned process: status %#x", status)
	}
	return cleanup, nil
}
