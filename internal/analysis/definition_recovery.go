package analysis

import (
	"context"
	"strings"

	"github.com/lazysnail1024/k8s-crd-toolkit/source"
	"github.com/lazysnail1024/k8s-crd-toolkit/yamlview"
	"github.com/lazysnail1024/zed-argo-workflows/internal/worktrace"
)

// RecoverLocalTemplateDefinitions serves only the ordinary YAML Definition
// request. Completing an unrelated EOF flow value may expose a complete local
// template arena; it never proves that the original resource or scope is valid.
// References, symbols, diagnostics, Helm and edit callers keep their strict path.
func RecoverLocalTemplateDefinitions(ctx context.Context, snapshot source.Snapshot, version string, pos source.Position) ([]Location, error) {
	result := []Location{}
	offset, err := snapshot.Offset(pos)
	if err != nil {
		return result, nil
	}
	view, err := parseView(ctx, snapshot)
	if err != nil {
		return result, ctx.Err()
	}
	original, found := recoveryDocument(view, offset)
	padding, bounded := recoveryPadding(snapshot.Text())
	if !found || original.Complete() || !bounded || len(snapshot.Text()) > 5*1024*1024-len(padding)-2 {
		return result, ctx.Err()
	}
	// At most two EOF closers are considered. No existing byte is removed,
	// replaced or moved, including bytes in other YAML documents.
	for _, suffix := range []string{"]", "}", "]]", "]}", "}]", "}}"} {
		if err := ctx.Err(); err != nil {
			return result[:0], err
		}
		temporary, err := source.New(snapshot.URI(), snapshot.Revision(), snapshot.Text()+padding+suffix)
		if err != nil {
			return result[:0], err
		}
		// Candidate bytes must not read or populate invocation/FactStore state.
		candidateCtx := WithLogicalResult(context.WithValue(ctx, invocationKey{}, (*invocation)(nil)))
		location, ok := recoveredTemplateLocation(candidateCtx, snapshot, original, temporary, version, offset, len(suffix))
		if !ok {
			continue
		}
		if len(result) > 0 && result[0] != location {
			return result[:0], nil
		}
		if len(result) == 0 {
			result = append(result, location)
		}
	}
	if err := ctx.Err(); err != nil {
		return result[:0], err
	}
	return result, nil
}

// A closer on an existing empty final line still needs the surrounding block's
// indentation. Reuse the last content line's indentation without changing any
// original whitespace; strict parsing and the flow checks remain authoritative.
func recoveryPadding(text string) (string, bool) {
	last := text[strings.LastIndexByte(text, '\n')+1:]
	if strings.Trim(last, " ") != "" {
		return "", true
	}
	content := strings.TrimRight(text, " \r\n")
	line := content[strings.LastIndexByte(content, '\n')+1:]
	indent := len(line) - len(strings.TrimLeft(line, " "))
	if indent > 256 {
		return "", false
	}
	return strings.Repeat(" ", max(0, indent-len(last))), true
}

func recoveryDocument(view yamlview.View, offset int) (yamlview.Document, bool) {
	for _, doc := range view.Documents() {
		if offset >= doc.Span().Start && offset < doc.Span().End {
			return doc, true
		}
	}
	return yamlview.Document{}, false
}

func recoveredTemplateLocation(ctx context.Context, original source.Snapshot, prior yamlview.Document, candidate source.Snapshot, version string, offset, closers int) (Location, bool) {
	worktrace.Parse(ctx, worktrace.ParseRecoveryCalls, len(candidate.Text()))
	view, err := yamlview.Parse(ctx, candidate)
	if err != nil {
		return Location{}, false
	}
	doc, found := recoveryDocument(view, offset)
	if !found || !doc.Complete() || doc.Span().Start != prior.Span().Start {
		return Location{}, false
	}
	visits := 0
	flows, safe := recoverySyntax(ctx, doc.Root(), len(original.Text()), 0, &visits)
	if !safe || flows != closers {
		return Location{}, false
	}
	value, err := project(ctx, candidate, doc, version)
	if err != nil || !recoveryScope(original, doc.Root(), value, offset) {
		return Location{}, false
	}
	// Reuse the ordinary binder only after the entire declaration collection
	// and its enclosing block scopes have been proven independent of repair.
	bindings, err := collectLocalBindings(ctx, candidate, version, offset, true)
	if err != nil {
		return Location{}, false
	}
	keys := selectedDomain(bindings, offset)
	if len(keys) != 1 || keys[0].owner != -1 || keys[0].kind != "template" || !workflowFieldName.MatchString(keys[0].name) {
		return Location{}, false
	}
	key := keys[0]
	declarations := bindings.declarations[key]
	if bindings.declarationCount[key] != 1 || len(declarations) != 1 || !recoverySelection(bindings, key, offset, len(original.Text())) {
		return Location{}, false
	}
	span := declarations[0].span
	if span.Start < prior.Span().Start || span.End > len(original.Text()) {
		return Location{}, false
	}
	start, startErr := original.Position(span.Start)
	end, endErr := original.Position(span.End)
	if startErr != nil || endErr != nil {
		return Location{}, false
	}
	return Location{URI: string(original.URI()), Range: Range{Position{start.Line, start.UTF16Column}, Position{end.Line, end.UTF16Column}}}, true
}

