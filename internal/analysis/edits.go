package analysis

import (
	"context"
	"fmt"
	"github.com/lazysnail1024/k8s-crd-toolkit/edit"
	"github.com/lazysnail1024/k8s-crd-toolkit/source"
	"github.com/lazysnail1024/zed-argo-workflows/internal/workspace"
	"sort"
)

type Fix struct {
	Title string
	Code  string
	Plan  edit.Plan
}

func safePlan(ctx context.Context, s source.Snapshot, version string, plan edit.Plan) error {
	next, err := edit.Apply(s, s.Revision()+1, plan)
	if err != nil {
		return err
	}
	before := AnalyzeSchema(WithLogicalResult(ctx), s, version)
	after := AnalyzeSchema(WithLogicalResult(ctx), next, version)
	if len(before.NotEvaluated) > 0 || len(after.NotEvaluated) > 0 {
		return fmt.Errorf("analysis is incomplete")
	}
	counts := map[string]int{}
	for _, d := range before.Diagnostics {
		if d.Severity == "Error" {
			counts[d.Code+"\n"+d.Message]++
		}
	}
	for _, d := range after.Diagnostics {
		if d.Severity == "Error" {
			key := d.Code + "\n" + d.Message
			counts[key]--
			if counts[key] < 0 {
				return fmt.Errorf("edit introduces an error")
			}
		}
	}
	return nil
}
func distance(a, b string) int {
	previous := make([]int, len(b)+1)
	for j := range previous {
		previous[j] = j
	}
	for i := 0; i < len(a); i++ {
		current := make([]int, len(b)+1)
		current[0] = i + 1
		for j := 0; j < len(b); j++ {
			cost := 0
			if a[i] != b[j] {
				cost = 1
			}
			current[j+1] = min(current[j]+1, previous[j+1]+1, previous[j]+cost)
		}
		previous = current
	}
	return previous[len(b)]
}
func FieldFixes(ctx context.Context, s source.Snapshot, version string, requested Range) []Fix {
	fixes := []Fix{}
	result := AnalyzeSchema(ctx, s, version)
	for _, d := range result.Diagnostics {
		if d.Code != "SCHEMA_UNKNOWN_FIELD" || d.PositionKind != "exact" || d.Range.Start.Line < requested.Start.Line || d.Range.Start.Line > requested.End.Line {
			continue
		}
		field, err := queryField(ctx, s, version, source.Position{Line: d.Range.Start.Line, UTF16Column: d.Range.Start.Character})
		if err != nil || !field.key || field.name == "" || field.schema == nil {
			continue
		}
		raw := s.Text()[field.span.Start:field.span.End]
		if raw != field.name {
			continue
		}
		nodes, _ := queryNodes(ctx, field.schema, field.path, field.value, 0)
		candidates := map[string]bool{}
		for _, schema := range nodes {
			for name := range schema.Properties {
				if !field.existing[name] && distance(name, field.name) == 1 {
					candidates[name] = true
				}
			}
		}
		names := []string{}
		for name := range candidates {
			names = append(names, name)
		}
		sort.Strings(names)
		for _, name := range names {
			path := append(append([]string(nil), field.path...), name)
			switch queryType(ctx, field.schema, path, field.value) {
			case "string", "integer", "number", "boolean":
			default:
				continue
			}
			plan := edit.Plan{BaseRevision: s.Revision(), Replacements: []edit.Replacement{{Span: field.span, ExpectedText: raw, NewText: name}}}
			if safePlan(ctx, s, version, plan) == nil {
				fixes = append(fixes, Fix{Title: "Replace field with " + name, Code: "FIX_FIELD_NAME", Plan: plan})
			}
		}
	}
	return fixes
}

func PlanEdits(s source.Snapshot, plan edit.Plan) []map[string]any {
	result := []map[string]any{}
	for _, r := range plan.Replacements {
		a, _ := s.Position(r.Span.Start)
		b, _ := s.Position(r.Span.End)
		result = append(result, map[string]any{"range": Range{Position{a.Line, a.UTF16Column}, Position{b.Line, b.UTF16Column}}, "newText": r.NewText})
	}
	return result
}

// SafeScopePlan simulates against the same selected scope used by diagnostics.
// Text/revision guards do not establish safety of changed external bindings.
func SafeScopePlan(ctx context.Context, w workspace.Workspace, document workspace.Document, plan edit.Plan) error {
	if w.IncompleteScopes[document.Target.ID] {
		return fmt.Errorf("scope input is incomplete")
	}
	next, err := edit.Apply(document.Snapshot, document.Snapshot.Revision()+1, plan)
	if err != nil {
		return err
	}
	before, after := w, w
	before.Documents = nil
	after.Documents = nil
	for _, d := range w.Documents {
		if d.Target.ID != document.Target.ID {
			continue
		}
		before.Documents = append(before.Documents, d)
		if d.Snapshot.URI() == document.Snapshot.URI() {
			d.Snapshot = next
		}
		after.Documents = append(after.Documents, d)
	}
	collect := func(input workspace.Workspace) ([]Diagnostic, error) {
		ctx := WithLogicalResult(ctx)
		diagnostics := []Diagnostic{}
		for _, d := range input.Documents {
			result := AnalyzeSchema(ctx, d.Snapshot, d.Target.Version)
			if len(result.NotEvaluated) != 0 {
				return nil, fmt.Errorf("scope analysis is incomplete")
			}
			diagnostics = append(diagnostics, result.Diagnostics...)
		}
		externalDiagnostics, _, complete, _ := external(ctx, input, nil)
		if !complete[document.Target.ID] {
			return nil, fmt.Errorf("external scope analysis is incomplete")
		}
		diagnostics = append(diagnostics, externalDiagnostics...)
		return diagnostics, nil
	}
	oldDiagnostics, err := collect(before)
	if err != nil {
		return err
	}
	newDiagnostics, err := collect(after)
	if err != nil {
		return err
	}
	counts := map[string]int{}
	for _, d := range oldDiagnostics {
		if d.Severity == "Error" {
			counts[d.URI+"\n"+d.Code+"\n"+d.Message]++
		}
	}
	for _, d := range newDiagnostics {
		if d.Severity == "Error" {
			key := d.URI + "\n" + d.Code + "\n" + d.Message
			counts[key]--
			if counts[key] < 0 {
				return fmt.Errorf("edit introduces a scope error")
			}
		}
	}
	return nil
}
