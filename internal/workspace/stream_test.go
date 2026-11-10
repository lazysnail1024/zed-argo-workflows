package workspace

import (
	"context"
	"errors"
	"os"
	"path/filepath"
	"reflect"
	"strings"
	"testing"

	"github.com/lazysnail1024/k8s-crd-toolkit/source"
	"github.com/lazysnail1024/zed-argo-workflows/internal/fileuri"
)

func TestDescribeDoesNotAcquireResourceDirectory(t *testing.T) {
	root := filepath.Join(t.TempDir(), "not-created")
	configuration := "schemaVersion: 1\ntargetVersion: '3.6.0'\nreferencePolicy: closed\n"
	w, err := Describe(context.Background(), root, "", &configuration)
	if err != nil {
		t.Fatal(err)
	}
	if len(w.Documents) != 0 || len(w.Targets) != 1 || w.Targets[0].AnalysisComplete || w.Root != root {
		t.Fatalf("metadata must not claim acquired documents or completion: %+v", w)
	}
	if _, err := os.Stat(root); !os.IsNotExist(err) {
		t.Fatalf("resource directory unexpectedly created: %v", err)
	}
	invalidUnusedScope := configuration + "scopes:\n- name: unused\n  include: ['unused/**']\n  targetVersion: '9.9.9'\n"
	if _, err := Describe(context.Background(), root, "", &invalidUnusedScope); err == nil {
		t.Fatal("unused invalid target was hidden by empty discovery")
	}
	ctx, cancel := context.WithCancel(context.Background())
	cancel()
	if _, err := Describe(ctx, root, "", &configuration); !errors.Is(err, context.Canceled) {
		t.Fatalf("canceled metadata request: %v", err)
	}
}

func TestStreamTargetsMatchCLIAndConfigurationChanges(t *testing.T) {
	root := t.TempDir()
	configuration := "schemaVersion: 1\ntargetVersion: '3.6.0'\nreferencePolicy: closed\ndefaultNamespace: team-a\nexclude: ['excluded/**']\nscopes:\n- name: examples\n  include: ['workflow.yaml']\n"
	if err := os.WriteFile(filepath.Join(root, ".argo-workflows-ls.yaml"), []byte(configuration), 0o600); err != nil {
		t.Fatal(err)
	}
	filename := filepath.Join(root, "workflow.yaml")
	text := "apiVersion: argoproj.io/v1alpha1\nkind: Workflow\nmetadata: {name: demo}\nspec: {}\n"
	if err := os.WriteFile(filename, []byte(text), 0o600); err != nil {
		t.Fatal(err)
	}
	uri, err := fileuri.FromPath(filename)
	if err != nil {
		t.Fatal(err)
	}
	snapshot, err := source.New(source.URI(uri), 1, text)
	if err != nil {
		t.Fatal(err)
	}
	header, err := Describe(context.Background(), root, "", nil)
	if err != nil {
		t.Fatal(err)
	}
	document, err := OpenDocument(header, snapshot, "")
	if err != nil {
		t.Fatal(err)
	}
	cli, err := LoadContext(context.Background(), root, "", "")
	if err != nil || len(cli.Documents) != 1 {
		t.Fatalf("CLI fixture: documents=%d error=%v", len(cli.Documents), err)
	}
	if !reflect.DeepEqual(document.Target, cli.Documents[0].Target) {
		t.Fatalf("stream/CLI target mismatch: %+v / %+v", document.Target, cli.Documents[0].Target)
	}
	changed := strings.Replace(configuration, "team-a", "team-b", 1)
	newHeader, err := Describe(context.Background(), root, "", &changed)
	if err != nil {
		t.Fatal(err)
	}
	newDocument, err := OpenDocument(newHeader, snapshot, "")
	if err != nil {
		t.Fatal(err)
	}
	if newDocument.Target.ID != document.Target.ID || newDocument.Target.ConfigHash == document.Target.ConfigHash {
		t.Fatal("namespace-only configuration change must preserve scope and change its identity")
	}
	unassignedURI, err := fileuri.FromPath(filepath.Join(root, "unassigned.yaml"))
	if err != nil {
		t.Fatal(err)
	}
	unassignedSnapshot, err := source.New(source.URI(unassignedURI), 1, text)
	if err != nil {
		t.Fatal(err)
	}
	unassigned, err := OpenDocument(header, unassignedSnapshot, "")
	if err != nil {
		t.Fatal(err)
	}
	unassignedChanged, err := OpenDocument(newHeader, unassignedSnapshot, "")
	if err != nil {
		t.Fatal(err)
	}
	if unassigned.Target.Scope != "unassigned" || unassigned.Target.ConfigHash == unassignedChanged.Target.ConfigHash {
		t.Fatal("unassigned document lost the configuration identity")
	}
	for _, name := range []string{"workflow.yaml", "excluded/workflow.yaml"} {
		address, err := fileuri.FromPath(filepath.Join(root, filepath.FromSlash(name)))
		if err != nil {
			t.Fatal(err)
		}
		input, err := source.New(source.URI(address), 1, text)
		if err != nil {
			t.Fatal(err)
		}
		before, err := OpenBuffer(context.Background(), root, "", &configuration, input)
		if err != nil {
			t.Fatal(err)
		}
		after, err := OpenBuffer(context.Background(), root, "", &changed, input)
		if err != nil {
			t.Fatal(err)
		}
		if before.Target.ConfigHash == after.Target.ConfigHash || before.Target.ID != after.Target.ID {
			t.Errorf("%s: foreground buffer omitted configuration identity", name)
		}
		if strings.HasPrefix(name, "excluded/") {
			before, err = OpenDocument(header, input, "")
			if err != nil {
				t.Fatal(err)
			}
			after, err = OpenDocument(newHeader, input, "")
			if err != nil {
				t.Fatal(err)
			}
			if !strings.HasPrefix(before.Target.Scope, "opened:") || before.Target.ConfigHash == after.Target.ConfigHash {
				t.Error("isolated editor scope omitted configuration identity")
			}
		}
	}
}

