package analysis

import (
	"context"
	"encoding/json"
	"fmt"
	"sort"
	"strings"

	"github.com/lazysnail1024/k8s-crd-toolkit/source"
	"github.com/lazysnail1024/zed-argo-workflows/compat"
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
	nodes, ambiguous := queryNodes(ctx, field.schema, field.path, field.value, 0)
	if field.key {
		properties := map[string]bool{}
		for _, s := range nodes {
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
			if !queryAllowsKey(ctx, field.schema, path, field.value, 0) {
				continue
			}
			detail := queryType(ctx, field.schema, path, field.value) + " · Argo Workflows " + version
			if ambiguous {
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
		values := map[string]any{}
		for _, s := range nodes {
			if s.Enum != nil {
				for _, v := range s.Enum.Values {
					if raw, e := json.Marshal(v); e == nil {
						values[string(raw)] = v
					}
				}
			}
			if s.Const != nil {
				if raw, e := json.Marshal(*s.Const); e == nil {
					values[string(raw)] = *s.Const
				}
			}
		}
		names := []string{}
		for name, value := range values {
			if queryAccepts(ctx, field.schema, field.path, field.value, value, 0) {
				names = append(names, name)
			}
		}
		sort.Strings(names)
		for _, name := range names {
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
	nodes, ambiguous := queryNodes(ctx, field.schema, path, field.value, 0)
	if len(nodes) == 0 {
		return nil, nil
	}
	text := fmt.Sprintf("**%s**\n\nType: `%s`\n\nArgo Workflows target: `%s`", field.name, queryType(ctx, field.schema, path, field.value), version)
	seen := map[string]bool{}
	for _, s := range nodes {
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
	if ambiguous {
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
