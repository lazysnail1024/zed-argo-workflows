package lsp

import (
	"context"
	"encoding/json"
	"fmt"
	"github.com/lazysnail1024/zed-argo-workflows/internal/fileuri"
	"path/filepath"
	"strings"

	"github.com/lazysnail1024/zed-argo-workflows/internal/analysis"
	"github.com/lazysnail1024/zed-argo-workflows/internal/helmsource"
	"github.com/lazysnail1024/zed-argo-workflows/internal/workspace"
)

// RenderCommand executes the same bounded explicit CLI operation used outside
// Zed. The command entry point supplies it; analysis never starts programs.
type RenderCommand func(context.Context, string, string, string) (map[string]any, error)

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
func (s *session) renderRequest(m message) (bool, error) {
	if s.render == nil {
		return false, nil
	}
	if m.Method == "codeAction/resolve" {
		var action struct {
			Command struct {
				Command   string   `json:"command"`
				Arguments []string `json:"arguments"`
			} `json:"command"`
		}
		if json.Unmarshal(m.Params, &action) == nil && action.Command.Command == "argo-workflows.renderCheck" {
			args := action.Command.Arguments
			if len(args) != 2 {
				return true, s.failure(m.ID, -32602, "invalid render action")
			}
			if _, err := s.renderDocument(args[0]); err != nil {
				return true, s.failure(m.ID, -32602, err.Error())
			}
			if args[1] != fmt.Sprint(s.buffers[args[0]].version) {
				return true, s.failure(m.ID, -32801, "render action is stale")
			}
			return true, s.reply(m.ID, json.RawMessage(m.Params))
		}
	}
	if m.Method == "textDocument/codeAction" {
		var p struct {
			TextDocument struct {
				URI string `json:"uri"`
			} `json:"textDocument"`
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
	if m.Method != "workspace/executeCommand" {
		return false, nil
	}
	var p struct {
		Command   string   `json:"command"`
		Arguments []string `json:"arguments"`
	}
	if json.Unmarshal(m.Params, &p) != nil || p.Command != "argo-workflows.renderCheck" || len(p.Arguments) != 2 {
		return true, s.failure(m.ID, -32602, "unknown command or invalid arguments")
	}
	uri := p.Arguments[0]
	chart, err := s.renderDocument(uri)
	if err != nil {
		return true, s.failure(m.ID, -32602, err.Error())
	}
	if p.Arguments[1] != fmt.Sprint(s.buffers[uri].version) {
		return true, s.failure(m.ID, -32801, "render action is stale; request a new action")
	}
	for other, b := range s.buffers {
		name, e := filePath(other)
		if e != nil {
			continue
		}
		rel, e := filepath.Rel(chart, name)
		config := name == filepath.Join(s.root(uri), ".argo-workflows-ls.yaml")
		if !config && (e != nil || rel == ".." || strings.HasPrefix(rel, ".."+string(filepath.Separator))) {
			continue
		}
		disk, e := workspace.ReadText(s.context(), name)
		if e != nil || b.unavailable != "" || disk != b.snapshot.Text() {
			return true, s.failure(m.ID, -32801, "save all chart buffers before render-check")
		}
	}
	name, _ := filePath(uri)
	result, err := s.render(s.context(), s.root(uri), name, s.override)
	if err != nil {
		return true, s.failure(m.ID, -32803, err.Error())
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
	return true, s.reply(m.ID, result)
}

func (s *session) withRenderAction(uri string, actions []map[string]any) []map[string]any {
	if s.render == nil {
		return actions
	}
	if _, err := s.renderDocument(uri); err != nil {
		return actions
	}
	title := "Argo Workflows: render and check saved chart"
	return append(actions, map[string]any{"title": title, "kind": "source", "command": map[string]any{"title": title, "command": "argo-workflows.renderCheck", "arguments": []string{uri, fmt.Sprint(s.buffers[uri].version)}}})
}
