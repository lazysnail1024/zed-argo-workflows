package main

import (
	"bytes"
	"context"
	"crypto/sha256"
	"encoding/hex"
	"encoding/json"
	"flag"
	"fmt"
	"github.com/lazysnail1024/zed-argo-workflows/compat"
	"github.com/lazysnail1024/zed-argo-workflows/internal/fileuri"
	"github.com/lazysnail1024/zed-argo-workflows/internal/workspace"
	"io"
	"os"
	"os/exec"
	"os/signal"
	"path/filepath"
	"reflect"
	"strings"
	"sync"
	"syscall"
	"time"
)

type arguments []string

func (a *arguments) String() string         { return strings.Join(*a, ",") }
func (a *arguments) Set(value string) error { *a = append(*a, value); return nil }

type outputBudget struct {
	mu       sync.Mutex
	bytes    int
	overflow bool
	cancel   context.CancelFunc
}
type boundedOutput struct {
	buffer bytes.Buffer
	budget *outputBudget
}

func (b *boundedOutput) Write(p []byte) (int, error) {
	b.budget.mu.Lock()
	defer b.budget.mu.Unlock()
	remaining := 10*1024*1024 - b.budget.bytes
	n := len(p)
	if n > remaining {
		n = remaining
	}
	written, err := b.buffer.Write(p[:n])
	b.budget.bytes += written
	if n < len(p) {
		b.budget.overflow = true
		b.budget.cancel()
		return written, fmt.Errorf("combined command output exceeds 10 MiB")
	}
	return written, err
}
func toolRun(parent context.Context, path string, args, env []string) ([]byte, []byte, int, error) {
	ctx, cancel := context.WithCancel(parent)
	defer cancel()
	cmd := exec.CommandContext(ctx, path, args...)
	cmd.Env = env
	cmd.WaitDelay = 200 * time.Millisecond
	budget := &outputBudget{cancel: cancel}
	stdout := boundedOutput{budget: budget}
	stderr := boundedOutput{budget: budget}
	cmd.Stdout = &stdout
	cmd.Stderr = &stderr
	cleanup, err := startTool(cmd)
	if err == nil {
		defer cleanup()
		err = cmd.Wait()
	}
	code := 0
	if err != nil {
		code = 2
		if cmd.ProcessState != nil {
			code = cmd.ProcessState.ExitCode()
		}
	}
	if parent.Err() != nil {
		err = parent.Err()
	} else if budget.overflow {
		err = fmt.Errorf("combined command output exceeds 10 MiB")
	}
	return stdout.buffer.Bytes(), stderr.buffer.Bytes(), code, err
}
func commandContext() (context.Context, context.CancelFunc) {
	parent, stop := signal.NotifyContext(context.Background(), os.Interrupt, syscall.SIGTERM)
	ctx, cancel := context.WithTimeout(parent, 30*time.Second)
	return ctx, func() { cancel(); stop() }
}
func commandFailure(ctx context.Context, operation string, err error, stdout, stderr []byte, code int) int {
	exit := 2
	if ctx.Err() != nil {
		err = ctx.Err()
		exit = 3
	}
	emit(map[string]any{"operation": operation, "product": "argo-workflows", "analysisComplete": false, "error": err.Error(), "toolExitCode": code, "stdout": string(stdout), "stderr": string(stderr)})
	return exit
}
func selectedTarget(ctx context.Context, root, scope, override string) (string, error) {
	w, err := workspace.LoadContext(ctx, root, scope, override)
	if err != nil {
		return "", err
	}
	if len(w.Config.Scopes) > 1 && scope == "" {
		return "", fmt.Errorf("multiple scopes require explicit --scope")
	}
	target := w.Config.TargetVersion
	for _, s := range w.Config.Scopes {
		if s.Name == scope || scope == "" && len(w.Config.Scopes) == 1 {
			if s.TargetVersion != "" {
				target = s.TargetVersion
			}
		}
	}
	if override != "" {
		target = override
	}
	return target, compat.Resolve(target)
}
func renderCheck(args []string) int {
	flags := flag.NewFlagSet("render-check", flag.ContinueOnError)
	flags.SetOutput(os.Stderr)
	root := flags.String("root", ".", "source root")
	scope := flags.String("scope", "", "source scope")
	target := flags.String("target-version", "", "exact target")
	chart := flags.String("chart", "", "local chart directory")
	helm := flags.String("helm-binary", "", "absolute Helm3 path")
	output := flags.String("output", "", "generated input directory")
	release := flags.String("release-name", "argo-ls", "release name")
	namespace := flags.String("namespace", "default", "namespace")
	kube := flags.String("kube-version", "1.31.0", "Kubernetes capabilities version")
	var values, apiVersions arguments
	flags.Var(&values, "values", "local values file")
	flags.Var(&apiVersions, "api-version", "extra capability")
	if flags.Parse(args) != nil || flags.NArg() != 0 {
		return 2
	}
	ctx, cancel := commandContext()
	defer cancel()
	var rawStdout, rawStderr []byte
	rawExit := 0
	fail := func(err error) int { return commandFailure(ctx, "render-check", err, rawStdout, rawStderr, rawExit) }
	if !filepath.IsAbs(*helm) || *chart == "" || *output == "" {
		return fail(fmt.Errorf("--helm-binary must be absolute; --chart and --output are required"))
	}
	chartPath, err := filepath.Abs(*chart)
	if err != nil {
		return fail(err)
	}
	if _, err := os.Stat(filepath.Join(chartPath, "Chart.yaml")); err != nil {
		return fail(err)
	}
	outputPath, err := filepath.Abs(*output)
	if err != nil {
		return fail(err)
	}
	if entries, err := os.ReadDir(outputPath); err == nil && len(entries) > 0 {
		return fail(fmt.Errorf("output directory must be empty"))
	} else if err != nil && !os.IsNotExist(err) {
		return fail(err)
	}
	if relative, err := filepath.Rel(chartPath, outputPath); err != nil || relative == "." || relative != ".." && !strings.HasPrefix(relative, ".."+string(filepath.Separator)) {
		return fail(fmt.Errorf("output directory must be outside the chart input directory"))
	}
	inputs, err := renderInputs(ctx, chartPath, *helm, *root, values)
	if err != nil {
		return fail(err)
	}
	version, err := selectedTarget(ctx, *root, *scope, *target)
	if err != nil {
		return fail(err)
	}
	temp, err := os.MkdirTemp("", "argo-workflows-helm-")
	if err != nil {
		return fail(err)
	}
	defer os.RemoveAll(temp)
	kubeconfig := filepath.Join(temp, "kubeconfig")
	if err := os.WriteFile(kubeconfig, []byte("apiVersion: v1\nkind: Config\nclusters: []\ncontexts: []\nusers: []\n"), 0600); err != nil {
		return fail(err)
	}
	env := []string{"PATH=" + os.Getenv("PATH"), "LANG=C.UTF-8", "KUBECONFIG=" + kubeconfig, "HELM_PLUGINS=" + filepath.Join(temp, "plugins"), "HELM_CACHE_HOME=" + filepath.Join(temp, "cache"), "HELM_CONFIG_HOME=" + filepath.Join(temp, "config"), "HELM_DATA_HOME=" + filepath.Join(temp, "data")}
	versionOut, versionErr, versionCode, err := toolRun(ctx, *helm, []string{"version", "--short"}, env)
	rawStdout, rawStderr, rawExit = versionOut, versionErr, versionCode
	toolVersion := strings.TrimSpace(string(versionOut))
	if err != nil {
		return fail(fmt.Errorf("Helm version failed (%d): %s: %w", versionCode, versionErr, err))
	}
	if !strings.HasPrefix(toolVersion, "v3.") {
		return fail(fmt.Errorf("Helm3 is required; got %s", toolVersion))
	}
	command := []string{"template", *release, chartPath, "--namespace", *namespace, "--kube-version", *kube, "--include-crds"}
	for _, path := range values {
		absolute, e := filepath.Abs(path)
		if e != nil {
			return fail(e)
		}
		if _, e = os.Stat(absolute); e != nil {
			return fail(e)
		}
		command = append(command, "--values", absolute)
	}
	for _, api := range apiVersions {
		command = append(command, "--api-versions", api)
	}
	stdout, stderr, exitCode, executionErr := toolRun(ctx, *helm, command, env)
	currentInputs, inputErr := renderInputs(ctx, chartPath, *helm, *root, values)
	inputsUnchanged := inputErr == nil && reflect.DeepEqual(inputs, currentInputs)
	rawStdout, rawStderr, rawExit = stdout, stderr, exitCode
	if err := os.MkdirAll(outputPath, 0755); err != nil {
		return fail(err)
	}
	if err := os.WriteFile(filepath.Join(outputPath, "helm.stdout"), stdout, 0644); err != nil {
		return fail(err)
	}
	if err := os.WriteFile(filepath.Join(outputPath, "helm.stderr"), stderr, 0644); err != nil {
		return fail(err)
	}
	hash := sha256.Sum256(stdout)
	report := map[string]any{"command": append([]string{*helm}, command...), "toolVersion": toolVersion, "exitCode": exitCode, "targetVersion": version, "releaseName": *release, "namespace": *namespace, "kubeVersion": *kube, "apiVersions": []string(apiVersions), "stdoutSha256": hex.EncodeToString(hash[:]), "generatedFile": filepath.Join(outputPath, "rendered.yaml"), "chart": chartPath, "inputSha256": inputs, "inputsUnchanged": inputsUnchanged}
	save := func() {
		data, _ := json.MarshalIndent(report, "", "  ")
		if err := os.WriteFile(filepath.Join(outputPath, "render-report.json"), append(data, '\n'), 0644); err != nil {
			fmt.Fprintln(os.Stderr, err)
		}
	}
	save()
	if executionErr != nil {
		return fail(fmt.Errorf("Helm exited %d: %s: %w", exitCode, stderr, executionErr))
	}
	if !inputsUnchanged {
		return fail(fmt.Errorf("render inputs changed during Helm execution; discard this output and run render-check again"))
	}
	if err := os.WriteFile(filepath.Join(outputPath, "rendered.yaml"), stdout, 0644); err != nil {
		return fail(err)
	}
	code := runContext(ctx, []string{"check", "--root", outputPath, "--target-version", version, "--format", "json"})
	report["checkExitCode"] = code
	save()
	return code
}

