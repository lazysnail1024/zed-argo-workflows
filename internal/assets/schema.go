// Package assets loads the built-in normalized CRD schemas without network access.
package assets

import (
	"bytes"
	"context"
	"embed"
	"encoding/json"
	"fmt"
	"github.com/lazysnail1024/zed-argo-workflows/compat"
	"github.com/lazysnail1024/zed-argo-workflows/internal/worktrace"
	jsonschema "github.com/santhosh-tekuri/jsonschema/v6"
	"net/url"
	"strconv"
	"strings"
	"sync"
)

//go:embed schemas/*.json
var schemas embed.FS

type offlineLoader struct{}

func (offlineLoader) Load(url string) (any, error) {
	return nil, fmt.Errorf("external schema reference is forbidden: %s", url)
}

func readDocument(file string) (map[string]any, error) {
	return readDocumentContext(context.Background(), file)
}

func readDocumentContext(ctx context.Context, file string) (map[string]any, error) {
	raw, err := schemas.ReadFile("schemas/" + file)
	if err != nil {
		return nil, err
	}
	var document map[string]any
	decoder := json.NewDecoder(bytes.NewReader(raw))
	decoder.UseNumber()
	worktrace.Add(ctx, worktrace.SchemaLoads, 1)
	if err := decoder.Decode(&document); err != nil {
		return nil, err
	}
	return document, nil
}

type compiledEntry struct {
	schema   *jsonschema.Schema
	preserve map[string]bool
}

// collectPreservedUnknownFields records the exact jsonschema.Schema.Location
// strings for nodes carrying x-kubernetes-preserve-unknown-fields: true so
// unknown-field checks reuse the compiled schema without decoding JSON twice.
func collectPreservedUnknownFields(baseURL string, document map[string]any) map[string]bool {
	var preserved map[string]bool
	var walk func(any, string)
	walk = func(value any, ptr string) {
		switch node := value.(type) {
		case map[string]any:
			if ok, _ := node["x-kubernetes-preserve-unknown-fields"].(bool); ok {
				if preserved == nil {
					preserved = map[string]bool{}
				}
				preserved[baseURL+"#"+ptr] = true
			}
			for key, child := range node {
				switch child.(type) {
				case map[string]any, []any:
					tok := strings.ReplaceAll(strings.ReplaceAll(key, "~", "~0"), "/", "~1")
					walk(child, ptr+"/"+url.PathEscape(tok))
				}
			}
		case []any:
			for i, child := range node {
				switch child.(type) {
				case map[string]any, []any:
					walk(child, ptr+"/"+strconv.Itoa(i))
				}
			}
		}
	}
	walk(document, "")
	return preserved
}

// Compiled schemas contain only immutable embedded catalog data. The key set is
// bounded by the embedded schema files, not caller input. Warm queries bypass
// the gate. Cold compilation is serialized to bound construction memory;
// waiting requests can cancel independently of the active compiler.
var compiledSchemas sync.Map // embedded filename -> *compiledEntry
var schemaCompilation = make(chan struct{}, 1)

func Compile(ctx context.Context, version, apiVersion, kind string) (*jsonschema.Schema, error) {
	schema, _, err := CompileWithFieldPolicy(ctx, version, apiVersion, kind)
	return schema, err
}

func CompileWithFieldPolicy(ctx context.Context, version, apiVersion, kind string) (*jsonschema.Schema, map[string]bool, error) {
	worktrace.Add(ctx, worktrace.SchemaCompileCalls, 1)
	file, err := compat.SchemaFile(version, apiVersion, kind)
	if err != nil {
		return nil, nil, err
	}
	if err = ctx.Err(); err != nil {
		return nil, nil, err
	}
	if cached, ok := compiledSchemas.Load(file); ok {
		worktrace.Add(ctx, worktrace.SchemaCompileHits, 1)
		entry := cached.(*compiledEntry)
		return entry.schema, entry.preserve, nil
	}
	waiting := worktrace.Now(ctx)
	select {
	case schemaCompilation <- struct{}{}:
		defer func() { <-schemaCompilation }()
	case <-ctx.Done():
		worktrace.Elapsed(ctx, worktrace.SchemaCompileWaitNanoseconds, waiting)
		return nil, nil, ctx.Err()
	}
	worktrace.Elapsed(ctx, worktrace.SchemaCompileWaitNanoseconds, waiting)
	if err := ctx.Err(); err != nil {
		return nil, nil, err
	}
	if cached, ok := compiledSchemas.Load(file); ok {
		worktrace.Add(ctx, worktrace.SchemaCompileHits, 1)
		entry := cached.(*compiledEntry)
		return entry.schema, entry.preserve, nil
	}
	// Compilation only needs this decoded map once; keeping it in the
	// invocation cache duplicates the larger compiled schema's live memory.
	document, err := readDocumentContext(ctx, file)
	if err != nil {
		return nil, nil, err
	}
	// Exact CRDs are stored in expanded form for provenance and field lookup.
	// Automatic compatibility already shares its release branches at build time.
	if version != compat.DefaultVersion {
		document, err = compactSchema(ctx, document)
		if err != nil {
			return nil, nil, err
		}
	}

	compiler := jsonschema.NewCompiler()
	compiler.DefaultDraft(jsonschema.Draft2020)
	compiler.UseLoader(offlineLoader{})
	const schemaURL = "https://local.invalid/argo-schema.json"
	var preserve map[string]bool
	if version != compat.DefaultVersion {
		preserve = collectPreservedUnknownFields(schemaURL, document)
	}
	if err := compiler.AddResource(schemaURL, document); err != nil {
		return nil, nil, err
	}
	worktrace.Add(ctx, worktrace.SchemaCompiles, 1)
	schema, err := compiler.Compile(schemaURL)
	if err != nil {
		return nil, nil, err
	}
	// Successful compilation depends only on immutable embedded schema bytes.
	// Preserve it even if the requesting document changed during compilation;
	// that caller still receives cancellation below, and failures are never cached.
	actual, _ := compiledSchemas.LoadOrStore(file, &compiledEntry{schema: schema, preserve: preserve})
	if err := ctx.Err(); err != nil {
		return nil, nil, err
	}
	entry := actual.(*compiledEntry)
	return entry.schema, entry.preserve, nil
}
