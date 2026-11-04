package analysis

import (
	"context"
	"sort"
	"strconv"
	"strings"

	"github.com/lazysnail1024/k8s-crd-toolkit/source"
)

type queryOutputReference struct {
	producer   renameKey
	kind, name string
	span       source.Span
}

// Count declarations from strict projection before filtering source origins.
// Aliases and escaped spellings still participate in identity cardinality.
func semanticDeclarations(value any, kind string) map[renameKey]int {
	counts := map[renameKey]int{}
	count := func(owner int, kind string, value any) {
		values, _ := value.([]any)
		for _, item := range values {
			if name, ok := queryMember(item, "name").(string); ok && name != "" {
				counts[renameKey{owner, kind, name}]++
			}
		}
	}
	spec := queryMember(value, "spec")
	if kind == "CronWorkflow" {
		spec = queryMember(spec, "workflowSpec")
	}
	count(-1, "workflow.parameters", queryMember(queryMember(spec, "arguments"), "parameters"))
	templates, _ := queryMember(spec, "templates").([]any)
	count(-1, "template", templates)
	for i, t := range templates {
		for _, section := range []string{"inputs", "outputs"} {
			for _, kind := range []string{"parameters", "artifacts"} {
				count(i, section+"."+kind, queryMember(queryMember(t, section), kind))
			}
		}
		count(i, "task", queryMember(queryMember(t, "dag"), "tasks"))
		groups, _ := queryMember(t, "steps").([]any)
		for _, group := range groups {
			count(i, "step", group)
		}
	}
	return counts
}

type DomainSymbol struct {
	Name      string
	Kind      int
	Container string
	Location  Location
}

func spanLocation(snapshot source.Snapshot, span source.Span) Location {
	a, _ := snapshot.Position(span.Start)
	z, _ := snapshot.Position(span.End)
	return Location{URI: string(snapshot.URI()), Range: Range{Start: Position{a.Line, a.UTF16Column}, End: Position{z.Line, z.UTF16Column}}}
}
func domainBindings(ctx context.Context, snapshot source.Snapshot, version string) []*localRenameBindings {
	result := []*localRenameBindings{}
	view, err := parseView(ctx, snapshot)
	if err != nil {
		return result
	}
	for _, doc := range view.Documents() {
		if ctx.Err() != nil {
			return nil
		}
		b, err := collectLocalBindings(ctx, snapshot, version, doc.Span().Start, true)
		if err == nil {
			result = append(result, b)
		}
	}
	return result
}
func selectedDomain(b *localRenameBindings, offset int) []renameKey {
	result := []renameKey{}
	for span, keys := range b.queryPositions {
		if len(b.queryAmbiguous[span]) > 0 || offset < span.Start || offset >= span.End {
			continue
		}
		for key := range keys {
			if len(b.declarations[key]) == 0 || b.declarationCount[key] != len(b.declarations[key]) {
				continue
			}
			if visible, constrained := b.visibility[span]; constrained && !visible[key] {
				continue
			}
			result = append(result, key)
		}
	}
	return result
}
func DomainSymbols(ctx context.Context, snapshot source.Snapshot, version string) []DomainSymbol {
	result := []DomainSymbol{}
	for _, b := range domainBindings(ctx, snapshot, version) {
		for key, declarations := range b.declarations {
			if ctx.Err() != nil {
				return nil
			}
			kind := 13
			if key.kind == "template" || key.kind == "task" || key.kind == "step" {
				kind = 12
			}
			container := key.kind
			if key.owner >= 0 {
				container = stringValue(member(b.templates[key.owner], "name")) + " · " + key.kind
			}
			for _, d := range declarations {
				result = append(result, DomainSymbol{key.name, kind, container, spanLocation(snapshot, d.span)})
			}
		}
	}
	sort.Slice(result, func(i, j int) bool {
		a, z := result[i].Location.Range.Start, result[j].Location.Range.Start
		return a.Line < z.Line || a.Line == z.Line && a.Character < z.Character
	})
	return result
}
func Definitions(ctx context.Context, snapshot source.Snapshot, version string, pos source.Position) []Location {
	result, _ := domainDefinitions(ctx, snapshot, version, pos)
	return result
}
func domainDefinitions(ctx context.Context, snapshot source.Snapshot, version string, pos source.Position) ([]Location, bool) {
	result := []Location{}
	offset, err := snapshot.Offset(pos)
	if err != nil {
		return result, false
	}
	blocked := false
	for _, b := range domainBindings(ctx, snapshot, version) {
		for span, keys := range b.queryPositions {
			if offset < span.Start || offset >= span.End {
				continue
			}
			if len(b.queryAmbiguous[span]) > 0 {
				blocked = true
			}
			for key := range keys {
				if visible, constrained := b.visibility[span]; constrained && !visible[key] {
					continue
				}
				if b.declarationCount[key] != len(b.declarations[key]) {
					blocked = true
				}
				for _, declaration := range b.declarations[key] {
					result = append(result, spanLocation(snapshot, declaration.span))
				}
			}
		}
	}
	return result, blocked
}
func References(ctx context.Context, snapshot source.Snapshot, version string, pos source.Position, includeDeclaration bool) []Location {
	result := []Location{}
	offset, err := snapshot.Offset(pos)
	if err != nil {
		return result
	}
	for _, b := range domainBindings(ctx, snapshot, version) {
		keys := selectedDomain(b, offset)
		if len(keys) != 1 {
			continue
		}
		key := keys[0]
		if len(b.declarations[key]) != 1 {
			continue
		}
		declaration := b.declarations[key][0].span
		for span, bound := range b.queryPositions {
			if b.queryAmbiguous[span][key] || !bound[key] || !includeDeclaration && span == declaration {
				continue
			}
			if visible, constrained := b.visibility[span]; constrained && !visible[key] {
				continue
			}
			result = append(result, spanLocation(snapshot, span))
		}
	}
	sort.Slice(result, func(i, j int) bool {
		a, z := result[i].Range.Start, result[j].Range.Start
		return a.Line < z.Line || a.Line == z.Line && a.Character < z.Character
	})
	return result
}

