// Package assets loads the built-in normalized CRD schemas without network access.
package assets

import (
	"bytes"
	"context"
	"embed"
	"encoding/json"
	"fmt"
	"github.com/lazysnail1024/zed-argo-workflows/compat"
	jsonschema "github.com/santhosh-tekuri/jsonschema/v6"
	"sync"
)

//go:embed schemas/*.json
var schemas embed.FS

type offlineLoader struct{}

func (offlineLoader) Load(url string) (any, error) {
	return nil, fmt.Errorf("external schema reference is forbidden: %s", url)
}

func Document(ctx context.Context, version, apiVersion, kind string) (map[string]any, error) {
	file, err := compat.SchemaFile(version, apiVersion, kind)
	if err != nil {
		return nil, err
	}
	if err := ctx.Err(); err != nil {
		return nil, err
	}
	cache, _ := ctx.Value(documentKey{}).(*requestSchemas)
	if cache != nil && cache.documents[file] != nil {
		return cache.documents[file], nil
	}
	raw, err := schemas.ReadFile("schemas/" + file)
	if err != nil {
		return nil, err
	}
	var document map[string]any
	decoder := json.NewDecoder(bytes.NewReader(raw))
	decoder.UseNumber()
	if err := decoder.Decode(&document); err != nil {
		return nil, err
	}
	if cache != nil && ctx.Err() == nil {
		cache.documents[file] = document
	}
	return document, nil
}

// WithDocumentReuse bounds decoded document reuse to one sequential analysis.
// No workspace facts or failures survive the request context.
type documentKey struct{}

type requestSchemas struct {
	documents map[string]map[string]any
}

func WithDocumentReuse(ctx context.Context) context.Context {
	if _, ok := ctx.Value(documentKey{}).(*requestSchemas); ok {
		return ctx
	}
	return context.WithValue(ctx, documentKey{}, &requestSchemas{documents: map[string]map[string]any{}})
}

// Compiled schemas contain only immutable embedded catalog data. The key set is
// bounded by the embedded schema files (35), not caller input. Compilation runs
// outside map locks; cancellation never waits for another request to compile.
var compiledSchemas sync.Map // embedded filename -> *jsonschema.Schema

func Compile(ctx context.Context, version, apiVersion, kind string) (*jsonschema.Schema, error) {
	file, err := compat.SchemaFile(version, apiVersion, kind)
	if err != nil {
		return nil, err
	}
	if err = ctx.Err(); err != nil {
		return nil, err
	}
	if cached, ok := compiledSchemas.Load(file); ok {
		return cached.(*jsonschema.Schema), nil
	}
	document, err := Document(ctx, version, apiVersion, kind)
	if err != nil {
		return nil, err
	}

	compiler := jsonschema.NewCompiler()
	compiler.DefaultDraft(jsonschema.Draft2020)
	compiler.UseLoader(offlineLoader{})
	const url = "https://local.invalid/argo-schema.json"
	if err := compiler.AddResource(url, document); err != nil {
		return nil, err
	}
	schema, err := compiler.Compile(url)
	if err != nil {
		return nil, err
	}
	if err := ctx.Err(); err != nil {
		return nil, err
	}
	actual, _ := compiledSchemas.LoadOrStore(file, schema)
	return actual.(*jsonschema.Schema), nil
}