func lintOfficial(args []string) int {
	flags := flag.NewFlagSet("lint-official", flag.ContinueOnError)
	flags.SetOutput(os.Stderr)
	root := flags.String("root", ".", "source root")
	scope := flags.String("scope", "", "source scope")
	target := flags.String("target-version", "", "exact target")
	argo := flags.String("argo-binary", "", "absolute Argo CLI path")
	if flags.Parse(args) != nil || flags.NArg() != 0 {
		return 2
	}
	ctx, cancel := commandContext()
	defer cancel()
	var rawStdout, rawStderr []byte
	rawExit := 0
	fail := func(err error) int { return commandFailure(ctx, "lint-official", err, rawStdout, rawStderr, rawExit) }
	if !filepath.IsAbs(*argo) {
		return fail(fmt.Errorf("--argo-binary must be an absolute path"))
	}
	version, err := selectedTarget(ctx, *root, *scope, *target)
	if err != nil {
		return fail(err)
	}
	w, err := workspace.LoadContext(ctx, *root, *scope, *target)
	if err != nil {
		return fail(err)
	}
	if len(w.Documents) == 0 {
		return fail(fmt.Errorf("no YAML input in selected scope"))
	}
	temp, err := os.MkdirTemp("", "argo-workflows-lint-")
	if err != nil {
		return fail(err)
	}
	defer os.RemoveAll(temp)
	kubeconfig := filepath.Join(temp, "kubeconfig")
	if err := os.WriteFile(kubeconfig, []byte("apiVersion: v1\nkind: Config\nclusters: []\ncontexts: []\nusers: []\n"), 0600); err != nil {
		return fail(err)
	}
	env := []string{"PATH=" + os.Getenv("PATH"), "LANG=C.UTF-8", "KUBECONFIG=" + kubeconfig}
	versionOut, versionErr, versionCode, err := toolRun(ctx, *argo, []string{"version", "--short"}, env)
	rawStdout, rawStderr, rawExit = versionOut, versionErr, versionCode
	toolVersion := strings.TrimSpace(string(versionOut))
	if err != nil {
		return fail(fmt.Errorf("Argo version failed (%d): %s: %w", versionCode, versionErr, err))
	}
	if toolVersion != "argo: v"+version {
		return fail(fmt.Errorf("Argo CLI version %q does not match exact target %s", toolVersion, version))
	}
	helpOut, helpErr, helpCode, helpError := toolRun(ctx, *argo, []string{"lint", "--help"}, env)
	rawStdout, rawStderr, rawExit = helpOut, helpErr, helpCode
	if helpError != nil || !bytes.Contains(helpOut, []byte("--offline")) {
		return fail(fmt.Errorf("official offline lint is unavailable: %s", helpErr))
	}
	command := []string{"lint", "--offline", "--no-color", "--output", "simple", "--kubeconfig", kubeconfig}
	inputHashes := map[string]string{}
	for _, document := range w.Documents {
		path, err := fileuri.Path(string(document.Snapshot.URI()))
		if err != nil {
			return fail(err)
		}
		command = append(command, path)
		sum := sha256.Sum256([]byte(document.Snapshot.Text()))
		inputHashes[path] = hex.EncodeToString(sum[:])
	}
	stdout, stderr, exitCode, executionErr := toolRun(ctx, *argo, command, env)
	report := map[string]any{"operation": "lint-official", "product": "argo-workflows", "toolVersion": toolVersion, "targetVersion": version, "command": append([]string{*argo}, command...), "toolExitCode": exitCode, "stdout": string(stdout), "stderr": string(stderr), "inputHashes": inputHashes, "analysisComplete": false}
	if ctx.Err() != nil {
		report["error"] = ctx.Err().Error()
		emit(report)
		return 3
	}
	if executionErr != nil {
		report["error"] = executionErr.Error()
	}
	if exitCode == 0 && executionErr == nil && strings.TrimSpace(string(stdout)) == "no linting errors found!" {
		report["officialResult"] = "passed"
		emit(report)
		return 0
	}
	if exitCode == 1 {
		report["officialResult"] = "failed"
		emit(report)
		return 1
	}
	report["officialResult"] = "unavailable"
	if exitCode == 0 {
		report["error"] = "unrecognized official lint success output"
	}
	emit(report)
	return 2
}

