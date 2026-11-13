package lsp

import (
	"encoding/json"
	"io/fs"
	"path/filepath"
	"strings"

	"github.com/lazysnail1024/k8s-crd-toolkit/source"
	"github.com/lazysnail1024/k8s-crd-toolkit/yamlview"
	"github.com/lazysnail1024/zed-argo-workflows/internal/analysis"
	"github.com/lazysnail1024/zed-argo-workflows/internal/fileuri"
	"github.com/lazysnail1024/zed-argo-workflows/internal/helmsource"
	"github.com/lazysnail1024/zed-argo-workflows/internal/workspace"
	protocol "github.com/tliron/glsp/protocol_3_16"
)

// Helm source queries are deliberately separate from validation and editing.
// The temporary snapshots never enter the normal catalog or query caches.
func (s *session) helmQuery(m message) (bool, error) {
	if len(m.ID) == 0 || !strings.HasPrefix(m.Method, "textDocument/") {
		return false, nil
	}
	var p protocol.TextDocumentPositionParams
	if json.Unmarshal(m.Params, &p) != nil {
		return false, nil
	}
	uri := string(p.TextDocument.URI)
	b, opened := s.buffers[uri]
	name, err := filePath(uri)
	if !opened || err != nil || !workspace.HelmTemplate(s.root(uri), name) || !helmsource.HasActions(b.snapshot.Text()) {
		return false, nil
	}
	if m.Method != "textDocument/definition" {
		return true, s.reply(m.ID, nil)
	}
	projected, ok := helmsource.Project(s.context(), b.snapshot)
	if !ok {
		return true, s.reply(m.ID, nil)
	}
	d, err := s.bufferDocument(uri)
	if err != nil {
		return true, s.failure(m.ID, -32803, err.Error())
	}
	d.Snapshot = projected
	pos := source.Position{Line: p.Position.Line, UTF16Column: p.Position.Character}
	results := analysis.Definitions(s.context(), projected, d.Target.Version, pos)
	chart, err := workspace.NearestChart(name)
	if err != nil {
		return true, s.reply(m.ID, results)
	}
	header, err := workspace.Describe(s.context(), s.root(uri), s.override, nil)
	if err != nil {
		return true, s.failure(m.ID, -32803, err.Error())
	}
	w := header
	w.Documents = nil
	count := 0
	err = filepath.WalkDir(filepath.Join(chart, "templates"), func(path string, entry fs.DirEntry, walkErr error) error {
		if walkErr != nil {
			return walkErr
		}
		if s.context().Err() != nil {
			return s.context().Err()
		}
		if entry.IsDir() {
			return nil
		}
		if entry.Type()&fs.ModeSymlink != 0 || filepath.Ext(path) != ".yaml" && filepath.Ext(path) != ".yml" {
			return nil
		}
		count++
		if count > 5000 {
			return fs.ErrInvalid
		}
		other, e := fileuri.FromPath(path)
		if e != nil {
			return e
		}
		var snapshot source.Snapshot
		if buffer, found := s.buffers[other]; found {
			if buffer.unavailable != "" {
				return fs.ErrInvalid
			}
			snapshot = buffer.snapshot
		} else {
			text, e := workspace.ReadText(s.context(), path)
			if e != nil {
				return e
			}
			snapshot, e = source.New(source.URI(other), 1, text)
			if e != nil {
				return e
			}
		}
		original := snapshot
		if helmsource.HasActions(snapshot.Text()) {
			var safe bool
			snapshot, safe = helmsource.Project(s.context(), snapshot)
			if !safe {
				return fs.ErrInvalid
			}
		}
		if !s.staticSourceIdentity(snapshot) {
			return fs.ErrInvalid
		}
		doc, e := workspace.OpenDocument(header, original, s.override)
		if e != nil {
			return e
		}
		doc.Snapshot = snapshot
		if doc.Target.ID == d.Target.ID {
			w.Documents = append(w.Documents, doc)
		}
		return nil
	})
	if err == nil {
		results = append(results, analysis.ExternalDefinitions(s.context(), w, uri, pos)...)
	}
	return true, s.reply(m.ID, results)
}

// The normal YAML resolver treats namespace as a literal string. A Helm
// placeholder cannot establish identity, even if two masked values coincide.
// Alias/merge identity requires rendered values and is deliberately declined.
func (s *session) staticSourceIdentity(snapshot source.Snapshot) bool {
	view, err := yamlview.Parse(s.context(), snapshot)
	if err != nil {
		return false
	}
	literal := func(n yamlview.Node) bool { return n.Kind() == yamlview.Scalar && !strings.Contains(n.Raw(), "{{") }
	for _, doc := range view.Documents() {
		if !doc.Complete() {
			return false
		}
		// Workflow and CronWorkflow runtime names cannot be templateRef targets.
		// Their namespace still selects namespaced templates. Template resources
		// retain the name guard because an unknown name may alias a known candidate.
		callerOnly := false
		for _, member := range doc.Root().Members() {
			if strings.Trim(member.Key.Raw(), "\"'") == "kind" && literal(member.Value) {
				kind := strings.Trim(member.Value.Raw(), "\"'")
				callerOnly = kind == "Workflow" || kind == "CronWorkflow"
			}
		}
		for _, member := range doc.Root().Members() {
			key := strings.Trim(member.Key.Raw(), "\"'")
			if key == "<<" || strings.Contains(member.Key.Raw(), "\\") {
				return false
			}
			if (key == "apiVersion" || key == "kind") && !literal(member.Value) {
				return false
			}
			if key != "metadata" {
				continue
			}
			if member.Value.Kind() != yamlview.Mapping {
				return false
			}
			for _, field := range member.Value.Members() {
				name := strings.Trim(field.Key.Raw(), "\"'")
				if strings.Contains(field.Key.Raw(), "\\") {
					return false
				}
				if name == "<<" || (name == "namespace" || name == "name" && !callerOnly) && !literal(field.Value) {
					return false
				}
			}
		}
	}
	return true
}
