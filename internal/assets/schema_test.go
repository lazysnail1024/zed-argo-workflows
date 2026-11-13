package assets

import (
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"reflect"
	"sort"
	"strings"
	"testing"
	"time"

	"github.com/lazysnail1024/k8s-crd-toolkit/schemaquery"
	"github.com/lazysnail1024/zed-argo-workflows/compat"
	jsonschema "github.com/santhosh-tekuri/jsonschema/v6"
)

func TestColdCompileWaitCancelsAndWarmCacheBypassesGate(t *testing.T) {
	const api = "argoproj.io/v1alpha1"
	warm, err := Compile(context.Background(), "3.6.0", api, "Workflow")
	if err != nil {
		t.Fatal(err)
	}
	coldFile, err := compat.SchemaFile("3.6.0", api, "WorkflowTemplate")
	if err != nil {
		t.Fatal(err)
	}
	compiledSchemas.Delete(coldFile)
	func() {
		// Model an active cold compiler without a timing race or an expensive
		// synthetic schema. Public Compile calls must preserve both behaviors.
		schemaCompilation <- struct{}{}
		defer func() { <-schemaCompilation }()
		type response struct {
			kind   string
			schema *jsonschema.Schema
			err    error
		}
		responses := make(chan response, 2)
		ctx, cancel := context.WithTimeout(context.Background(), 20*time.Millisecond)
		defer cancel()
		for _, kind := range []string{"Workflow", "WorkflowTemplate"} {
			go func(kind string) {
				queryCtx := context.Background()
				if kind == "WorkflowTemplate" {
					queryCtx = ctx
				}
				schema, err := Compile(queryCtx, "3.6.0", api, kind)
				responses <- response{kind, schema, err}
			}(kind)
		}
		for range 2 {
			select {
			case reply := <-responses:
				if reply.kind == "Workflow" && (reply.err != nil || reply.schema != warm) {
					t.Fatalf("warm cache waited for cold compilation: %v", reply.err)
				}
				if reply.kind == "WorkflowTemplate" && !errors.Is(reply.err, context.DeadlineExceeded) {
					t.Fatalf("cold wait did not preserve request cancellation: %v", reply.err)
				}
			case <-time.After(time.Second):
				t.Fatal("compile wait did not terminate on cancellation")
			}
		}
	}()
	ctx, cancel := context.WithTimeout(context.Background(), 5*time.Second)
	defer cancel()
	if _, err := Compile(ctx, "3.6.0", api, "WorkflowTemplate"); err != nil {
		t.Fatalf("canceled waiter poisoned subsequent compilation: %v", err)
	}
}

func compileTestDocument(t *testing.T, document map[string]any) *jsonschema.Schema {
	t.Helper()
	compiler := jsonschema.NewCompiler()
	compiler.DefaultDraft(jsonschema.Draft2020)
	compiler.UseLoader(offlineLoader{})
	const url = "https://local.invalid/argo-schema.json"
	if err := compiler.AddResource(url, document); err != nil {
		t.Fatal(err)
	}
	schema, err := compiler.Compile(url)
	if err != nil {
		t.Fatal(err)
	}
	return schema
}

