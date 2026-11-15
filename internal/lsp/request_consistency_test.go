package lsp

import (
	"context"
	"encoding/json"
	"io"
	"os"
	"path/filepath"
	"strings"
	"testing"

	"github.com/lazysnail1024/k8s-crd-toolkit/source"
	"github.com/lazysnail1024/zed-argo-workflows/internal/analysis"
	"github.com/lazysnail1024/zed-argo-workflows/internal/fileuri"
	"github.com/lazysnail1024/zed-argo-workflows/internal/workspace"
)

func TestReadOnlyClosureKeepsSnapshotsWithTheirProofs(t *testing.T) {
	c := testCatalogNoWorkers(t)
	root := t.TempDir()
	if err := os.WriteFile(filepath.Join(root, ".argo-workflows-ls.yaml"), []byte("schemaVersion: 1\ntargetVersion: '3.6.0'\nreferencePolicy: closed\n"), 0600); err != nil {
		t.Fatal(err)
	}
	header, err := workspace.Describe(context.Background(), root, "", nil)
	if err != nil {
		t.Fatal(err)
	}
	c.roots[root] = &catalogRoot{header: header, identity: catalogIdentity(header, ""), done: true,
		epoch: 1, scanEpoch: 1, priority: map[string]bool{}}
	owner := session{catalog: c, roots: []string{root}, initialized: true, buffers: map[string]buffer{},
		pending: map[string]bool{}, actions: map[string]pendingAction{}, out: io.Discard, log: io.Discard}
	accept := func(method string, params any) {
		t.Helper()
		raw, err := json.Marshal(params)
		if err != nil {
			t.Fatal(err)
		}
		if _, _, err := owner.accept(message{Method: method, Params: raw}); err != nil {
			t.Fatal(err)
		}
	}
	callerText := "apiVersion: argoproj.io/v1alpha1\nkind: Workflow\nmetadata: {name: caller, namespace: argo}\nspec:\n  entrypoint: main\n  templates:\n    - name: main\n      steps:\n        - - name: call\n            templateRef: {name: library, template: work}\n"
	libraryText := "apiVersion: argoproj.io/v1alpha1\nkind: WorkflowTemplate\nmetadata: {name: library, namespace: argo}\nspec:\n  templates:\n    - name: work\n      container: {image: alpine}\n"
	addresses := map[string]string{}
	for name, text := range map[string]string{"caller.yaml": callerText, "library.yaml": libraryText} {
		uri, err := fileuri.FromPath(filepath.Join(root, name))
		if err != nil {
			t.Fatal(err)
		}
		addresses[name] = uri
		accept("textDocument/didOpen", map[string]any{"textDocument": map[string]any{"uri": uri, "version": 1, "text": text}})
	}
	drain := func() {
		t.Helper()
		for n := 0; n < 8; n++ {
			job, ok := c.queue.Pop()
			if !ok {
				return
			}
			c.process(job)
		}
		t.Fatal("fixture jobs did not settle")
	}
	drain()
	caller, library := addresses["caller.yaml"], addresses["library.yaml"]
	params, err := json.Marshal(map[string]any{"textDocument": map[string]string{"uri": caller}})
	if err != nil {
		t.Fatal(err)
	}
	request := owner.queueRequest(message{Method: "textDocument/definition", Params: params})
	worker := owner
	worker.buffers = map[string]buffer{}
	for uri, b := range owner.buffers {
		worker.buffers[uri] = b
	}
	worker.method, worker.focus = "textDocument/definition", caller
	worker.ctx = analysis.WithInvocation(context.Background())
	worker.workspaces = map[workspaceKey]workspace.Workspace{}
	moved := "# new dependency location\n" + libraryText
	accept("textDocument/didChange", map[string]any{"textDocument": map[string]any{"uri": library, "version": 2},
		"contentChanges": []map[string]string{{"text": moved}}})
	drain()
	if !owner.requestCurrent(request) || worker.buffers[library].snapshot.Text() != libraryText {
		t.Fatal("fixture must keep an admissible caller and the worker's old dependency snapshot")
	}
	w, err := worker.workspace(root, "")
	if err != nil {
		t.Fatal(err)
	}
	if !c.valid(worker.proofs) {
		t.Fatal("a stable, current dependency closure did not provide a valid proof")
	}
	found := false
	for _, document := range w.Documents {
		if string(document.Snapshot.URI()) == library {
			found = document.Snapshot.Text() == moved && document.Snapshot.Revision() == owner.buffers[library].snapshot.Revision()
		}
	}
	if !found {
		t.Fatal("new catalog proof was paired with the worker's old dependency source")
	}
	offset := strings.Index(callerText, "template: work") + len("template: ")
	position, err := owner.buffers[caller].snapshot.Position(offset)
	if err != nil {
		t.Fatal(err)
	}
	locations := analysis.ExternalDefinitions(worker.ctx, w, caller, source.Position{Line: position.Line, UTF16Column: position.UTF16Column})
	if len(locations) != 1 || locations[0].URI != library || locations[0].Range.Start.Line != 6 {
		t.Fatalf("navigation accepted an obsolete dependency location: %+v", locations)
	}
}

