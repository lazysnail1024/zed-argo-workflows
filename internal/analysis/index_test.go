package analysis

import (
	"context"
	"errors"
	"strings"
	"testing"

	"github.com/lazysnail1024/k8s-crd-toolkit/index"
	"github.com/lazysnail1024/k8s-crd-toolkit/source"
	"github.com/lazysnail1024/zed-argo-workflows/internal/workspace"
)

const summaryCaller = "apiVersion: argoproj.io/v1alpha1\nkind: Workflow\nmetadata: {name: caller, namespace: argo}\nspec:\n  entrypoint: main\n  templates:\n    - name: main\n      steps:\n        - - name: call\n            templateRef: {name: library, template: run}\n"
const summaryLibrary = "apiVersion: argoproj.io/v1alpha1\nkind: WorkflowTemplate\nmetadata: {name: library, namespace: argo}\nspec:\n  templates:\n    - name: run\n      container: {image: alpine}\n"

func summaryInput(t *testing.T, uri, text string, revision source.Revision) (workspace.Workspace, workspace.Document, FileSummary) {
	t.Helper()
	snapshot, err := source.New(source.URI(uri), revision, text)
	if err != nil {
		t.Fatal(err)
	}
	target := workspace.Target{ID: "scope", Scope: "root", Version: "3.6.0"}
	document := workspace.Document{Snapshot: snapshot, Target: target}
	header := workspace.Workspace{Config: workspace.Config{ReferencePolicy: "closed"}, Targets: []workspace.Target{target}}
	summary, err := Summarize(WithInvocation(context.Background()), header, document)
	if err != nil {
		t.Fatal(err)
	}
	return header, document, summary
}

func TestSummaryTracksMissingSignatureAndRealLocations(t *testing.T) {
	_, caller, use := summaryInput(t, "file:///caller.yaml", summaryCaller, 1)
	_, library, definition := summaryInput(t, "file:///library.yaml", summaryLibrary, 1)
	idx, err := index.New(index.Limits{Files: 8, Symbols: 16, References: 16, Bytes: 8192})
	if err != nil {
		t.Fatal(err)
	}
	commit := func(document workspace.Document, facts index.Facts) index.Change {
		ticket, err := idx.Begin(document.Snapshot.URI(), "scope", document.Snapshot.Revision())
		if err != nil {
			t.Fatal(err)
		}
		change, err := idx.Commit(ticket, facts)
		if err != nil {
			t.Fatal(err)
		}
		return change
	}
	commit(caller, use.Facts)
	added := commit(library, definition.Facts)
	if len(added.Affected) != 2 || added.Affected[0] != caller.Snapshot.URI() {
		t.Fatalf("missing target did not wake caller: %+v", added)
	}
	span := use.Facts.References[0].Span
	if summaryCaller[span.Start:span.End] != "library" {
		t.Fatalf("reference is not exact source: %+v", span)
	}
	_, moved, movedSummary := summaryInput(t, "file:///library.yaml", "# moved\n\n"+summaryLibrary, 2)
	if movedSummary.Facts.SemanticDigest != definition.Facts.SemanticDigest {
		t.Fatal("comments changed semantic digest")
	}
	change := commit(moved, movedSummary.Facts)
	if change.SemanticChanged || len(change.Affected) != 1 {
		t.Fatalf("location change invalidated callers: %+v", change)
	}
	locations, _ := idx.Definitions("scope", use.Facts.References[0].Target)
	if len(locations) != 1 || moved.Snapshot.Text()[locations[0].Symbol.Selection.Start:locations[0].Symbol.Selection.End] != "library" {
		t.Fatalf("definition location is stale: %+v", locations)
	}
	changed := strings.Replace(summaryLibrary, "      container:", "      inputs: {parameters: [{name: required}]}\n      container:", 1)
	_, required, signature := summaryInput(t, "file:///library.yaml", changed, 3)
	change = commit(required, signature.Facts)
	if !change.SemanticChanged || len(change.Affected) != 2 {
		t.Fatalf("input signature did not invalidate caller: %+v", change)
	}
}

func TestSummaryKeepsInexactDependencyWithoutInventingSpan(t *testing.T) {
	text := strings.Replace(summaryCaller, "namespace: argo}", "namespace: argo, annotations: {identity: &identity library}}", 1)
	text = strings.Replace(text, "name: library, template:", "name: *identity, template:", 1)
	_, _, summary := summaryInput(t, "file:///alias.yaml", text, 1)
	if len(summary.Facts.References) != 0 || len(summary.Facts.Dependencies) != 1 {
		t.Fatalf("alias became a navigable fabricated location: %+v", summary.Facts)
	}
	if summary.Facts.Dependencies[0] != resourceIndexKey("WorkflowTemplate", "argo", "library") {
		t.Fatal("static alias lost its missing-target dependency")
	}
	cluster := strings.Replace(summaryCaller, "name: library,", "clusterScope: true, name: library,", 1)
	_, _, summary = summaryInput(t, "file:///cluster.yaml", cluster, 1)
	if summary.Facts.References[0].Target != resourceIndexKey("ClusterWorkflowTemplate", "", "library") {
		t.Fatal("cluster identity collided with namespaced template")
	}
}

func TestDomainSummaryAdmissionAndCancellation(t *testing.T) {
	text := strings.Replace(summaryLibrary, "name: run", "name: "+strings.Repeat("x", 2048), 1)
	text = strings.Replace(text, "      container:", "      inputs: {parameters: [{name: one}, {name: two}, {name: three}]}\n      container:", 1)
	snapshot, err := source.New("file:///bounded.yaml", 1, text)
	if err != nil {
		t.Fatal(err)
	}
	if _, err := boundedDomainSymbols(WithInvocation(context.Background()), snapshot, "3.6.0", 4096); err == nil {
		t.Fatal("long repeated owner names exceeded the summary budget")
	}
	header, document, _ := summaryInput(t, "file:///cancel.yaml", summaryLibrary, 1)
	ctx, cancel := context.WithCancel(context.Background())
	cancel()
	if _, err := Summarize(WithInvocation(ctx), header, document); !errors.Is(err, context.Canceled) {
		t.Fatalf("cancelled summary committed partial facts: %v", err)
	}
}
