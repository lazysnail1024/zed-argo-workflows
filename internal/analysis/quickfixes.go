package analysis

import (
	"context"
	"encoding/json"
	"sort"
	"strings"

	"github.com/lazysnail1024/k8s-crd-toolkit/edit"
	"github.com/lazysnail1024/k8s-crd-toolkit/schemaquery"
	"github.com/lazysnail1024/k8s-crd-toolkit/source"
	"github.com/lazysnail1024/k8s-crd-toolkit/yamlview"
	"github.com/lazysnail1024/zed-argo-workflows/internal/workspace"
)

func singleLiteral(n yamlview.Node) bool {
	raw := n.Raw()
	if n.Kind() != yamlview.Scalar || raw == "" || strings.ContainsAny(raw, "\r\n") || strings.ContainsAny(raw[:1], "|>&*!") {
		return false
	}
	_, err := decode(raw)
	return err == nil
}
func requestedDiagnostic(d Diagnostic, requested Range) bool {
	a, b := d.Range.Start, d.Range.End
	return (a.Line < requested.End.Line || a.Line == requested.End.Line && a.Character <= requested.End.Character) && (b.Line > requested.Start.Line || b.Line == requested.Start.Line && b.Character >= requested.Start.Character)
}
func scalarPlan(s source.Snapshot, n yamlview.Node, value any) (edit.Plan, bool) {
	if !singleLiteral(n) {
		return edit.Plan{}, false
	}
	encoded, err := json.Marshal(value)
	if err != nil || strings.ContainsAny(string(encoded), "\r\n") {
		return edit.Plan{}, false
	}
	return edit.Plan{BaseRevision: s.Revision(), Replacements: []edit.Replacement{{Span: n.Span(), ExpectedText: n.Raw(), NewText: string(encoded)}}}, true
}
func diagnosticGone(ctx context.Context, w workspace.Workspace, d workspace.Document, plan edit.Plan, target Diagnostic) bool {
	ctx = WithLogicalResult(ctx)
	next, err := edit.Apply(d.Snapshot, d.Snapshot.Revision()+1, plan)
	if err != nil {
		return false
	}
	after := w
	after.Documents = append([]workspace.Document(nil), w.Documents...)
	for i := range after.Documents {
		if after.Documents[i].Snapshot.URI() == next.URI() {
			after.Documents[i].Snapshot = next
		}
	}
	diagnostics := AnalyzeSchema(ctx, next, d.Target.Version).Diagnostics
	externalDiagnostics, _ := ExternalDiagnostics(ctx, after)
	diagnostics = append(diagnostics, externalDiagnostics...)
	if ExpansionError(ctx) != nil {
		return false
	}
	for _, v := range diagnostics {
		if v.URI == target.URI && v.Code == target.Code && v.Range.Start == target.Range.Start {
			return false
		}
	}
	return true
}