func TestCandidatePreservesDiskMembership(t *testing.T) {
	root := t.TempDir()
	configuration := "schemaVersion: 1\nexclude: ['out/**']\n"
	w, err := Describe(context.Background(), root, "", &configuration)
	if err != nil {
		t.Fatal(err)
	}
	nested := filepath.Join(root, "child")
	for filename, want := range map[string]bool{
		"workflow.yaml": true, "workflow.yml": true, "WORKFLOW.YAML": false,
		"ordinary.txt": false, ".argo-workflows-ls.yaml": false,
		"vendor/workflow.yaml": false, "nested/.git/workflow.yaml": false,
		"node_modules/workflow.yaml": false, ".argo-ls/workflow.yaml": false,
		"out/workflow.yaml": false, "child/workflow.yaml": false,
		"child-other/workflow.yaml": true, "templates/plain.yaml": true,
	} {
		got, err := Candidate(w, filepath.Join(root, filepath.FromSlash(filename)), []string{root, nested})
		if err != nil || got != want {
			t.Errorf("%s: candidate=%v want=%v error=%v", filename, got, want, err)
		}
	}
	if ok, err := Candidate(w, filepath.Join(filepath.Dir(root), "outside.yaml"), nil); err != nil || ok {
		t.Fatalf("outside root admitted: %v %v", ok, err)
	}
	chart := filepath.Join(root, "chart")
	if err := os.MkdirAll(filepath.Join(chart, "templates"), 0o700); err != nil {
		t.Fatal(err)
	}
	if err := os.WriteFile(filepath.Join(chart, "Chart.yaml"), []byte("apiVersion: v2\nname: demo\nversion: 0.1.0\n"), 0o600); err != nil {
		t.Fatal(err)
	}
	if ok, err := Candidate(w, filepath.Join(chart, "templates", "workflow.yaml"), nil); err != nil || ok {
		t.Fatalf("Helm source admitted: %v %v", ok, err)
	}
	t.Run("symlinked-directory-watch", func(t *testing.T) {
		link := filepath.Join(root, "linked")
		if err := os.Symlink(t.TempDir(), link); err != nil {
			t.Skipf("host cannot create symlinks: %v", err)
		}
		if ok, err := Candidate(w, filepath.Join(link, "workflow.yaml"), nil); err != nil || ok {
			t.Fatalf("watch bypassed child symlink policy: %v %v", ok, err)
		}
	})
}

