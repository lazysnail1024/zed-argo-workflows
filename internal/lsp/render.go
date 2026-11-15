package lsp

import (
	"context"
	"crypto/sha256"
	"encoding/hex"
	"encoding/json"
	"errors"
	"fmt"
	"github.com/lazysnail1024/k8s-crd-toolkit/source"
	"github.com/lazysnail1024/zed-argo-workflows/internal/fileuri"
	"os"
	"path/filepath"
	"sort"
	"strings"

	"github.com/lazysnail1024/zed-argo-workflows/internal/analysis"
	"github.com/lazysnail1024/zed-argo-workflows/internal/helmsource"
	"github.com/lazysnail1024/zed-argo-workflows/internal/workspace"
)

// RenderCommand executes the same bounded explicit CLI operation used outside
// Zed. The command entry point supplies it; analysis never starts programs.
type RenderCommand func(context.Context, workspace.RenderRequest) (map[string]any, error)

func (s *session) renderDocument(uri string) (string, error) {
	root := s.root(uri)
	b, ok := s.buffers[uri]
	if !ok || root == "" || b.unavailable != "" {
		return "", fmt.Errorf("render requires an available open chart document")
	}
	name, err := filePath(uri)
	if err != nil {
		return "", err
	}
	chart, err := workspace.NearestChart(name)
	if err != nil {
		return "", err
	}
	relative, err := filepath.Rel(root, chart)
	if err != nil || relative == ".." || strings.HasPrefix(relative, ".."+string(filepath.Separator)) {
		return "", fmt.Errorf("chart is outside workspace")
	}
	realRoot, rootErr := filepath.EvalSymlinks(root)
	realChart, chartErr := filepath.EvalSymlinks(chart)
	realRelative, relErr := filepath.Rel(realRoot, realChart)
	if rootErr != nil || chartErr != nil || relErr != nil || realRelative == ".." || strings.HasPrefix(realRelative, ".."+string(filepath.Separator)) {
		return "", fmt.Errorf("chart is outside workspace")
	}
	if !workspace.HelmTemplate(root, name) {
		return "", fmt.Errorf("select a Workflow chart template")
	}
	snapshot := b.snapshot
	if helmsource.HasActions(snapshot.Text()) {
		if projected, safe := helmsource.Identity(snapshot); safe {
			snapshot = projected
		}
	}
	owned, err := analysis.OwnsSnapshot(s.context(), snapshot)
	if err != nil || !owned {
		return "", fmt.Errorf("select an Argo Workflows chart template")
	}
	return chart, nil
}

type renderArgument struct {
	URI             string          `json:"uri"`
	Root            string          `json:"root"`
	Version         int32           `json:"version"`
	Revision        source.Revision `json:"revision"`
	Epoch           uint64          `json:"epoch"`
	RootEpoch       uint64          `json:"rootEpoch"`
	Profile         string          `json:"profile"`
	RequestIdentity string          `json:"requestIdentity"`
}

func (s *session) renderRootEpoch(uri string) uint64 {
	s.catalog.mu.Lock()
	defer s.catalog.mu.Unlock()
	if root := s.catalog.roots[s.root(uri)]; root != nil {
		return root.inputEpoch
	}
	return 0
}

func (s *session) resolveRender(uri, profile string) (workspace.RenderRequest, error) {
	if _, err := s.renderDocument(uri); err != nil {
		return workspace.RenderRequest{}, err
	}
	name, err := filePath(uri)
	if err != nil {
		return workspace.RenderRequest{}, err
	}
	return workspace.ResolveRenderRequest(s.context(), workspace.RenderOptions{Root: s.root(uri), File: name, TargetVersion: s.override, Profile: profile, RequireChartInRoot: true})
}

func (s *session) currentRender(arg renderArgument) (workspace.RenderRequest, error) {
	b, opened := s.buffers[arg.URI]
	if !opened || b.version != arg.Version || b.snapshot.Revision() != arg.Revision || s.epoch != arg.Epoch || s.root(arg.URI) != arg.Root || s.renderRootEpoch(arg.URI) != arg.RootEpoch {
		return workspace.RenderRequest{}, workspace.ErrRenderInputsChanged
	}
	request, err := s.resolveRender(arg.URI, arg.Profile)
	if err != nil || request.RequestIdentity != arg.RequestIdentity {
		return workspace.RenderRequest{}, workspace.ErrRenderInputsChanged
	}
	return request, nil
}

func renderInside(root, name string) bool {
	rel, err := filepath.Rel(root, name)
	return err == nil && rel != ".." && !strings.HasPrefix(rel, ".."+string(filepath.Separator))
}