// ValueFixes offers explicit choices from current schema and resolved scope only.
func ValueFixes(ctx context.Context, w workspace.Workspace, d workspace.Document, requested Range) []Fix {
	fixes := []Fix{}
	s := d.Snapshot
	diagnostics := AnalyzeSchema(ctx, s, d.Target.Version).Diagnostics
	externalDiagnostics, _ := ExternalDiagnostics(ctx, w)
	diagnostics = append(diagnostics, externalDiagnostics...)
	if ExpansionError(ctx) != nil {
		return fixes
	}
	view, err := parseView(ctx, s)
	if err != nil {
		return fixes
	}
	for _, diag := range diagnostics {
		if diag.URI != string(s.URI()) || diag.PositionKind != "exact" || !requestedDiagnostic(diag, requested) {
			continue
		}
		if diag.Code != "SCHEMA_VALIDATION" && diag.Code != "WF_LOCAL_TEMPLATE_MISSING" && diag.Code != "WF_TEMPLATE_REF_UNRESOLVED" {
			continue
		}
		pos := source.Position{Line: diag.Range.Start.Line, UTF16Column: diag.Range.Start.Character}
		offset, err := s.Offset(pos)
		if err != nil {
			continue
		}
		var node yamlview.Node
		var docSpan source.Span
		var find func(yamlview.Node)
		find = func(n yamlview.Node) {
			if n.Kind() == yamlview.Scalar && n.Span().Start == offset {
				node = n
			}
			for _, m := range n.Members() {
				find(m.Value)
			}
			for _, e := range n.Elements() {
				find(e)
			}
		}
		for _, doc := range view.Documents() {
			if offset >= doc.Span().Start && offset < doc.Span().End {
				find(doc.Root())
				docSpan = doc.Span()
			}
		}
		if !singleLiteral(node) {
			continue
		}
		values := []any{}
		code := "FIX_REFERENCE_CANDIDATE"
		if diag.Code == "SCHEMA_VALIDATION" {
			field, err := queryField(ctx, s, d.Target.Version, pos)
			if err != nil || field.key || field.schema == nil || field.span != node.Span() {
				continue
			}
			values, err = schemaquery.Values(ctx, field.schema, field.path, field.value, schemaquery.Limits{})
			if err != nil {
				continue
			}
			code = "FIX_ENUM_VALUE"
		} else if diag.Code == "WF_LOCAL_TEMPLATE_MISSING" {
			names := map[string]int{}
			for _, sym := range DomainSymbols(ctx, s, d.Target.Version) {
				at, err := s.Offset(source.Position{Line: sym.Location.Range.Start.Line, UTF16Column: sym.Location.Range.Start.Character})
				if err == nil && sym.Container == "template" && at >= docSpan.Start && at < docSpan.End {
					names[sym.Name]++
				}
			}
			for name, count := range names {
				if count == 1 {
					values = append(values, name)
				}
			}
		} else {
			names := map[string]bool{}
			for _, other := range w.Documents {
				if other.Target.ID != d.Target.ID {
					continue
				}
				v, e := parseView(ctx, other.Snapshot)
				if e != nil {
					continue
				}
				for _, doc := range v.Documents() {
					root := doc.Root()
					kind := stringValue(member(root, "kind"))
					if kind != "WorkflowTemplate" && kind != "ClusterWorkflowTemplate" {
						continue
					}
					if _, err := project(ctx, other.Snapshot, doc, other.Target.Version); err != nil {
						continue
					}
					names[stringValue(member(member(root, "metadata"), "name"))] = true
					for _, t := range member(member(root, "spec"), "templates").Elements() {
						names[stringValue(member(t, "name"))] = true
					}
				}
			}
			for name := range names {
				if name != "" {
					values = append(values, name)
				}
			}
		}
		sort.Slice(values, func(i, j int) bool {
			a, _ := json.Marshal(values[i])
			b, _ := json.Marshal(values[j])
			return string(a) < string(b)
		})
		for _, value := range values {
			plan, ok := scalarPlan(s, node, value)
			if !ok {
				continue
			}
			local := SafeLocalPlan(ctx, w, d, plan) == nil
			if !local && SafeScopePlan(ctx, w, d, plan) != nil || !diagnosticGone(WithLogicalResult(ctx), w, d, plan, diag) {
				continue
			}
			if diag.Code == "WF_TEMPLATE_REF_UNRESOLVED" {
				next, _ := edit.Apply(s, s.Revision()+1, plan)
				after := w
				after.Documents = append([]workspace.Document(nil), w.Documents...)
				for i := range after.Documents {
					if after.Documents[i].Snapshot.URI() == s.URI() {
						after.Documents[i].Snapshot = next
					}
				}
				if len(ExternalDefinitions(ctx, after, string(s.URI()), pos)) != 1 {
					continue
				}
			}
			fixes = append(fixes, Fix{Title: code + ": " + plan.Replacements[0].NewText, Code: code, Plan: plan})
		}
	}
	return fixes
}