// Restrict names to literal identifiers before applying context-specific quoting.
func domainCompletionName(key renameKey) bool {
	if key.kind == "template" || key.kind == "task" || key.kind == "step" {
		if !workflowFieldName.MatchString(key.name) {
			return false
		}
		return key.kind != "task" || key.name[0] < '0' || key.name[0] > '9'
	}
	return parameterName.MatchString(key.name)
}

// Recover one identifier, then ask the ordinary lexical binder for its scope.
// The temporary snapshot is neither cached nor published. A context without a
// proven reference identity cannot receive an unscoped list of names.
func CompleteNames(ctx context.Context, snapshot source.Snapshot, version string, pos source.Position) ([]map[string]any, error) {
	result := []map[string]any{}
	offset, err := snapshot.Offset(pos)
	if err != nil {
		return result, err
	}
	text := snapshot.Text()
	a, z := offset, offset
	for a > 0 && identifierByte(text[a-1]) {
		a--
	}
	for z < len(text) && identifierByte(text[z]) {
		z++
	}
	prefix := text[a:offset]
	marker := "__argo_domain_query__"
	temporary, err := source.New(snapshot.URI(), snapshot.Revision(), text[:a]+marker+text[z:])
	if err != nil {
		return result, err
	}
	// No invocation reuse: this artificial token must not enter analysis caches.
	b, err := collectLocalBindings(WithLogicalResult(context.WithValue(ctx, invocationKey{}, (*invocation)(nil))), temporary, version, a, true)
	if err != nil {
		return result, ctx.Err()
	}
	span := source.Span{Start: a, End: a + len(marker)}
	keys := b.queryPositions[span]
	contexts := b.argumentContexts[span]
	workflowDeclaration := false
	for key := range keys {
		if version != "3.6.0" || key.kind != "workflow.parameters" {
			continue
		}
		for _, declaration := range b.declarations[key] {
			if declaration.span == span {
				workflowDeclaration = true
			}
		}
	}
	callContext := len(contexts) > 0 && (len(keys) == 0 || workflowDeclaration)
	if callContext {
		keys = contexts
	}
	if len(keys) == 0 || (!callContext || version != "3.6.0") && len(keys) != 1 {
		return result, ctx.Err()
	}
	for key := range keys {
		for _, declaration := range b.declarations[key] {
			if declaration.span == span {
				return result, ctx.Err()
			}
		}
	}
	names := []string{}
	details := map[string]string{}
	for candidate, declarations := range b.declarations {
		if ctx.Err() != nil {
			return nil, ctx.Err()
		}
		selected := false
		for key := range keys {
			if candidate.owner == key.owner && candidate.kind == key.kind {
				selected = true
			}
		}
		if !selected || len(declarations) != 1 || b.declarationCount[candidate] != 1 || !domainCompletionName(candidate) || !strings.HasPrefix(candidate.name, prefix) {
			continue
		}
		if visible, constrained := b.visibility[span]; constrained && !visible[candidate] {
			continue
		}
		if _, exists := details[candidate.name]; !exists {
			names = append(names, candidate.name)
		}
		details[candidate.name] = candidate.kind + " · " + b.queryOrigin + " · Argo Workflows " + version
	}
	sort.Strings(names)
	for _, name := range names {
		result = append(result, map[string]any{"label": name, "kind": 6, "detail": details[name], "insertTextFormat": 1, "textEdit": map[string]any{"range": queryRange(snapshot, source.Span{Start: a, End: z}), "newText": domainNameText(ctx, snapshot, version, pos, source.Span{Start: a, End: z}, name)}})
	}
	return result, ctx.Err()
}

// A whole plain scalar must retain a string value. Quoted scalar contents and
// expression members already have their own syntax and retain literal names.
func domainNameText(ctx context.Context, snapshot source.Snapshot, version string, pos source.Position, span source.Span, name string) string {
	value, err := decode(name)
	if text, ok := value.(string); err == nil && ok && text == name {
		return name
	}
	field, err := queryField(ctx, snapshot, version, pos)
	if err == nil && !field.key && field.span == span {
		return strconv.Quote(name)
	}
	return name
}