func TestCompactedReleasePreservesValidationAndFieldMetadata(t *testing.T) {
	const api = "argoproj.io/v1alpha1"
	file, err := compat.SchemaFile("4.0.0", api, "Workflow")
	if err != nil {
		t.Fatal(err)
	}
	document, err := readDocument(file)
	if err != nil {
		t.Fatal(err)
	}
	original := compileTestDocument(t, document)
	compacted, err := Compile(context.Background(), "4.0.0", api, "Workflow")
	if err != nil {
		t.Fatal(err)
	}
	type violation struct{ Path, Kind, Message string }
	violations := func(err error) []violation {
		var result []violation
		var collect func(*jsonschema.ValidationError)
		collect = func(err *jsonschema.ValidationError) {
			if len(err.Causes) == 0 {
				result = append(result, violation{strings.Join(err.InstanceLocation, "/"), fmt.Sprintf("%T", err.ErrorKind), err.Error()})
			}
			for _, child := range err.Causes {
				collect(child)
			}
		}
		if err != nil {
			validation, ok := err.(*jsonschema.ValidationError)
			if !ok {
				t.Fatalf("unexpected validation error: %v", err)
			}
			collect(validation)
		}
		sort.Slice(result, func(i, j int) bool { return fmt.Sprint(result[i]) < fmt.Sprint(result[j]) })
		return result
	}
	for _, test := range []struct {
		name, spec, path, kind string
	}{
		{"valid", `{"templates":[{"name":"main","container":{"image":"alpine"}}]}`, "", ""},
		{"required", `{"templates":[{"name":"main","container":{"ports":[{"protocol":"TCP"}]}}]}`, "spec/templates/0/container/ports/0", "*kind.Required"},
		{"type", `{"parallelism":"many"}`, "spec/parallelism", "*kind.Type"},
		{"enum", `{"artifactGC":{"strategy":"Sometimes"}}`, "spec/artifactGC/strategy", "*kind.Enum"},
		{"minimum", `{"parallelism":0}`, "spec/parallelism", "*kind.Minimum"},
		{"exclusive-template-kinds", `{"templates":[{"name":"main","container":{},"suspend":{}}]}`, "spec/templates/0", "*kind.Not"},
		// Exact releases retain their upstream additionalProperties policy.
		{"open-additional-properties", `{"futureField":true}`, "", ""},
	} {
		t.Run(test.name, func(t *testing.T) {
			var value any
			if err := json.Unmarshal([]byte(`{"apiVersion":"argoproj.io/v1alpha1","kind":"Workflow","metadata":{"name":"case"},"spec":`+test.spec+`}`), &value); err != nil {
				t.Fatal(err)
			}
			want := violations(original.Validate(value))
			got := violations(compacted.Validate(value))
			if !reflect.DeepEqual(got, want) {
				t.Fatalf("validation or instance locations changed:\ncompacted: %#v\noriginal: %#v", got, want)
			}
			if test.kind == "" {
				if len(got) != 0 {
					t.Fatalf("valid input rejected: %#v", got)
				}
			} else if len(got) != 1 || got[0].Path != test.path || got[0].Kind != test.kind {
				t.Fatalf("expected %s at %s, got %#v", test.kind, test.path, got)
			}
		})
	}
	// This path is supported by the 3.6.0 editing contract. The 4.x template
	// exclusivity assertions above intentionally require a different query shape.
	file, err = compat.SchemaFile("3.6.0", api, "Workflow")
	if err != nil {
		t.Fatal(err)
	}
	document, err = readDocument(file)
	if err != nil {
		t.Fatal(err)
	}
	original = compileTestDocument(t, document)
	compacted, err = Compile(context.Background(), "3.6.0", api, "Workflow")
	if err != nil {
		t.Fatal(err)
	}
	var value any
	if err := json.Unmarshal([]byte(`{"spec":{"templates":[{"name":"main","container":{"ports":[{"containerPort":80,"protocol":"TCP"}]}}]}}`), &value); err != nil {
		t.Fatal(err)
	}
	path := []string{"spec", "templates", "0", "container", "ports", "0", "protocol"}
	for _, schema := range []*jsonschema.Schema{original, compacted} {
		result, err := schemaquery.Lookup(context.Background(), schema, path, value, schemaquery.Limits{})
		if err != nil || !result.Complete {
			t.Fatalf("field query failed: %v", err)
		}
		found := false
		for _, node := range result.Nodes {
			if node.Default != nil && *node.Default == "TCP" {
				found = true
			}
		}
		if !found {
			t.Fatal("compiled field lost its default")
		}
	}
}

func TestSchemaCompactionPreservesLiteralObjects(t *testing.T) {
	literal := map[string]any{"properties": map[string]any{"payload": map[string]any{"type": "string", "description": strings.Repeat("literal data ", 20)}}, "items": map[string]any{"type": "string"}}
	node := func() map[string]any {
		return map[string]any{"type": "object", "description": "schema annotations survive sharing", "const": literal, "enum": []any{literal}, "default": literal, "examples": []any{literal}, "x-example": literal}
	}
	document := map[string]any{"$schema": "https://json-schema.org/draft/2020-12/schema", "type": "object", "properties": map[string]any{"a": node(), "b": node()}}
	compacted, err := compactSchema(context.Background(), document)
	if err != nil {
		t.Fatal(err)
	}
	properties := compacted["properties"].(map[string]any)
	a := properties["a"].(map[string]any)["$ref"]
	b := properties["b"].(map[string]any)["$ref"]
	if a == nil || a != b {
		t.Fatalf("identical schema subtrees were not shared: %v, %v", a, b)
	}
	shared := compacted["$defs"].(map[string]any)[strings.TrimPrefix(a.(string), "#/$defs/")].(map[string]any)
	if !reflect.DeepEqual(shared, node()) {
		t.Fatalf("literal data or annotations changed: %#v", shared)
	}
	schema := compileTestDocument(t, compacted)
	if err := schema.Validate(map[string]any{"a": literal, "b": literal}); err != nil {
		t.Fatalf("literal object no longer accepted: %v", err)
	}
	if err := schema.Validate(map[string]any{"a": map[string]any{"wrong": true}}); err == nil {
		t.Fatal("literal const/enum constraints were lost")
	}
}

func TestSchemaCompactionLeavesScopedDocumentsIntact(t *testing.T) {
	for _, keyword := range []string{"$id", "$ref", "$defs", "$anchor", "$dynamicAnchor", "$dynamicRef", "$recursiveAnchor", "$recursiveRef", "$schema", "$vocabulary"} {
		t.Run(keyword, func(t *testing.T) {
			document := map[string]any{"$schema": "https://json-schema.org/draft/2020-12/schema", "properties": map[string]any{"nested": map[string]any{keyword: "scope-sensitive", "description": strings.Repeat("long description ", 20)}}}
			before, err := json.Marshal(document)
			if err != nil {
				t.Fatal(err)
			}
			result, err := compactSchema(context.Background(), document)
			if err != nil {
				t.Fatal(err)
			}
			after, err := json.Marshal(result)
			if err != nil || string(after) != string(before) {
				t.Fatalf("scope-sensitive schema changed: %s (%v)", after, err)
			}
		})
	}
	ctx, cancel := context.WithCancel(context.Background())
	cancel()
	if _, err := compactSchema(ctx, map[string]any{"$schema": "https://json-schema.org/draft/2020-12/schema"}); !errors.Is(err, context.Canceled) {
		t.Fatalf("schema factoring ignored cancellation: %v", err)
	}
}