// Every participating open buffer must agree with saved disk bytes, including
// values outside the chart and symlink/hard-link aliases of those inputs.
func (s *session) renderSaved(request workspace.RenderRequest) error {
	for uri, b := range s.buffers {
		name, err := filePath(uri)
		if err != nil {
			continue
		}
		real, _ := filepath.EvalSymlinks(name)
		affected := renderInside(request.Chart, name) || real != "" && renderInside(request.ChartRealPath, real)
		inputPath := ""
		info, _ := os.Stat(name)
		for path, resolved := range request.InputRealPaths {
			if name == path || real != "" && real == resolved {
				affected, inputPath = true, path
				break
			}
			if info != nil {
				other, e := os.Stat(path)
				if e == nil && os.SameFile(info, other) {
					affected, inputPath = true, path
					break
				}
			}
		}
		if !affected {
			continue
		}
		if b.unavailable != "" {
			return fmt.Errorf("save all chart buffers, configuration and selected values before render-check: %s", name)
		}
		if inputPath != "" {
			hash := sha256.Sum256([]byte(b.snapshot.Text()))
			if hex.EncodeToString(hash[:]) == request.InputSHA256[inputPath] {
				continue
			}
		} else {
			disk, err := workspace.ReadText(s.context(), name)
			if err == nil && disk == b.snapshot.Text() {
				continue
			}
		}
		return fmt.Errorf("save all chart buffers, configuration and selected values before render-check: %s", name)
	}
	return nil
}

func (s *session) renderRequest(m message) (bool, error) {
	if s.render == nil {
		return false, nil
	}
	if m.Method == "textDocument/codeAction" {
		var p struct {
			TextDocument struct{ URI string } `json:"textDocument"`
		}
		if json.Unmarshal(m.Params, &p) != nil {
			return false, nil
		}
		if _, err := s.renderDocument(p.TextDocument.URI); err != nil {
			return false, nil
		}
		if s.versionedEdits && !helmsource.HasActions(s.buffers[p.TextDocument.URI].snapshot.Text()) {
			return false, nil
		}
		return true, s.reply(m.ID, s.withRenderAction(p.TextDocument.URI, nil))
	}
	if m.Method != "workspace/executeCommand" && m.Method != "codeAction/resolve" {
		return false, nil
	}
	params := m.Params
	if m.Method == "codeAction/resolve" {
		var action struct{ Command json.RawMessage }
		if json.Unmarshal(params, &action) != nil || len(action.Command) == 0 {
			return false, nil
		}
		params = action.Command
	}
	var identity struct{ Command string }
	if json.Unmarshal(params, &identity) != nil || identity.Command != "argo-workflows.renderCheck" {
		return false, nil
	}
	var command struct {
		Command   string
		Arguments []renderArgument
	}
	if json.Unmarshal(params, &command) != nil || len(command.Arguments) != 1 {
		return true, s.failure(m.ID, -32602, "invalid render action arguments")
	}
	request, err := s.currentRender(command.Arguments[0])
	if err != nil {
		return true, s.failure(m.ID, -32801, err.Error())
	}
	if m.Method == "codeAction/resolve" {
		return true, s.reply(m.ID, json.RawMessage(m.Params))
	}
	if err := s.renderSaved(request); err != nil {
		return true, s.failure(m.ID, -32801, err.Error())
	}
	result, err := s.render(s.context(), request)
	if err != nil {
		code := -32803
		if errors.Is(err, workspace.ErrRenderInputsChanged) {
			code = -32801
		}
		if errors.Is(err, context.Canceled) || errors.Is(err, context.DeadlineExceeded) {
			code = -32800
		}
		return true, s.failure(m.ID, code, err.Error())
	}
	// The worker owner also checks accepted input authority before publishing
	// these messages. Disk-only changes are rechecked here independently.
	if !request.Current(s.context()) {
		return true, s.failure(m.ID, -32801, workspace.ErrRenderInputsChanged.Error())
	}
	if err := s.reply(m.ID, result); err != nil {
		return true, err
	}
	message := fmt.Sprintf("Argo Workflows render-check finished (exit %v). Generated YAML: %v", result["exitCode"], result["generatedFile"])
	if err := s.write(map[string]any{"jsonrpc": "2.0", "method": "window/showMessage", "params": map[string]any{"type": 3, "message": message}}); err != nil {
		return true, err
	}
	if s.showDocument {
		generated, _ := result["generatedFile"].(string)
		uri, err := fileuri.FromPath(generated)
		if err != nil {
			return true, err
		}
		if err := s.write(map[string]any{"jsonrpc": "2.0", "id": "argo-workflows-render:" + uri, "method": "window/showDocument", "params": map[string]any{"uri": uri, "external": false, "takeFocus": true}}); err != nil {
			return true, err
		}
	}
	return true, nil
}

func (s *session) withRenderAction(uri string, actions []map[string]any) []map[string]any {
	if s.render == nil || !s.explainActionAllowed {
		return actions
	}
	if _, err := s.renderDocument(uri); err != nil {
		return actions
	}
	root := s.root(uri)
	header, err := workspace.Describe(s.context(), root, s.override, nil)
	if err != nil {
		return actions
	}
	names := []string{""}
	for name := range header.Config.RenderProfiles {
		names = append(names, name)
	}
	sort.Strings(names)
	b := s.buffers[uri]
	for _, profile := range names {
		request, err := s.resolveRender(uri, profile)
		if err != nil {
			continue
		}
		title := "Argo Workflows: render and check saved chart"
		if profile != "" {
			title += " (" + profile + ")"
		}
		arg := renderArgument{URI: uri, Root: root, Version: b.version, Revision: b.snapshot.Revision(), Epoch: s.epoch, RootEpoch: s.renderRootEpoch(uri), Profile: profile, RequestIdentity: request.RequestIdentity}
		actions = append(actions, map[string]any{"title": title, "kind": "source", "command": map[string]any{"title": title, "command": "argo-workflows.renderCheck", "arguments": []renderArgument{arg}}})
	}
	return actions
}