func TestExplanationRetainsScopeProofUntilPublication(t *testing.T) {
	for _, withPosition := range []bool{false, true} {
		name := "readiness-without-position"
		if withPosition {
			name = "closed-file-refusal-with-positive-references"
		}
		t.Run(name, func(t *testing.T) {
			c := testCatalogNoWorkers(t)
			root := t.TempDir()
			configuration := "schemaVersion: 1\ntargetVersion: '3.6.0'\nreferencePolicy: closed\n"
			if err := os.WriteFile(filepath.Join(root, ".argo-workflows-ls.yaml"), []byte(configuration), 0600); err != nil {
				t.Fatal(err)
			}
			header, err := workspace.Describe(context.Background(), root, "", nil)
			if err != nil {
				t.Fatal(err)
			}
			c.roots[root] = &catalogRoot{header: header, identity: catalogIdentity(header, ""), done: true, epoch: 1, scanEpoch: 1, priority: map[string]bool{}}
			owner := session{catalog: c, roots: []string{root}, initialized: true, versionedEdits: true,
				buffers: map[string]buffer{}, pending: map[string]bool{}, actions: map[string]pendingAction{}, out: io.Discard, log: io.Discard}
			accept := func(method string, params any) {
				t.Helper()
				raw, err := json.Marshal(params)
				if err != nil {
					t.Fatal(err)
				}
				if _, _, err := owner.accept(message{Method: method, Params: raw}); err != nil {
					t.Fatal(err)
				}
			}
			libraryText := "apiVersion: argoproj.io/v1alpha1\nkind: WorkflowTemplate\nmetadata: {name: library, namespace: argo}\nspec:\n  templates:\n    - name: work\n      container: {image: alpine}\n"
			callerText := "apiVersion: argoproj.io/v1alpha1\nkind: Workflow\nmetadata: {name: caller, namespace: argo}\nspec:\n  workflowTemplateRef: {name: library}\n"
			library, err := fileuri.FromPath(filepath.Join(root, "library.yaml"))
			if err != nil {
				t.Fatal(err)
			}
			caller, err := fileuri.FromPath(filepath.Join(root, "caller.yaml"))
			if err != nil {
				t.Fatal(err)
			}
			if err := os.WriteFile(filepath.Join(root, "caller.yaml"), []byte(callerText), 0600); err != nil {
				t.Fatal(err)
			}
			accept("textDocument/didOpen", map[string]any{"textDocument": map[string]any{"uri": library, "version": 1, "text": libraryText}})
			accept("workspace/didChangeWatchedFiles", map[string]any{"changes": []map[string]any{{"uri": caller, "type": 1}}})
			for n := 0; n < 8; n++ {
				job, ok := c.queue.Pop()
				if !ok {
					break
				}
				c.process(job)
			}
			params, err := json.Marshal(map[string]any{"textDocument": map[string]string{"uri": library}})
			if err != nil {
				t.Fatal(err)
			}
			request := owner.queueRequest(message{Method: "argo-workflows/explainAnalysis", Params: params})
			worker := owner
			worker.ctx = analysis.WithInvocation(context.Background())
			worker.method, worker.focus = "argo-workflows/explainAnalysis", library
			worker.proofs = nil
			var position *analysis.Position
			if withPosition {
				position = &analysis.Position{Line: 2, Character: 19}
			}
			status, err := worker.explainAnalysis(library, position)
			if err != nil || !status.Completeness.Workspace || !c.valid(worker.proofs) {
				t.Fatalf("fixture did not produce a ready explanation with valid authority: %+v, %v", status, err)
			}
			if withPosition && (status.References == nil || status.References.Count != 1 || status.Operations["rename"].ReasonCode != "AFFECTED_FILE_NOT_OPEN") {
				t.Fatalf("fixture must contain positive reference facts and a refused edit: %+v", status)
			}
			accept("textDocument/didOpen", map[string]any{"textDocument": map[string]any{"uri": caller, "version": 1, "text": callerText + "# newer input\n"}})
			if !owner.requestCurrent(request) {
				t.Fatal("unchanged queried buffer must remain admissible; dependency proof must reject the result")
			}
			if c.valid(worker.proofs) {
				t.Fatal("explanation retained publication authority after its selected-scope input changed")
			}
		})
	}
}
