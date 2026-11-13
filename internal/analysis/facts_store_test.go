package analysis

import (
	"context"
	"fmt"
	"strings"
	"testing"
	"unsafe"

	"github.com/lazysnail1024/k8s-crd-toolkit/source"
	"github.com/lazysnail1024/zed-argo-workflows/internal/workspace"
)

const storedWorkflow = "apiVersion: argoproj.io/v1alpha1\nkind: Workflow\nmetadata: {name: sample, namespace: argo}\nspec:\n  entrypoint: main\n  templates:\n    - name: main\n      container: {image: alpine}\n"

func storedInput(t *testing.T, uri, text, version string, revision source.Revision) workspace.Document {
	t.Helper()
	snapshot, err := source.New(source.URI(uri), revision, text)
	if err != nil {
		t.Fatal(err)
	}
	return workspace.Document{Snapshot: snapshot, Target: workspace.Target{ID: "scope", Scope: "root", Version: version}}
}

func captureStoredInput(t *testing.T, document workspace.Document) *Facts {
	t.Helper()
	ctx := WithInvocation(context.Background())
	view, err := parseView(ctx, document.Snapshot)
	if err != nil {
		t.Fatal(err)
	}
	for _, doc := range view.Documents() {
		if _, err := project(ctx, document.Snapshot, doc, document.Target.Version); err != nil {
			t.Fatal(err)
		}
	}
	facts := CaptureDocumentFacts(ctx, document)
	if facts == nil {
		t.Fatal("completed document did not produce bounded facts")
	}
	return facts
}

func assertStoredDefinition(t *testing.T, ctx context.Context, document workspace.Document) {
	t.Helper()
	text := document.Snapshot.Text()
	use := strings.Index(text, "entrypoint: main") + len("entrypoint: ")
	declaration := strings.Index(text, "name: main") + len("name: ")
	position, err := document.Snapshot.Position(use)
	if err != nil {
		t.Fatal(err)
	}
	got := Definitions(ctx, document.Snapshot, document.Target.Version, position)
	want := spanLocation(document.Snapshot, source.Span{Start: declaration, End: declaration + len("main")})
	if len(got) != 1 || got[0] != want {
		t.Fatalf("definition did not use current URI and spans: got=%+v want=%+v", got, want)
	}
}

func TestFactStoreRestoresOnlyFreshIdentityAndRelease(t *testing.T) {
	document := storedInput(t, "file:///stored.yaml", storedWorkflow, "3.6.0", 1)
	store := &FactStore{}
	store.Store(captureStoredInput(t, document))
	cases := []struct {
		name               string
		document           workspace.Document
		views, projections int
	}{
		{"same", document, 1, 1},
		{"uri", storedInput(t, "file:///other.yaml", storedWorkflow, "3.6.0", 1), 0, 0},
		{"revision", storedInput(t, "file:///stored.yaml", storedWorkflow, "3.6.0", 2), 0, 0},
		{"text", storedInput(t, "file:///stored.yaml", "# moved\n"+storedWorkflow, "3.6.0", 1), 0, 0},
		{"release", storedInput(t, "file:///stored.yaml", storedWorkflow, "4.0.0", 1), 1, 0},
	}
	for _, test := range cases {
		t.Run(test.name, func(t *testing.T) {
			ctx := WithInvocation(context.Background())
			store.Restore(ctx, workspace.Workspace{Documents: []workspace.Document{test.document}})
			state := ctx.Value(invocationKey{}).(*invocation)
			if len(state.views) != test.views || len(state.projections) != test.projections {
				t.Fatalf("unexpected restored identity/release: views=%d projections=%d", len(state.views), len(state.projections))
			}
			assertStoredDefinition(t, ctx, test.document)
		})
	}
	ctx, cancel := context.WithCancel(context.Background())
	ctx = WithInvocation(ctx)
	cancel()
	store.Restore(ctx, workspace.Workspace{Documents: []workspace.Document{document}})
	if state := ctx.Value(invocationKey{}).(*invocation); len(state.views) != 0 || len(state.projections) != 0 {
		t.Fatal("canceled request imported facts")
	}
}

