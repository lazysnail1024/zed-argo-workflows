package assets

import (
	"context"
	"errors"
	"testing"
	"time"

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