// Bind render results to the selected local chart, values, executable and pin.
// Inputs are bounded and regular files; chart symlinks would hide dependencies.
func renderInputs(ctx context.Context, chart, helm, root string, values []string) (map[string]string, error) {
	result := map[string]string{}
	remaining := int64(128 * 1024 * 1024)
	add := func(name string) error {
		if err := ctx.Err(); err != nil {
			return err
		}
		absolute, err := filepath.Abs(name)
		if err != nil {
			return err
		}
		info, err := os.Stat(absolute)
		if err != nil {
			return err
		}
		if !info.Mode().IsRegular() {
			return fmt.Errorf("render input is not a regular file: %s", absolute)
		}
		if info.Size() > remaining {
			return fmt.Errorf("render input provenance exceeds 128 MiB")
		}
		file, err := os.Open(absolute)
		if err != nil {
			return err
		}
		defer file.Close()
		hash := sha256.New()
		count, err := io.Copy(hash, io.LimitReader(file, remaining+1))
		if err != nil {
			return err
		}
		remaining -= count
		if remaining < 0 {
			return fmt.Errorf("render input provenance exceeds 128 MiB")
		}
		result[absolute] = hex.EncodeToString(hash.Sum(nil))
		return nil
	}
	err := filepath.WalkDir(chart, func(path string, entry os.DirEntry, err error) error {
		if err != nil {
			return err
		}
		if ctx.Err() != nil {
			return ctx.Err()
		}
		if entry.Type()&os.ModeSymlink != 0 {
			return fmt.Errorf("chart symlinks prevent complete input provenance: %s", path)
		}
		if entry.IsDir() {
			return nil
		}
		if len(result) >= 20000 {
			return fmt.Errorf("render input provenance exceeds 20000 files")
		}
		return add(path)
	})
	if err != nil {
		return nil, err
	}
	for _, value := range values {
		if err := add(value); err != nil {
			return nil, err
		}
	}
	if err := add(helm); err != nil {
		return nil, err
	}
	config, err := filepath.Abs(filepath.Join(root, ".argo-workflows-ls.yaml"))
	if err != nil {
		return nil, err
	}
	if _, err := os.Stat(config); os.IsNotExist(err) {
		result[config] = "absent"
	} else if err != nil {
		return nil, err
	} else if err := add(config); err != nil {
		return nil, err
	}
	return result, nil
}