// Every appended punctuation byte must close a real flow collection. A suffix
// consumed by a scalar or comment is not recovery. Alias/merge paths remain
// unsupported here.
func recoverySyntax(ctx context.Context, node yamlview.Node, originalEnd, depth int, visits *int) (int, bool) {
	*visits++
	if ctx.Err() != nil || *visits > 100000 || depth > 256 || node.Damaged() || node.Kind() == yamlview.Error || node.Kind() == yamlview.Alias {
		return 0, false
	}
	flows := 0
	if node.Span().End > originalEnd {
		if node.Kind() == yamlview.Scalar {
			return 0, false
		}
		if node.Span().Start < originalEnd && (strings.HasPrefix(node.Raw(), "[") || strings.HasPrefix(node.Raw(), "{")) {
			flows++
		}
	}
	for _, member := range node.Members() {
		if stringValue(member.Key) == "<<" {
			return 0, false
		}
		for _, child := range []yamlview.Node{member.Key, member.Value} {
			count, safe := recoverySyntax(ctx, child, originalEnd, depth+1, visits)
			if !safe {
				return 0, false
			}
			flows += count
		}
	}
	for _, child := range node.Elements() {
		count, safe := recoverySyntax(ctx, child, originalEnd, depth+1, visits)
		if !safe {
			return 0, false
		}
		flows += count
	}
	return flows, true
}

func recoveryScope(original source.Snapshot, root yamlview.Node, value any, offset int) bool {
	api, _ := queryMember(value, "apiVersion").(string)
	kind, _ := queryMember(value, "kind").(string)
	if !owned(api, kind) || schemaOnlyKind(kind) || !recoveryBlockMapping(root) {
		return false
	}
	for _, field := range []string{"apiVersion", "kind"} {
		node, ok := recoveryMember(original, root, field)
		if !ok || node.Kind() != yamlview.Scalar || !recoveryOriginalNode(original, node) {
			return false
		}
	}
	spec, ok := recoveryMember(original, root, "spec")
	if !ok || !recoveryBlockMapping(spec) {
		return false
	}
	if kind == "CronWorkflow" {
		spec, ok = recoveryMember(original, spec, "workflowSpec")
		if !ok || !recoveryBlockMapping(spec) {
			return false
		}
	}
	if _, inherited := recoveryMember(original, spec, "workflowTemplateRef"); inherited {
		return false
	}
	templates, ok := recoveryMember(original, spec, "templates")
	return ok && templates.Kind() == yamlview.Sequence && recoveryOriginalNode(original, templates) && recoveryCallScope(original, spec, offset)
}

// A selected call must belong to an entirely original spec member. This covers
// entrypoint/onExit scalars and complete templates or hooks, while refusing a
// hook whose containing flow mappings become complete only after EOF repair.
func recoveryCallScope(original source.Snapshot, spec yamlview.Node, offset int) bool {
	for _, member := range spec.Members() {
		span := member.Value.Span()
		if offset >= span.Start && offset < span.End {
			return recoveryOriginalNode(original, member.Key) && recoveryOriginalNode(original, member.Value)
		}
	}
	return false
}

func recoveryBlockMapping(node yamlview.Node) bool {
	return node.Kind() == yamlview.Mapping && !strings.HasPrefix(node.Raw(), "{")
}

func recoveryMember(original source.Snapshot, parent yamlview.Node, name string) (yamlview.Node, bool) {
	for _, member := range parent.Members() {
		if stringValue(member.Key) == name {
			return member.Value, recoveryOriginalNode(original, member.Key)
		}
	}
	return yamlview.Node{}, false
}

func recoveryOriginalNode(original source.Snapshot, node yamlview.Node) bool {
	if node.Kind() == yamlview.Empty || node.Damaged() || node.Span().End > len(original.Text()) {
		return false
	}
	raw, err := original.Slice(node.Span())
	return err == nil && raw == node.Raw()
}

func recoverySelection(bindings *localRenameBindings, key renameKey, offset, originalEnd int) bool {
	for span, keys := range bindings.queryPositions {
		if offset >= span.Start && offset < span.End && span.End <= originalEnd && keys[key] && !bindings.queryAmbiguous[span][key] {
			return true
		}
	}
	return false
}