func TestFactStorePreservesCurrentConfiguration(t *testing.T) {
	text := "apiVersion: argoproj.io/v1alpha1\nkind: Workflow\nmetadata: {name: caller, namespace: argo}\nspec:\n  workflowTemplateRef: {name: missing}\n"
	document := storedInput(t, "file:///policy.yaml", text, "3.6.0", 1)
	store := &FactStore{}
	store.Store(captureStoredInput(t, document))
	for _, policy := range []struct{ name, severity string }{{"closed", "Error"}, {"open", "Information"}} {
		t.Run(policy.name, func(t *testing.T) {
			ctx := WithInvocation(context.Background())
			w := workspace.Workspace{Documents: []workspace.Document{document}, Config: workspace.Config{ReferencePolicy: policy.name}}
			store.Restore(ctx, w)
			diagnostics, _ := ExternalDiagnostics(ctx, w)
			if len(diagnostics) != 1 || diagnostics[0].Code != "WF_TEMPLATE_REF_UNRESOLVED" || diagnostics[0].Severity != policy.severity {
				t.Fatalf("retained parse facts replaced current reference policy: %+v", diagnostics)
			}
		})
	}
}

func TestFactStoreCanonicalizesRereadTextAfterReleaseChange(t *testing.T) {
	old := storedInput(t, "file:///release.yaml", storedWorkflow, "3.6.0", 1)
	store := &FactStore{}
	store.Store(captureStoredInput(t, old))
	// The filesystem returns equal bytes in a different allocation. The CST
	// can be reused, but the changed release requires a fresh projection.
	fresh := storedInput(t, "file:///release.yaml", strings.Clone(storedWorkflow), "4.0.0", 1)
	if unsafe.StringData(old.Snapshot.Text()) == unsafe.StringData(fresh.Snapshot.Text()) {
		t.Fatal("fixture did not allocate independent reread bytes")
	}
	ctx := WithInvocation(context.Background())
	store.Restore(ctx, workspace.Workspace{Documents: []workspace.Document{fresh}})
	assertStoredDefinition(t, ctx, fresh)
	store.Store(CaptureDocumentFacts(ctx, fresh))
	entry := store.entries[fresh.Snapshot.URI()].Value.(*storedDocumentFacts)
	if len(entry.projections) != 1 {
		t.Fatal("fresh release projection was not retained")
	}
	for key := range entry.projections {
		if key.version != fresh.Target.Version || unsafe.StringData(key.text) != unsafe.StringData(entry.identity.text) {
			t.Fatal("projection retained a second uncharged copy of reread source bytes")
		}
	}
}

func TestFactStoreAggregateRetentionAndEvictionFallback(t *testing.T) {
	for _, dimension := range []string{"text", "nodes"} {
		t.Run(dimension, func(t *testing.T) {
			store := &FactStore{}
			text := storedWorkflow
			if dimension == "text" {
				text = "# " + strings.Repeat("x", 2*1024*1024) + "\n" + text
			} else {
				text += "padding: [" + strings.Repeat("value,", 4000) + "value]\n"
			}
			first := storedInput(t, "file:///cache-000.yaml", text, "3.6.0", 1)
			firstFacts := captureStoredInput(t, first)
			cost := firstFacts.costs[snapshotKey(first.Snapshot)]
			count := maxFactText/cost.text + 2
			if dimension == "nodes" {
				count = maxFactNodes/cost.nodes + 2
			}
			for i := range count {
				document := storedInput(t, fmt.Sprintf("file:///cache-%03d.yaml", i), text, "3.6.0", 1)
				store.Store(captureStoredInput(t, document))
				if store.cost.text > maxFactText || store.cost.nodes > maxFactNodes {
					t.Fatalf("aggregate %s budget exceeded: %+v", dimension, store.cost)
				}
			}
			if store.entries[first.Snapshot.URI()] != nil || len(store.entries) >= count {
				t.Fatal("oldest document was not evicted at the aggregate limit")
			}
			ctx := WithInvocation(context.Background())
			store.Restore(ctx, workspace.Workspace{Documents: []workspace.Document{first}})
			if len(ctx.Value(invocationKey{}).(*invocation).views) != 0 {
				t.Fatal("evicted document remained reachable through the store")
			}
			assertStoredDefinition(t, ctx, first)
			store.Store(firstFacts)
			before := store.cost
			store.Store(firstFacts)
			if store.cost != before {
				t.Fatal("replacing the same URI leaked aggregate retention")
			}
			store.Delete(first.Snapshot.URI())
			if store.cost.text != before.text-cost.text || store.cost.nodes != before.nodes-cost.nodes {
				t.Fatal("deleted document's retention was not released")
			}
			store.Clear()
			if len(store.entries) != 0 || store.order.Len() != 0 || store.cost != (factBudget{}) {
				t.Fatal("clearing the store retained document entries")
			}
		})
	}
}
