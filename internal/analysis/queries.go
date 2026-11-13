package analysis

import (
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"sort"
	"strconv"
	"strings"

	"github.com/lazysnail1024/k8s-crd-toolkit/schemaquery"
	"github.com/lazysnail1024/k8s-crd-toolkit/source"
	"github.com/lazysnail1024/zed-argo-workflows/compat"
	jsonschema "github.com/santhosh-tekuri/jsonschema/v6"
)

func queryRange(snapshot source.Snapshot, span source.Span) map[string]any {
	start, _ := snapshot.Position(span.Start)
	end, _ := snapshot.Position(span.End)
	return map[string]any{"start": map[string]any{"line": start.Line, "character": start.UTF16Column}, "end": map[string]any{"line": end.Line, "character": end.UTF16Column}}
}
func CompleteFields(ctx context.Context, snapshot source.Snapshot, version string, pos source.Position) ([]map[string]any, error) {
	result := []map[string]any{}
	field, err := queryField(ctx, snapshot, version, pos)
	if err != nil || field.schema == nil || field.shared {
		return result, err
	}
	query, err := schemaquery.Lookup(ctx, field.schema, field.path, field.value, schemaquery.Limits{})
	if err != nil {
		return nil, err
	}
	if field.key {
		properties := map[string]bool{}
		for _, s := range query.Nodes {
			for name := range s.Properties {
				if !field.existing[name] && strings.HasPrefix(name, field.prefix) {
					properties[name] = true
				}
			}
		}
		names := []string{}
		for name := range properties {
			names = append(names, name)
		}
		sort.Strings(names)
		for _, name := range names {
			path := append(append([]string(nil), field.path...), name)
			allowed, err := schemaquery.AllowsKey(ctx, field.schema, path, field.value, schemaquery.Limits{})
			if err != nil {
				return nil, err
			}
			if !allowed {
				continue
			}
			kind, err := schemaquery.Type(ctx, field.schema, path, field.value, schemaquery.Limits{})
			if err != nil {
				return nil, err
			}
			detail := kind + " · Argo Workflows " + version
			if query.Ambiguous {
				detail += " · candidate (branch unresolved)"
			}
			replacement := name + field.after
			raw := snapshot.Text()[field.span.Start:field.span.End]
			if len(raw) >= 2 && (raw[0] == '\'' || raw[0] == '"') {
				replacement = string(raw[0]) + name + string(raw[0]) + field.after
			}
			result = append(result, map[string]any{"label": name, "kind": 10, "detail": detail, "insertTextFormat": 1, "textEdit": map[string]any{"range": queryRange(snapshot, field.span), "newText": replacement}})
		}
	} else {
		values, err := schemaquery.Values(ctx, field.schema, field.path, field.value, schemaquery.Limits{})
		if err != nil {
			return nil, err
		}
		for _, value := range values {
			raw, err := json.Marshal(value)
			if err != nil {
				return nil, err
			}
			name := string(raw)
			result = append(result, map[string]any{"label": name, "kind": 12, "detail": "Argo Workflows " + version, "insertTextFormat": 1, "textEdit": map[string]any{"range": queryRange(snapshot, field.span), "newText": field.before + name + field.after}})
		}
	}
	if err := ctx.Err(); err != nil {
		return nil, err
	}
	return result, nil
}
func HoverField(ctx context.Context, snapshot source.Snapshot, version string, pos source.Position) (any, error) {
	offset, err := snapshot.Offset(pos)
	if err != nil {
		return nil, err
	}
	if offset < len(snapshot.Text()) && snapshot.Text()[offset] == '#' {
		return nil, nil
	}
	field, err := queryField(ctx, snapshot, version, pos)
	if err != nil || field.schema == nil {
		return nil, err
	}
	path := field.path
	if field.key {
		if field.name == "" {
			return nil, nil
		}
		path = append(append([]string(nil), path...), field.name)
	}
	query, err := schemaquery.Lookup(ctx, field.schema, path, field.value, schemaquery.Limits{})
	if errors.Is(err, schemaquery.ErrUnsupported) {
		// A template name has an unconditional positive property declaration even
		// when its enclosing template has negative implementation-choice rules.
		// Read only that declaration for documentation; validation stays unchanged.
		if node := templateNameDocumentation(field.schema, path); node != nil {
			query, err = schemaquery.Lookup(ctx, node, nil, nil, schemaquery.Limits{})
			field.schema, path, field.value = node, nil, nil
		}
	}
	if err != nil {
		return nil, err
	}
	if len(query.Nodes) == 0 {
		return nil, nil
	}
	kind, err := schemaquery.Type(ctx, field.schema, path, field.value, schemaquery.Limits{})
	if err != nil {
		return nil, err
	}
	text := fmt.Sprintf("**%s**\n\nType: `%s`\n\nArgo Workflows target: `%s`", field.name, kind, version)
	seen := map[string]bool{}
	for _, s := range query.Nodes {
		if s.Description != "" && !seen[s.Description] {
			text += "\n\n" + s.Description
			seen[s.Description] = true
		}
		if s.Default != nil {
			raw, _ := json.Marshal(*s.Default)
			annotation := "Default (not inserted): `" + string(raw) + "`"
			if !seen[annotation] {
				text += "\n\n" + annotation
				seen[annotation] = true
			}
		}
	}
	if query.Ambiguous {
		text += "\n\nCandidate: branch selection is unresolved."
	}
	if origin, err := compat.SchemaSource(version, field.api, field.kind); err == nil {
		text += "\n\nSource: " + origin
	}
	if err := ctx.Err(); err != nil {
		return nil, err
	}
	return map[string]any{"contents": map[string]any{"kind": "markdown", "value": text}, "range": queryRange(snapshot, field.span)}, nil
}

func templateNameDocumentation(root *jsonschema.Schema, path []string) *jsonschema.Schema {
	p := path
	if len(p) > 1 && p[0] == "spec" && p[1] == "workflowSpec" {
		p = append([]string{"spec"}, p[2:]...)
	}
	if len(p) != 4 || p[0] != "spec" || p[1] != "templates" || p[3] != "name" {
		return nil
	}
	var nodes []*jsonschema.Schema
	var visit func(*jsonschema.Schema, []string, int)
	visit = func(s *jsonschema.Schema, path []string, depth int) {
		if s == nil || depth > 256 {
			return
		}
		if len(path) == 0 {
			nodes = append(nodes, s)
			return
		}
		if s.Ref != nil {
			visit(s.Ref, path, depth+1)
		}
		for _, child := range s.AnyOf {
			visit(child, path, depth+1)
		}
		for _, child := range s.AllOf {
			visit(child, path, depth+1)
		}
		if child := s.Properties[path[0]]; child != nil {
			visit(child, path[1:], depth+1)
		} else if _, err := strconv.Atoi(path[0]); err == nil {
			if s.Items2020 != nil {
				visit(s.Items2020, path[1:], depth+1)
			} else if child, ok := s.Items.(*jsonschema.Schema); ok {
				visit(child, path[1:], depth+1)
			}
		}
	}
	visit(root, path, 0)
	if len(nodes) == 0 {
		return nil
	}
	return &jsonschema.Schema{AllOf: nodes}
}
