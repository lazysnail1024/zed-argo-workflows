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
	"github.com/lazysnail1024/zed-argo-workflows/internal/rendercache"
	"github.com/lazysnail1024/zed-argo-workflows/internal/workspace"
	"io"
	"os"
	"os/exec"
	"os/signal"
	"path/filepath"
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
func selectTarget(config workspace.Config, scope, override string) (string, error) {
	if len(config.Scopes) > 1 && scope == "" {
		return "", fmt.Errorf("multiple scopes require explicit --scope")
	}
	target := config.TargetVersion
	for _, s := range config.Scopes {
		if s.Name == scope || scope == "" && len(config.Scopes) == 1 {
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
	ctx, cancel := commandContext()
	defer cancel()
	return renderCheckContext(ctx, args, os.Stdout, os.Stderr)
}
func renderCheckContext(ctx context.Context, args []string, outputWriter, errorWriter io.Writer) int {
	flags := flag.NewFlagSet("render-check", flag.ContinueOnError)
	flags.SetOutput(errorWriter)
	options := workspace.RenderOptions{}
	flags.StringVar(&options.Root, "root", ".", "source root")
	flags.StringVar(&options.Scope, "scope", "", "source scope")
	flags.StringVar(&options.TargetVersion, "target-version", "", "exact target")
	flags.StringVar(&options.Profile, "profile", "", "named saved render profile")
	flags.StringVar(&options.Chart, "chart", "", "local chart directory (default: nearest Chart.yaml)")
	flags.StringVar(&options.File, "file", "", "current saved chart file")
	helm := flags.String("helm-binary", "", "absolute Helm 3 or 4 path (default: PATH)")
	output := flags.String("output", "", "generated input directory (custom output is never cleanup-owned)")
	flags.StringVar(&options.ReleaseName, "release-name", "argo-ls", "release name")
	flags.StringVar(&options.Namespace, "namespace", "default", "namespace")
	flags.StringVar(&options.KubeVersion, "kube-version", "1.31.0", "Kubernetes capabilities version")
	var values, apiVersions arguments
	flags.Var(&values, "values", "local values file (repeat in override order)")
	flags.Var(&apiVersions, "api-version", "extra capability")
	if flags.Parse(args) != nil || flags.NArg() != 0 {
		return 2
	}
	options.Values, options.APIVersions = values, apiVersions
	var conflict string
	flags.Visit(func(f *flag.Flag) {
		switch f.Name {
		case "chart", "values", "release-name", "namespace", "kube-version", "api-version":
			conflict = f.Name
		}
	})
	if options.Profile != "" && conflict != "" {
		fmt.Fprintln(errorWriter, "--profile cannot be combined with --"+conflict)
		return 2
	}
	request, err := workspace.ResolveRenderRequest(ctx, options)
	if err != nil {
		return renderFailure(ctx, outputWriter, errorWriter, err, nil, nil, 0)
	}
	code, _, _ := renderResolved(ctx, request, *helm, *output, outputWriter, errorWriter)
	return code
}

func renderFailure(ctx context.Context, outputWriter, errorWriter io.Writer, err error, stdout, stderr []byte, toolExit int) int {
	exit := 2
	if ctx.Err() != nil {
		err, exit = ctx.Err(), 3
	}
	report := map[string]any{"operation": "render-check", "product": "argo-workflows", "analysisComplete": false,
		"error": err.Error(), "toolExitCode": toolExit, "stdout": string(stdout), "stderr": string(stderr)}
	if writeErr := json.NewEncoder(outputWriter).Encode(report); writeErr != nil {
		fmt.Fprintln(errorWriter, writeErr)
		return 2
	}
	return exit
}

// Resolve existing ancestors before checking an output that may not exist yet.
// Lexical separation alone lets an output-directory alias write into a chart.
func renderOutputOutsideChart(chart, output string) error {
	current := output
	var suffix []string
	for {
		real, err := filepath.EvalSymlinks(current)
		if err == nil {
			for i := len(suffix) - 1; i >= 0; i-- {
				real = filepath.Join(real, suffix[i])
			}
			rel, err := filepath.Rel(chart, real)
			if err != nil || rel == "." || rel != ".." && !strings.HasPrefix(rel, ".."+string(filepath.Separator)) {
				return fmt.Errorf("output directory must be outside the chart input directory")
			}
			return nil
		}
		if !os.IsNotExist(err) {
			return err
		}
		if _, exists := os.Lstat(current); exists == nil {
			return fmt.Errorf("output ancestor is an unresolved symlink: %s", current)
		}
		parent := filepath.Dir(current)
		if parent == current {
			return err
		}
		suffix = append(suffix, filepath.Base(current))
		current = parent
	}
}

// Custom output has no ownership marker and is never cleanup-owned. Hold its
// actual directory across the tool lifetime so replacing the visible pathname
// cannot redirect writes into source or another directory. Every artifact is
// created once and exclusively; an unexpected existing leaf is never truncated.
type customRenderOutput struct {
	root       *os.Root
	path, real string
	identity   os.FileInfo
}

func openCustomRenderOutput(path string) (*customRenderOutput, error) {
	real, err := filepath.EvalSymlinks(path)
	if err != nil {
		return nil, err
	}
	root, err := os.OpenRoot(real)
	if err != nil {
		return nil, err
	}
	file, err := root.Open(".")
	if err != nil {
		root.Close()
		return nil, err
	}
	info, err := file.Stat()
	file.Close()
	if err != nil {
		root.Close()
		return nil, err
	}
	output := &customRenderOutput{root: root, path: path, real: real, identity: info}
	if err := output.check(); err != nil {
		root.Close()
		return nil, err
	}
	return output, nil
}

func (output *customRenderOutput) check() error {
	real, err := filepath.EvalSymlinks(output.path)
	info, statErr := os.Stat(output.path)
	if err != nil || statErr != nil || real != output.real || !info.IsDir() || !os.SameFile(info, output.identity) {
		return fmt.Errorf("custom output directory changed during render; refusing redirected writes or publication")
	}
	return nil
}

func (output *customRenderOutput) write(name string, data []byte) error {
	if err := output.check(); err != nil {
		return err
	}
	file, err := output.root.OpenFile(name, os.O_WRONLY|os.O_CREATE|os.O_EXCL, 0644)
	if err != nil {
		return err
	}
	count, writeErr := file.Write(data)
	closeErr := file.Close()
	if writeErr != nil {
		return writeErr
	}
	if closeErr != nil {
		return closeErr
	}
	if count != len(data) {
		return io.ErrShortWrite
	}
	return output.check()
}

// CLI and the editor execute exactly the same resolved request. The output
// store seals raw failure/cancellation evidence too; sealing proves ownership,
// not successful rendering. Preflight failures do not allocate cache entries.
func renderResolved(ctx context.Context, request workspace.RenderRequest, helm, output string, outputWriter, errorWriter io.Writer) (int, string, error) {
	var rawStdout, rawStderr []byte
	rawExit := 0
	var store *rendercache.Store
	var custom *customRenderOutput
	seal := func() error {
		if store != nil {
			return store.Seal()
		}
		return nil
	}
	fail := func(err error) (int, string, error) {
		if sealErr := seal(); sealErr != nil {
			err = fmt.Errorf("%w; sealing render evidence: %v", err, sealErr)
		}
		return renderFailure(ctx, outputWriter, errorWriter, err, rawStdout, rawStderr, rawExit), "", err
	}
	if !request.Current(ctx) {
		return fail(workspace.ErrRenderInputsChanged)
	}
	if helm == "" {
		resolved, err := exec.LookPath("helm")
		if err != nil {
			return fail(fmt.Errorf("Helm not found on PATH: %w", err))
		}
		helm, err = filepath.Abs(resolved)
		if err != nil {
			return fail(err)
		}
	}
	if !filepath.IsAbs(helm) {
		return fail(fmt.Errorf("--helm-binary must be absolute"))
	}
	inputs, realPaths, err := request.Inputs(ctx, helm)
	if err != nil {
		return fail(err)
	}
	if output != "" {
		output, err = filepath.Abs(output)
		if err != nil {
			return fail(err)
		}
		if entries, err := os.ReadDir(output); err == nil && len(entries) > 0 {
			return fail(fmt.Errorf("output directory must be empty"))
		} else if err != nil && !os.IsNotExist(err) {
			return fail(err)
		}
	}
	outputPlacement := output
	if outputPlacement == "" {
		cache, err := os.UserCacheDir()
		if err != nil {
			return fail(err)
		}
		outputPlacement, err = filepath.Abs(filepath.Join(cache, "argo-workflows-ls", "render"))
		if err != nil {
			return fail(err)
		}
	}
	if err := renderOutputOutsideChart(request.ChartRealPath, outputPlacement); err != nil {
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
	versionOut, versionErr, versionCode, err := toolRun(ctx, helm, []string{"version", "--short"}, env)
	rawStdout, rawStderr, rawExit = versionOut, versionErr, versionCode
	toolVersion := strings.TrimSpace(string(versionOut))
	if err != nil {
		return fail(fmt.Errorf("Helm version failed (%d): %s: %w", versionCode, versionErr, err))
	}
	if !strings.HasPrefix(toolVersion, "v3.") && !strings.HasPrefix(toolVersion, "v4.") {
		return fail(fmt.Errorf("Helm 3 or 4 is required; got %s", toolVersion))
	}
	if !request.InputsEqual(ctx, helm, inputs, realPaths) {
		return fail(workspace.ErrRenderInputsChanged)
	}
	if output == "" {
		store, err = rendercache.Create()
		if err != nil {
			return fail(err)
		}
		defer store.Close()
		output = store.Directory
	} else {
		if err := os.MkdirAll(output, 0755); err != nil {
			return fail(err)
		}
		custom, err = openCustomRenderOutput(output)
		if err != nil {
			return fail(err)
		}
		defer custom.root.Close()
	}
	if err := renderOutputOutsideChart(request.ChartRealPath, output); err != nil {
		return fail(err)
	}
	write := func(name string, data []byte) error {
		if store != nil {
			return store.Write(name, data)
		}
		return custom.write(name, data)
	}
	command := request.Arguments()
	stdout, stderr, exitCode, executionErr := toolRun(ctx, helm, command, env)
	rawStdout, rawStderr, rawExit = stdout, stderr, exitCode
	inputsUnchanged := request.InputsEqual(ctx, helm, inputs, realPaths)
	if err := write("helm.stdout", stdout); err != nil {
		return fail(err)
	}
	if err := write("helm.stderr", stderr); err != nil {
		return fail(err)
	}
	hash := sha256.Sum256(stdout)
	generated := filepath.Join(output, "rendered.yaml")
	report := map[string]any{"command": append([]string{helm}, command...), "toolVersion": toolVersion, "exitCode": exitCode,
		"targetVersion": request.TargetVersion, "scope": request.Scope, "root": request.Root, "selectedFile": request.SelectedFile,
		"profile": request.Profile, "values": request.Values, "requestIdentity": request.RequestIdentity,
		"releaseName": request.ReleaseName, "namespace": request.Namespace, "kubeVersion": request.KubeVersion, "apiVersions": request.APIVersions,
		"helmIdentity": map[string]string{"path": helm, "sha256": inputs[helm], "version": toolVersion},
		"stdoutSha256": hex.EncodeToString(hash[:]), "generatedFile": generated, "chart": request.Chart,
		"inputSha256": inputs, "inputRealPaths": realPaths, "inputsUnchanged": inputsUnchanged}
	if executionErr != nil {
		report["error"] = executionErr.Error()
	} else if !inputsUnchanged {
		report["error"] = workspace.ErrRenderInputsChanged.Error()
	}
	if ctx.Err() != nil {
		report["cancelled"] = true
		report["error"] = ctx.Err().Error()
	}
	save := func() error {
		data, err := json.MarshalIndent(report, "", "  ")
		if err != nil {
			return err
		}
		return write("render-report.json", append(data, '\n'))
	}
	failReport := func(reason error) (int, string, error) {
		report["error"] = reason.Error()
		if ctx.Err() != nil {
			report["cancelled"] = true
			report["error"] = ctx.Err().Error()
		}
		if err := save(); err != nil {
			return fail(fmt.Errorf("%w; preserving render report: %v", reason, err))
		}
		return fail(reason)
	}
	if executionErr != nil {
		return failReport(fmt.Errorf("Helm exited %d: %s: %w", exitCode, stderr, executionErr))
	}
	if !inputsUnchanged {
		return failReport(workspace.ErrRenderInputsChanged)
	}
	if err := write("rendered.yaml", stdout); err != nil {
		return failReport(err)
	}
	if custom != nil {
		if err := custom.check(); err != nil {
			return failReport(err)
		}
	}
	var checked bytes.Buffer
	code := runWithIO(ctx, []string{"check", "--root", output, "--target-version", request.TargetVersion, "--format", "json"}, &checked, errorWriter)
	report["checkExitCode"] = code
	if !request.InputsEqual(ctx, helm, inputs, realPaths) {
		report["inputsUnchanged"] = false
		report["error"] = workspace.ErrRenderInputsChanged.Error()
		if ctx.Err() != nil {
			report["cancelled"] = true
			report["error"] = ctx.Err().Error()
		}
		return failReport(workspace.ErrRenderInputsChanged)
	}
	if err := save(); err != nil {
		return fail(err)
	}
	if err := seal(); err != nil {
		return fail(err)
	}
	if custom != nil {
		if err := custom.check(); err != nil {
			return fail(err)
		}
	}
	if ctx.Err() != nil {
		return fail(ctx.Err())
	}
	fmt.Fprintln(errorWriter, "Generated YAML:", generated)
	if _, err := io.Copy(outputWriter, &checked); err != nil {
		return 2, "", err
	}
	return code, generated, nil
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
	w, err := workspace.LoadContext(ctx, *root, *scope, *target)
	if err != nil {
		return fail(err)
	}
	version, err := selectTarget(w.Config, *scope, *target)
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

func renderForEditor(ctx context.Context, request workspace.RenderRequest) (map[string]any, error) {
	var stdout, stderr bytes.Buffer
	code, generated, err := renderResolved(ctx, request, "", "", &stdout, &stderr)
	if ctx.Err() != nil {
		return nil, ctx.Err()
	}
	if err != nil {
		return nil, err
	}
	if code != 0 && code != 1 {
		return nil, fmt.Errorf("render-check failed: %s %s", stdout.String(), stderr.String())
	}
	var report map[string]any
	if err := json.Unmarshal(stdout.Bytes(), &report); err != nil {
		return nil, err
	}
	return map[string]any{"exitCode": code, "generatedFile": generated, "report": report}, nil
}

func renderCache(args []string, outputWriter, errorWriter io.Writer) int {
	flags := flag.NewFlagSet("render-cache", flag.ContinueOnError)
	flags.SetOutput(errorWriter)
	remove := flags.String("remove", "", "remove one verified generated cache directory")
	if flags.Parse(args) != nil || flags.NArg() != 0 {
		return 2
	}
	if *remove != "" {
		if err := rendercache.Remove(*remove); err != nil {
			json.NewEncoder(outputWriter).Encode(map[string]any{"removed": false, "directory": *remove, "error": err.Error()})
			return 2
		}
		if err := json.NewEncoder(outputWriter).Encode(map[string]any{"removed": true, "directory": *remove}); err != nil {
			fmt.Fprintln(errorWriter, err)
			return 2
		}
		return 0
	}
	entries, err := rendercache.List()
	if err != nil {
		fmt.Fprintln(errorWriter, err)
		return 2
	}
	if err := json.NewEncoder(outputWriter).Encode(map[string]any{"entries": entries}); err != nil {
		fmt.Fprintln(errorWriter, err)
		return 2
	}
	return 0
}
