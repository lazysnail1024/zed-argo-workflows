package analysis

import (
	"context"
	"sort"
	"strconv"
	"strings"

	"github.com/lazysnail1024/k8s-crd-toolkit/source"
	"github.com/lazysnail1024/zed-argo-workflows/internal/workspace"
)

// The server can avoid loading a workspace for unrelated completion positions.
func ExternalNamePosition(ctx context.Context, snapshot source.Snapshot, version string, pos source.Position) bool {
	field, err := queryField(ctx, snapshot, version, pos)
	n := len(field.path)
	if err != nil || field.key || field.shared {
		return false
	}
	if n >= 2 && (field.path[n-2] == "templateRef" || field.path[n-2] == "workflowTemplateRef") && (field.path[n-1] == "name" || field.path[n-1] == "template") {
		return true
	}
	if n > 0 && field.path[n-1] == "name" {
		for _, part := range field.path {
			if part == "arguments" {
				return true
			}
		}
	}
	offset, err := snapshot.Offset(pos)
	if err != nil {
		return false
	}
	b, err := collectLocalBindings(ctx, snapshot, version, offset, true)
	if err != nil {
		return false
	}
	for _, ref := range b.externalOutputs {
		if offset >= ref.span.Start && offset <= ref.span.End {
			return true
		}
	}
	return false
}

func completeExternalMembers(ctx context.Context, w workspace.Workspace, uri string, pos source.Position) ([]map[string]any, error) {
	result := []map[string]any{}
	for index, d := range w.Documents {
		if string(d.Snapshot.URI()) != uri {
			continue
		}
		offset, err := d.Snapshot.Offset(pos)
		if err != nil {
			return result, err
		}
		text := d.Snapshot.Text()
		a, z := offset, offset
		for a > 0 && identifierByte(text[a-1]) {
			a--
		}
		for z < len(text) && identifierByte(text[z]) {
			z++
		}
		prefix := text[a:offset]
		temporary, err := source.New(d.Snapshot.URI(), d.Snapshot.Revision(), text[:a]+"__argo_domain_query__"+text[z:])
		if err != nil {
			return result, err
		}
		w.Documents = append([]workspace.Document(nil), w.Documents...)
		w.Documents[index].Snapshot = temporary
		query := &externalQuery{uri: uri, offset: a, names: map[string]int{}, origins: map[string]string{}, inexact: map[string]bool{}}
		external(WithLogicalResult(context.WithValue(ctx, invocationKey{}, (*invocation)(nil))), w, query)
		names := []string{}
		for name, count := range query.names {
			if count == 1 && query.origins[name] != "" && !query.inexact[name] && parameterName.MatchString(name) && strings.HasPrefix(name, prefix) {
				names = append(names, name)
			}
		}
		sort.Strings(names)
		for _, name := range names {
			result = append(result, map[string]any{"label": name, "kind": 6, "detail": "external template member · " + query.origins[name] + " · Argo Workflows " + d.Target.Version, "insertTextFormat": 1, "textEdit": map[string]any{"range": queryRange(d.Snapshot, source.Span{Start: a, End: z}), "newText": domainNameText(ctx, d.Snapshot, d.Target.Version, pos, source.Span{Start: a, End: z}, name)}})
		}
		break
	}
	return result, ctx.Err()
}

func CompleteExternalNames(ctx context.Context, w workspace.Workspace, uri string, pos source.Position) ([]map[string]any, error) {
	result := []map[string]any{}
	resources, _, _, _ := externalResources(ctx, w, true)
	for _, caller := range resources {
		snapshot := caller.document.Snapshot
		if string(snapshot.URI()) != uri {
			continue
		}
		offset, err := snapshot.Offset(pos)
		if err != nil {
			return result, err
		}
		if offset < caller.root.Span().Start || offset > caller.root.Span().End {
			continue
		}
		field, err := queryField(ctx, snapshot, caller.document.Target.Version, pos)
		if err != nil {
			return result, err
		}
		n := len(field.path)
		if field.key || n < 2 || field.path[n-2] != "templateRef" && field.path[n-2] != "workflowTemplateRef" {
			continue
		}
		leaf := field.path[n-1]
		if leaf != "name" && leaf != "template" {
			continue
		}
		parent := field.value
		for _, part := range field.path[:n-1] {
			parent = queryMember(parent, part)
		}
		ref, ok := parent.(map[string]any)
		if !ok {
			continue
		}
		cluster := false
		if v, exists := ref["clusterScope"]; exists {
			cluster, ok = v.(bool)
			if !ok {
				continue
			}
		}
		wanted := "WorkflowTemplate"
		if cluster {
			wanted = "ClusterWorkflowTemplate"
		}
		end := offset
		if end > field.span.End {
			end = field.span.End
		}
		if end < field.span.Start {
			continue
		}
		prefix := strings.Trim(snapshot.Text()[field.span.Start:end], "\"'")
		if strings.Contains(prefix, "{{") {
			continue
		}
		counts := map[string]int{}
		inexact := map[string]bool{}
		matches := map[string][]resource{}
		for _, candidate := range resources {
			if candidate.document.Target.ID != caller.document.Target.ID || candidate.kind != wanted || !cluster && (caller.namespace == "" || candidate.namespace != caller.namespace) {
				continue
			}
			matches[candidate.name] = append(matches[candidate.name], candidate)
		}
		if leaf == "name" {
			for name, group := range matches {
				if name != "" && len(group) == 1 {
					counts[name] = 1
				}
			}
		} else {
			name, _ := ref["name"].(string)
			if strings.Contains(name, "{{") || len(matches[name]) != 1 {
				continue
			}
			for key, count := range matches[name][0].declarations {
				if key.owner == -1 && key.kind == "template" {
					counts[key.name] = count
					inexact[key.name] = true
				}
			}
			for _, template := range member(matches[name][0].spec, "templates").Elements() {
				node := member(template, "name")
				name := stringValue(node)
				_, _, exact := exactName(node)
				if name != "" {
					if exact {
						inexact[name] = false
					}
				}
			}
		}
		names := []string{}
		for name, count := range counts {
			if count == 1 && !inexact[name] && strings.HasPrefix(name, prefix) {
				names = append(names, name)
			}
		}
		sort.Strings(names)
		for _, name := range names {
			originName := name
			if leaf == "template" {
				originName, _ = ref["name"].(string)
			}
			origin := externalOrigin(matches[originName][0])
			result = append(result, map[string]any{"label": name, "kind": 6, "detail": origin + " · Argo Workflows " + caller.document.Target.Version, "insertTextFormat": 1, "textEdit": map[string]any{"range": queryRange(snapshot, field.span), "newText": field.before + strconv.Quote(name) + field.after}})
		}
	}
	if len(result) == 0 {
		return completeExternalMembers(ctx, w, uri, pos)
	}
	return result, ctx.Err()
}