func TestReadTextBoundsAndFreshBytes(t *testing.T) {
	root := t.TempDir()
	filename := filepath.Join(root, "workflow.yaml")
	if err := os.WriteFile(filename, []byte("before"), 0o600); err != nil {
		t.Fatal(err)
	}
	info, err := os.Stat(filename)
	if err != nil {
		t.Fatal(err)
	}
	if got, err := ReadText(context.Background(), filename); err != nil || got != "before" {
		t.Fatalf("first read: %q %v", got, err)
	}
	if err := os.WriteFile(filename, []byte("after!"), 0o600); err != nil {
		t.Fatal(err)
	}
	if err := os.Chtimes(filename, info.ModTime(), info.ModTime()); err != nil {
		t.Fatal(err)
	}
	if got, err := ReadText(context.Background(), filename); err != nil || got != "after!" {
		t.Fatalf("same-size/same-mtime change was not read: %q %v", got, err)
	}
	ctx, cancel := context.WithCancel(context.Background())
	cancel()
	if _, err := ReadText(ctx, filename); !errors.Is(err, context.Canceled) {
		t.Fatalf("cancellation: %v", err)
	}
	if _, err := ReadText(context.Background(), filepath.Join(root, "deleted.yaml")); !os.IsNotExist(err) {
		t.Fatalf("deletion error lost: %v", err)
	}
	if _, err := ReadText(context.Background(), root); err == nil {
		t.Fatal("directory accepted as text")
	}
	if err := os.WriteFile(filename, []byte(strings.Repeat("x", 5*1024*1024)), 0o600); err != nil {
		t.Fatal(err)
	}
	if got, err := ReadText(context.Background(), filename); err != nil || len(got) != 5*1024*1024 {
		t.Fatalf("exact byte limit rejected: length=%d error=%v", len(got), err)
	}
	if err := os.WriteFile(filename, []byte(strings.Repeat("x", 5*1024*1024+1)), 0o600); err != nil {
		t.Fatal(err)
	}
	if _, err := ReadText(context.Background(), filename); err == nil || !strings.Contains(err.Error(), "5 MiB") {
		t.Fatalf("oversized input accepted: %v", err)
	}
	t.Run("symlink-file", func(t *testing.T) {
		link := filepath.Join(root, "symlink.yaml")
		if err := os.Symlink(filename, link); err != nil {
			t.Skipf("host cannot create symlinks: %v", err)
		}
		if _, err := ReadText(context.Background(), link); err == nil || !strings.Contains(err.Error(), "regular") {
			t.Fatalf("symlink accepted: %v", err)
		}
	})
}

func TestCLISelectionDefersUnassignedTarget(t *testing.T) {
	root := t.TempDir()
	configuration := "schemaVersion: 1\ntargetVersion: '9.9.9'\nscopes:\n- name: selected\n  include: ['selected.yaml']\n  targetVersion: '3.6.0'\n"
	for name, text := range map[string]string{".argo-workflows-ls.yaml": configuration, "selected.yaml": "selected: true\n", "unassigned.yaml": "outside: true\n"} {
		if err := os.WriteFile(filepath.Join(root, name), []byte(text), 0600); err != nil {
			t.Fatal(err)
		}
	}
	selected, err := LoadContext(context.Background(), root, "selected", "")
	if err != nil || len(selected.Documents) != 1 || len(selected.Targets) != 1 || selected.Targets[0].Version != "3.6.0" {
		t.Fatalf("unselected file activated an invalid default: targets=%+v, error=%v", selected.Targets, err)
	}
	if _, err := LoadContext(context.Background(), root, "", ""); err == nil {
		t.Fatal("an acquired unassigned document bypassed target validation")
	}
}
