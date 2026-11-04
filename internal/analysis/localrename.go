package analysis

import (
	"context"
	"fmt"
	"regexp"
	"sort"
	"strings"

	"github.com/lazysnail1024/k8s-crd-toolkit/edit"
	"github.com/lazysnail1024/k8s-crd-toolkit/source"
	"github.com/lazysnail1024/k8s-crd-toolkit/yamlview"
)

type renameKey struct {
	owner      int
	kind, name string
}
type renameOccurrence struct {
	span source.Span
	dot  int
}
type localRenameBindings struct {
	staticExternalCalls bool
	ctx                 context.Context
	shared              map[source.Span]bool
	symbols             map[renameKey][]renameOccurrence
	positions           map[source.Span]renameKey
	templates           []yamlview.Node
	calls               map[renameKey]int
	snapshot            source.Snapshot
	err                 error
	blocked             map[renameKey]bool
	readOnly            bool
	declarations        map[renameKey][]renameOccurrence
	visibility          map[source.Span]map[renameKey]bool
	externalOutputs     []queryOutputReference
	queryOrigin         string
	queryPositions      map[source.Span]map[renameKey]bool
	queryAmbiguous      map[source.Span]map[renameKey]bool
	argumentContexts    map[source.Span]map[renameKey]bool
	declarationCount    map[renameKey]int
}

// Queries retain independently proven occurrences after an unrelated failure.
// Rename never enables this mode and still requires the complete closed set.
func (b *localRenameBindings) resumeQuery() {
	if b.readOnly && b.ctx.Err() == nil {
		b.err = nil
	}
}
func (b *localRenameBindings) queryReference(key renameKey, span source.Span) {
	if b.readOnly && span.End > span.Start && !sharedOrigin(b.shared, span) {
		if b.queryPositions[span] == nil {
			b.queryPositions[span] = map[renameKey]bool{}
		}
		b.queryPositions[span][key] = true
	}
}

func (b *localRenameBindings) add(key renameKey, occurrence renameOccurrence) {
	if b.err != nil {
		return
	}
	if occurrence.span.Start == occurrence.span.End {
		b.err = fmt.Errorf("empty rename location")
		return
	}
	if other, ok := b.positions[occurrence.span]; ok {
		if other != key {
			b.err = fmt.Errorf("source location has multiple binding identities")
		}
		return
	}
	b.positions[occurrence.span] = key
	b.symbols[key] = append(b.symbols[key], occurrence)
}
func exactName(n yamlview.Node) (string, source.Span, bool) {
	name := stringValue(n)
	raw := n.Raw()
	span := n.Span()
	if name == "" || n.Kind() != yamlview.Scalar {
		return "", span, false
	}
	if raw == name {
		return name, span, true
	}
	if len(raw) >= 2 && (raw[0] == '\'' && raw[len(raw)-1] == '\'' || raw[0] == '"' && raw[len(raw)-1] == '"') && raw[1:len(raw)-1] == name {
		span.Start++
		span.End--
		return name, span, true
	}
	return "", span, false
}
func (b *localRenameBindings) declare(owner int, kind string, n yamlview.Node) {
	b.resumeQuery()
	name, span, ok := exactName(n)
	if !ok || sharedOrigin(b.shared, span) {
		b.err = fmt.Errorf("declaration is not an exact static name")
		return
	}
	key := renameKey{owner, kind, name}
	if b.readOnly {
		b.declarations[key] = append(b.declarations[key], renameOccurrence{span, -1})
		b.queryReference(key, span)
	}
	if _, exists := b.symbols[key]; exists {
		b.err = fmt.Errorf("duplicate local symbol")
		return
	}
	b.symbols[key] = nil
	b.add(key, renameOccurrence{span, -1})
}
func (b *localRenameBindings) reference(owner int, kind string, n yamlview.Node, optional bool) {
	b.resumeQuery()
	if n.Kind() == yamlview.Empty {
		return
	}
	name, span, ok := exactName(n)
	key := renameKey{owner, kind, name}
	if !ok {
		b.err = fmt.Errorf("reference is not an exact static name")
		return
	}
	b.queryReference(key, span)
	if b.readOnly && (len(b.declarations[key]) != 1 || b.declarationCount[key] != 1) {
		return
	}
	if _, exists := b.symbols[key]; !exists {
		if !optional {
			b.err = fmt.Errorf("unknown local reference")
		}
		return
	}
	b.add(key, renameOccurrence{span, -1})
}
func (b *localRenameBindings) callee(n yamlview.Node) int {
	b.resumeQuery()
	name := stringValue(n)
	if b.readOnly {
		b.reference(-1, "template", n, true)
		if len(b.declarations[renameKey{-1, "template", name}]) != 1 || b.declarationCount[renameKey{-1, "template", name}] != 1 {
			return -1
		}
	}
	if _, ok := b.symbols[renameKey{-1, "template", name}]; !ok {
		b.err = fmt.Errorf("callee is not a unique local template")
		return -1
	}
	b.reference(-1, "template", n, false)
	for i, t := range b.templates {
		if stringValue(member(t, "name")) == name {
			return i
		}
	}
	return -1
}
func (b *localRenameBindings) call(n yamlview.Node, field string, args yamlview.Node) int {
	b.resumeQuery()
	target := member(n, field)
	if target.Kind() == yamlview.Empty {
		return -1
	}
	callee := b.callee(target)
	if callee < 0 {
		return callee
	}
	for _, kind := range []string{"parameters", "artifacts"} {
		for _, arg := range member(args, kind).Elements() {
			b.reference(callee, "inputs."+kind, member(arg, "name"), true)
		}
	}
	return callee
}

// The raw scalar body is used only when member tokens retain their source bytes.
// YAML escapes/folding that could alter those tokens are deliberately refused.
func renameText(n yamlview.Node) (string, int, bool) {
	raw := n.Raw()
	value := stringValue(n)
	if n.Kind() != yamlview.Scalar {
		return "", 0, false
	}
	if raw == value {
		return raw, n.Span().Start, true
	}
	if len(raw) >= 2 && (raw[0] == '"' || raw[0] == '\'') && raw[1:len(raw)-1] == value {
		return value, n.Span().Start + 1, true
	}
	if strings.HasPrefix(raw, "|") {
		if at := strings.IndexByte(raw, '\n'); at >= 0 {
			return raw[at+1:], n.Span().Start + at + 1, true
		}
	}
	return "", 0, false
}
func (b *localRenameBindings) block(owner int, prefix string) {
	for key := range b.symbols {
		if key.owner == owner && strings.HasPrefix(key.kind, prefix) {
			b.blocked[key] = true
		}
	}
}
func (b *localRenameBindings) blockRoot(owner int, root string) {
	switch root {
	case "workflow":
		b.block(-1, "workflow.parameters")
	case "inputs", "outputs":
		b.block(owner, root+".")
	case "tasks", "steps":
		kind := "task"
		if root == "steps" {
			kind = "step"
		}
		b.block(owner, kind)
		for key, callee := range b.calls {
			if key.owner == owner && key.kind == kind {
				b.block(callee, "outputs.")
			}
		}
	}
}
func (b *localRenameBindings) chain(owner int, ref memberReference, base int, expression bool, visible map[renameKey]bool) {
	b.resumeQuery()
	p := ref.parts
	if len(p) == 0 {
		return
	}
	// $env bypasses local let bindings. Indirect access does not prove exact
	// symbol locations, but its static namespace still limits affected symbols.
	if p[0] == "$env" {
		roots := []string{"inputs", "outputs", "workflow", "tasks", "steps"}
		if len(p) > 1 && p[1] != "*" {
			roots = []string{p[1]}
		}
		for _, root := range roots {
			b.blockRoot(owner, root)
		}
		return
	}
	switch p[0] {
	case "inputs", "outputs", "workflow", "tasks", "steps":
	default:
		return
	}
	bind := func(key renameKey, index int) {
		span := ref.spans[index]
		span.Start += base
		span.End += base
		if raw, _ := b.snapshot.Slice(span); raw != p[index] && b.readOnly {
			return
		}
		b.queryReference(key, span)
		if b.readOnly && (len(b.declarations[key]) != 1 || b.declarationCount[key] != 1) {
			return
		}
		if _, ok := b.symbols[key]; !ok {
			b.err = fmt.Errorf("unknown local member")
			return
		}
		if raw, _ := b.snapshot.Slice(span); raw != p[index] {
			b.err = fmt.Errorf("encoded member is not an exact location")
			return
		}
		dot := -1
		if expression && ref.dots[index] >= 0 {
			dot = base + ref.dots[index]
			if !expressionName.MatchString(p[index]) {
				b.err = fmt.Errorf("non-identifier expression members require indexing")
				return
			}
		}
		b.add(key, renameOccurrence{span, dot})
	}
	switch p[0] {
	case "workflow":
		if len(p) < 2 || p[1] == "*" {
			b.blockRoot(owner, "workflow")
			return
		}
		if p[1] == "parameters" {
			if len(p) < 3 || p[2] == "*" || p[2] == "json" && !b.readOnly {
				b.block(-1, "workflow.parameters")
				return
			}
			// A read-only occurrence is not a declaration. Complete caller/base
			// sources decide whether a real named json identity exists.
			bind(renameKey{-1, "workflow.parameters", p[2]}, 2)
		}
		if p[1] == "outputs" {
			b.err = fmt.Errorf("global output access is not a local binding")
		}
	case "inputs", "outputs":
		if p[0] == "outputs" && len(p) == 2 && p[1] == "result" {
			return
		}
		if len(p) < 2 || p[1] == "*" {
			b.blockRoot(owner, p[0])
			return
		}
		if p[1] != "parameters" && p[1] != "artifacts" {
			b.err = fmt.Errorf("unknown input/output member")
			return
		}
		if len(p) < 3 || p[2] == "*" {
			b.block(owner, p[0]+"."+p[1])
			return
		}
		bind(renameKey{owner, p[0] + "." + p[1], p[2]}, 2)
		if len(p) > 3 && p[3] != "*" && !(len(p) == 4 && p[3] == "path" && (p[1] == "artifacts" || p[0] == "outputs")) {
			b.err = fmt.Errorf("unknown local value member")
		}
	case "tasks", "steps":
		if len(p) == 2 && p[1] == "name" {
			return
		}
		if len(p) < 2 || p[1] == "*" {
			b.blockRoot(owner, p[0])
			return
		}
		kind := "task"
		if p[0] == "steps" {
			kind = "step"
		}
		key := renameKey{owner, kind, p[1]}
		if b.readOnly && len(ref.spans) > 1 {
			span := ref.spans[1]
			span.Start += base
			span.End += base
			if raw, _ := b.snapshot.Slice(span); raw == p[1] {
				b.queryReference(key, span)
				b.visibility[span] = map[renameKey]bool{}
				for candidate, allowed := range visible {
					b.visibility[span][candidate] = allowed
				}
			}
		}
		if !visible[key] {
			b.err = fmt.Errorf("task/step is not visible in this context")
			return
		}
		bind(key, 1)
		if b.readOnly && (len(b.declarations[key]) != 1 || b.declarationCount[key] != 1) {
			return
		}
		callee, ok := b.calls[key]
		if len(p) < 3 || p[2] == "*" {
			if ok {
				b.block(callee, "outputs.")
			}
			return
		}
		if p[2] == "outputs" {
			if !ok || callee < 0 {
				if b.readOnly && len(p) >= 5 && (p[3] == "parameters" || p[3] == "artifacts") && p[4] != "*" {
					span := ref.spans[4]
					span.Start += base
					span.End += base
					if raw, _ := b.snapshot.Slice(span); raw == p[4] {
						b.externalOutputs = append(b.externalOutputs, queryOutputReference{key, p[3], p[4], span})
					}
				}
				b.err = fmt.Errorf("unknown output producer")
				return
			}
			if len(p) < 4 || p[3] == "*" {
				b.block(callee, "outputs.")
				return
			}
			if p[3] == "result" && len(p) == 4 {
				return
			}
			if p[3] != "parameters" && p[3] != "artifacts" {
				b.err = fmt.Errorf("unknown output member")
				return
			}
			if len(p) < 5 || p[4] == "*" {
				b.block(callee, "outputs."+p[3])
				return
			}
			bind(renameKey{callee, "outputs." + p[3], p[4]}, 4)
			if len(p) > 5 && p[5] != "*" {
				b.err = fmt.Errorf("unknown task output value member")
			}
		} else {
			switch p[2] {
			case "id", "ip", "status", "exitCode", "startedAt", "finishedAt", "hostNodeName":
				if len(p) != 3 {
					b.err = fmt.Errorf("unknown task value member")
				}
			default:
				b.err = fmt.Errorf("unknown task/step member")
			}
		}
	}
}
func (b *localRenameBindings) scalar(owner int, n yamlview.Node, expression bool, visible map[renameKey]bool) {
	b.resumeQuery()
	raw := n.Raw()
	if !expression && !strings.Contains(raw, "{{") {
		return
	}
	text, base, ok := renameText(n)
	if !ok {
		b.err = fmt.Errorf("reference has no exact source mapping")
		return
	}
	parse := func(text string, start int, expr bool) {
		var refs []memberReference
		if expr {
			var err error
			refs, err = expressionReferences(b.ctx, text)
			if err != nil {
				b.err = fmt.Errorf("invalid expression: %w", err)
				return
			}
		} else {
			refs = memberReferences(text)
		}
		if !expr && len(refs) > 0 {
			first := refs[0].parts
			relevant := first[0] == "inputs" || first[0] == "outputs" || first[0] == "tasks" || first[0] == "steps" || (first[0] == "workflow" && len(first) > 1 && first[1] == "parameters")
			if relevant && (len(refs) != 1 || strings.Join(first, ".") != strings.TrimSpace(text)) {
				b.err = fmt.Errorf("simple tags require a static dotted variable")
				return
			}
		}
		for _, ref := range refs {
			b.chain(owner, ref, base+start, expr, visible)
		}
	}
	if expression && !strings.Contains(text, "{{") {
		parse(text, 0, true)
		return
	}
	for offset := 0; offset < len(text); {
		a := strings.Index(text[offset:], "{{")
		if a < 0 {
			break
		}
		a += offset + 2
		end := strings.Index(text[a:], "}}")
		if end < 0 {
			b.err = fmt.Errorf("unfinished template tag")
			return
		}
		end += a
		expr := a < len(text) && text[a] == '='
		if expr {
			a++
		}
		parse(text[a:end], a, expr)
		offset = end + 2
	}
}
func (b *localRenameBindings) scan(owner int, n yamlview.Node, visible map[renameKey]bool, expression bool) {
	b.resumeQuery()
	if err := b.ctx.Err(); err != nil {
		b.err = err
		return
	}
	if b.err != nil {
		return
	}
	if n.Kind() == yamlview.Scalar {
		b.scalar(owner, n, expression, visible)
		return
	}
	for _, m := range n.Members() {
		b.scalar(owner, m.Key, false, visible)
		key := stringValue(m.Key)
		if b.readOnly && key == "inline" {
			continue
		}
		if (key == "from" || key == "parameter") && !strings.Contains(m.Value.Raw(), "{{") {
			text, base, ok := renameText(m.Value)
			if !ok {
				b.err = fmt.Errorf("raw variable has no exact location")
				continue
			}
			trimmed := strings.TrimLeft(text, " \t\r\n")
			base += len(text) - len(trimmed)
			text = trimmed
			expr := strings.HasPrefix(text, "=")
			if expr {
				text = text[1:]
				base++
			}
			var refs []memberReference
			if expr {
				var err error
				refs, err = expressionReferences(b.ctx, text)
				if err != nil {
					b.err = fmt.Errorf("invalid raw expression: %w", err)
					continue
				}
			} else {
				refs = memberReferences(text)
			}
			if !expr && (len(refs) != 1 || strings.Join(refs[0].parts, ".") != strings.TrimSpace(text)) {
				b.err = fmt.Errorf("raw variable is not a static dotted key")
				continue
			}
			for _, ref := range refs {
				b.chain(owner, ref, base, expr, visible)
			}
		} else {
			b.scan(owner, m.Value, visible, key == "expression" || key == "fromExpression")
		}
	}
	for _, v := range n.Elements() {
		b.scan(owner, v, visible, false)
	}
}
func (b *localRenameBindings) taskNames(owner int, n yamlview.Node, depends bool) {
	b.resumeQuery()
	if n.Kind() == yamlview.Empty {
		return
	}
	text, base, ok := renameText(n)
	if !ok {
		b.err = fmt.Errorf("task selection is not exact")
		return
	}
	var occurrences []dependsOccurrence
	if depends {
		var err error
		occurrences, err = parseDepends(text)
		if err != nil {
			b.err = err
			return
		}
	} else {
		for i := 0; i < len(text); {
			if !identifierByte(text[i]) {
				i++
				continue
			}
			start := i
			for i < len(text) && identifierByte(text[i]) {
				i++
			}
			if start > 0 && text[start-1] == '.' {
				continue
			}
			occurrences = append(occurrences, dependsOccurrence{name: text[start:i], span: source.Span{Start: start, End: i}})
		}
	}
	for _, occurrence := range occurrences {
		key := renameKey{owner, "task", occurrence.name}
		span := source.Span{Start: base + occurrence.span.Start, End: base + occurrence.span.End}
		b.queryReference(key, span)
		if b.readOnly && (len(b.declarations[key]) != 1 || b.declarationCount[key] != 1) {
			continue
		}
		if _, ok := b.symbols[key]; !ok {
			b.err = fmt.Errorf("unknown DAG target or dependency")
			return
		}
		b.add(key, renameOccurrence{span, -1})
	}
}

func localBindings(ctx context.Context, s source.Snapshot, version string, selected int) (*localRenameBindings, error) {
	return collectLocalBindings(ctx, s, version, selected, false)
}
func collectLocalBindings(ctx context.Context, s source.Snapshot, version string, selected int, readOnly bool) (*localRenameBindings, error) {
	b := &localRenameBindings{ctx: ctx, symbols: map[renameKey][]renameOccurrence{}, positions: map[source.Span]renameKey{}, calls: map[renameKey]int{}, snapshot: s, blocked: map[renameKey]bool{}, readOnly: readOnly, queryPositions: map[source.Span]map[renameKey]bool{}, queryAmbiguous: map[source.Span]map[renameKey]bool{}, declarationCount: map[renameKey]int{}, declarations: map[renameKey][]renameOccurrence{}, visibility: map[source.Span]map[renameKey]bool{}}
	view, err := parseView(ctx, s)
	if err != nil {
		return b, err
	}
	var doc yamlview.Document
	found := false
	for _, v := range view.Documents() {
		if selected >= v.Span().Start && selected < v.Span().End {
			doc = v
			found = true
			break
		}
	}
	if !found || !doc.Complete() {
		return b, fmt.Errorf("resource is incomplete")
	}
	root := doc.Root()
	projected, err := project(ctx, s, doc, version)
	if err != nil {
		return b, err
	}
	kind, _ := queryMember(projected, "kind").(string)
	api, _ := queryMember(projected, "apiVersion").(string)
	if !owned(api, kind) || !readOnly && kind != "Workflow" && kind != "CronWorkflow" {
		return b, fmt.Errorf("only closed Workflow and CronWorkflow symbols can be renamed")
	}
	if readOnly {
		namespace, _ := queryMember(queryMember(projected, "metadata"), "namespace").(string)
		if namespace == "" {
			namespace = "unspecified"
		}
		b.queryOrigin = kind + " · document namespace: " + namespace + " · source: " + string(s.URI())
	}
	if readOnly {
		b.declarationCount = semanticDeclarations(projected, kind)
		b.shared = sharedOrigins(ctx, s, doc, version)
	}
	var closed func(yamlview.Node, []string) bool
	closed = func(n yamlview.Node, path []string) bool {
		if n.Kind() == yamlview.Alias || n.Damaged() {
			return false
		}
		for _, m := range n.Members() {
			key := stringValue(m.Key)
			part := key
			if len(path) > 0 && path[len(path)-1] == "hooks" {
				part = "*"
			}
			next := append(append([]string(nil), path...), part)
			if key == "<<" {
				return false
			}
			if renameControlPath(next) {
				if key != "templateRef" {
					return false
				}
				// A static external call addresses a WorkflowTemplate identity, never a
				// template declared by this private Workflow/CronWorkflow resource.
				for _, field := range []string{"name", "template"} {
					value, _, exact := exactName(member(m.Value, field))
					if !exact || strings.Contains(value, "{{") {
						return false
					}
				}
				b.staticExternalCalls = true
			}
			if !closed(m.Value, next) {
				return false
			}
		}
		for _, v := range n.Elements() {
			if !closed(v, append(append([]string(nil), path...), "*")) {
				return false
			}
		}
		return true
	}
	if !readOnly && !closed(root, nil) {
		return b, fmt.Errorf("derived, inherited, external or exported bindings prevent local rename")
	}
	effectiveRoot := workflowRoot(projected, root)
	effectiveRoot.shared = b.shared
	spec := workflowMember(effectiveRoot, "spec")
	if kind == "CronWorkflow" {
		spec = workflowMember(spec, "workflowSpec")
	}
	templates := workflowMember(spec, "templates").Elements()
	for _, template := range templates {
		b.templates = append(b.templates, template.node)
	}
	for _, p := range workflowMember(workflowMember(spec, "arguments"), "parameters").Elements() {
		b.declare(-1, "workflow.parameters", queryNode(workflowMember(p, "name")))
	}
	for i, t := range templates {
		b.declare(-1, "template", queryNode(workflowMember(t, "name")))
		for _, section := range []string{"inputs", "outputs"} {
			for _, kind := range []string{"parameters", "artifacts"} {
				for _, p := range workflowMember(workflowMember(t, section), kind).Elements() {
					b.declare(i, section+"."+kind, queryNode(workflowMember(p, "name")))
				}
			}
		}
		for _, task := range workflowMember(workflowMember(t, "dag"), "tasks").Elements() {
			b.declare(i, "task", queryNode(workflowMember(task, "name")))
		}
		for _, group := range workflowMember(t, "steps").Elements() {
			for _, step := range group.Elements() {
				b.declare(i, "step", queryNode(workflowMember(step, "name")))
			}
		}
	}
	if !readOnly || workflowMember(spec, "workflowTemplateRef").Kind() == yamlview.Empty {
		b.callValue(spec, "entrypoint", workflowMember(spec, "arguments"), templates)
	}
	if !readOnly || workflowMember(spec, "workflowTemplateRef").Kind() == yamlview.Empty || !workflowVersionAtLeast(version, 4, 6) {
		b.callValue(spec, "onExit", workflowMember(spec, "arguments"), templates)
	}
	b.hooksValue(spec, templates)
	for i, t := range templates {
		b.callValue(t, "onExit", workflowValue{}, templates)
		b.hooksValue(t, templates)
		for _, task := range workflowMember(workflowMember(t, "dag"), "tasks").Elements() {
			key := renameKey{i, "task", workflowString(workflowMember(task, "name"))}
			b.calls[key] = b.callValue(task, "template", workflowMember(task, "arguments"), templates)
			b.callValue(task, "onExit", workflowValue{}, templates)
			b.hooksValue(task, templates)
		}
		for _, group := range workflowMember(t, "steps").Elements() {
			for _, step := range group.Elements() {
				key := renameKey{i, "step", workflowString(workflowMember(step, "name"))}
				b.calls[key] = b.callValue(step, "template", workflowMember(step, "arguments"), templates)
				b.callValue(step, "onExit", workflowValue{}, templates)
				b.hooksValue(step, templates)
			}
		}
	}
	for i, t := range templates {
		all := map[renameKey]bool{}
		edges := map[string][]string{}
		tasks := workflowMember(workflowMember(t, "dag"), "tasks").Elements()
		b.taskNames(i, queryNode(workflowMember(workflowMember(t, "dag"), "target")), false)
		for _, task := range tasks {
			name := workflowString(workflowMember(task, "name"))
			all[renameKey{i, "task", name}] = true
			for _, dep := range workflowMember(task, "dependencies").Elements() {
				b.reference(i, "task", queryNode(dep), false)
				edges[name] = append(edges[name], workflowString(dep))
			}
			depends := workflowMember(task, "depends")
			if depends.Kind() != yamlview.Empty {
				b.taskNames(i, queryNode(depends), true)
				occurrences, _ := parseDepends(workflowString(depends))
				for _, occurrence := range occurrences {
					edges[name] = append(edges[name], occurrence.name)
				}
			}
		}
		for _, task := range tasks {
			visible := map[renameKey]bool{}
			var ancestors func(string)
			ancestors = func(name string) {
				for _, dep := range edges[name] {
					key := renameKey{i, "task", dep}
					if !visible[key] {
						visible[key] = true
						ancestors(dep)
					}
				}
			}
			ancestors(workflowString(workflowMember(task, "name")))
			for _, m := range task.Members() {
				if readOnly && m.Name == "inline" {
					continue
				}
				if m.Name == "hooks" {
					hookVisible := map[renameKey]bool{}
					for k, v := range visible {
						hookVisible[k] = v
					}
					hookVisible[renameKey{i, "task", workflowString(workflowMember(task, "name"))}] = true
					b.scanValue(i, m.Value, hookVisible, false)
				} else {
					b.scanValue(i, m.Value, visible, false)
				}
			}
		}
		previous := map[renameKey]bool{}
		for _, group := range workflowMember(t, "steps").Elements() {
			for _, step := range group.Elements() {
				for _, m := range step.Members() {
					if readOnly && m.Name == "inline" {
						continue
					}
					if m.Name == "hooks" {
						hookVisible := map[renameKey]bool{}
						for key, value := range previous {
							hookVisible[key] = value
						}
						hookVisible[renameKey{i, "step", workflowString(workflowMember(step, "name"))}] = true
						b.scanValue(i, m.Value, hookVisible, false)
					} else {
						b.scanValue(i, m.Value, previous, false)
					}
				}
			}
			for _, step := range group.Elements() {
				key := renameKey{i, "step", workflowString(workflowMember(step, "name"))}
				previous[key] = true
				all[key] = true
			}
		}
		for _, m := range t.Members() {
			key := m.Name
			if key == "dag" || key == "steps" {
				continue
			}
			visible := map[renameKey]bool{}
			if key == "outputs" {
				visible = all
			}
			b.scanValue(i, m.Value, visible, false)
		}
	}
	for _, m := range spec.Members() {
		if m.Name != "templates" {
			b.scanValue(-1, m.Value, nil, false)
		}
	}
	for _, m := range effectiveRoot.Members() {
		if m.Name != "spec" {
			b.scanValue(-1, m.Value, nil, false)
		}
	}
	if readOnly {
		return b, ctx.Err()
	}
	return b, b.err
}
func renameSelection(ctx context.Context, s source.Snapshot, version string, pos source.Position) (*localRenameBindings, renameKey, renameOccurrence, error) {
	at, err := s.Offset(pos)
	if err != nil {
		return nil, renameKey{}, renameOccurrence{}, err
	}
	b, err := localBindings(ctx, s, version, at)
	if err != nil {
		return b, renameKey{}, renameOccurrence{}, err
	}
	for key, occurrences := range b.symbols {
		for _, o := range occurrences {
			if at >= o.span.Start && at < o.span.End {
				if b.staticExternalCalls && key.kind != "template" {
					return b, key, o, fmt.Errorf("external call arguments and outputs require a cross-resource binding proof; only independent local template names can be renamed")
				}
				if b.blocked[key] {
					return b, key, o, fmt.Errorf("indirect or dynamic access prevents proving all references to %q", key.name)
				}
				return b, key, o, nil
			}
		}
	}
	return b, renameKey{}, renameOccurrence{}, fmt.Errorf("position has no unique local symbol")
}
func PrepareRename(ctx context.Context, s source.Snapshot, version string, pos source.Position) (any, error) {
	_, key, o, err := renameSelection(ctx, s, version, pos)
	if err != nil {
		return nil, err
	}
	a, _ := s.Position(o.span.Start)
	z, _ := s.Position(o.span.End)
	return map[string]any{"range": Range{Position{a.Line, a.UTF16Column}, Position{z.Line, z.UTF16Column}}, "placeholder": key.name}, nil
}

var parameterName = regexp.MustCompile(`^[-a-zA-Z0-9_]+$`)
var workflowFieldName = regexp.MustCompile(`^[a-zA-Z0-9][-a-zA-Z0-9]*$`)
var expressionName = regexp.MustCompile(`^[a-zA-Z_][a-zA-Z0-9_]*$`)

func RenameLocal(ctx context.Context, s source.Snapshot, version string, pos source.Position, newName string) (edit.Plan, error) {
	b, key, _, err := renameSelection(ctx, s, version, pos)
	if err != nil {
		return edit.Plan{}, err
	}
	valid := parameterName.MatchString(newName)
	if key.kind == "template" || key.kind == "task" || key.kind == "step" {
		valid = len(newName) <= 128 && workflowFieldName.MatchString(newName)
	}
	if key.kind == "task" && newName != "" && newName[0] >= '0' && newName[0] <= '9' {
		valid = false
	}
	if !valid || newName == key.name {
		return edit.Plan{}, fmt.Errorf("invalid or unchanged local symbol name")
	}
	nextKey := key
	nextKey.name = newName
	if _, exists := b.symbols[nextKey]; exists {
		return edit.Plan{}, fmt.Errorf("new name conflicts with a local symbol")
	}
	plan := edit.Plan{BaseRevision: s.Revision()}
	for _, o := range b.symbols[key] {
		span := o.span
		value := newName
		if o.dot >= 0 && !expressionName.MatchString(newName) {
			if o.dot+1 != span.Start {
				return edit.Plan{}, fmt.Errorf("member conversion would change intervening syntax")
			}
			span.Start = o.dot
			value = "['" + newName + "']"
		}
		old, _ := s.Slice(span)
		plan.Replacements = append(plan.Replacements, edit.Replacement{Span: span, ExpectedText: old, NewText: value})
	}
	sort.Slice(plan.Replacements, func(i, j int) bool { return plan.Replacements[i].Span.Start < plan.Replacements[j].Span.Start })
	next, err := edit.Apply(s, s.Revision()+1, plan)
	if err != nil {
		return edit.Plan{}, err
	}
	// The resource begins before every symbol edit, so its start remains stable.
	selected := plan.Replacements[0].Span.Start
	after, err := localBindings(ctx, next, version, selected)
	if err != nil {
		return edit.Plan{}, err
	}
	if len(after.symbols[nextKey]) != len(b.symbols[key]) {
		return edit.Plan{}, fmt.Errorf("rename changed the proven reference set")
	}
	return plan, nil
}

// queryNode exposes only an independently located source value. Projection
// hosts never stand in for a variable token.
func queryNode(value workflowValue) yamlview.Node {
	if value.origin != "exact" {
		return yamlview.Node{}
	}
	return value.node
}

func (b *localRenameBindings) callValue(n workflowValue, field string, args workflowValue, templates []workflowValue) int {
	if !b.readOnly {
		return b.call(n.node, field, args.node)
	}
	target := workflowMember(n, field)
	name := workflowString(target)
	if !target.present || name == "" {
		return -1
	}
	b.reference(-1, "template", queryNode(target), true)
	candidates := []int{}
	for i, template := range templates {
		if workflowString(workflowMember(template, "name")) == name {
			candidates = append(candidates, i)
		}
	}
	for _, kind := range []string{"parameters", "artifacts"} {
		for _, argument := range workflowMember(args, kind).Elements() {
			name, span, exact := exactName(queryNode(workflowMember(argument, "name")))
			if !exact {
				continue
			}
			// An unfinished name has a completion namespace, not a bound input role.
			// Keep only a unique callee context, separate from navigation identities.
			if len(candidates) == 1 && !sharedOrigin(b.shared, span) {
				key := renameKey{candidates[0], "inputs." + kind, name}
				if b.declarationCount[key] == 0 {
					if b.argumentContexts == nil {
						b.argumentContexts = map[source.Span]map[renameKey]bool{}
					}
					if b.argumentContexts[span] == nil {
						b.argumentContexts[span] = map[renameKey]bool{}
					}
					key.name = ""
					b.argumentContexts[span][key] = true
				}
			}
			for _, owner := range candidates {
				key := renameKey{owner, "inputs." + kind, name}
				if b.declarationCount[key] > 0 {
					if len(candidates) != 1 {
						if b.queryAmbiguous[span] == nil {
							b.queryAmbiguous[span] = map[renameKey]bool{}
						}
						b.queryAmbiguous[span][key] = true
					}
					b.queryReference(key, span)
				}
			}
		}
	}
	if len(candidates) != 1 {
		return -1
	}
	return candidates[0]
}
func (b *localRenameBindings) hooksValue(n workflowValue, templates []workflowValue) {
	for _, hook := range workflowMember(n, "hooks").Members() {
		b.callValue(hook.Value, "template", workflowMember(hook.Value, "arguments"), templates)
	}
}
func (b *localRenameBindings) scanValue(owner int, n workflowValue, visible map[renameKey]bool, expression bool) {
	if !b.readOnly {
		b.scan(owner, n.node, visible, expression)
		return
	}
	b.resumeQuery()
	if err := b.ctx.Err(); err != nil {
		b.err = err
		return
	}
	if n.Kind() == yamlview.Scalar {
		if node := queryNode(n); node.Kind() != yamlview.Empty {
			b.scalar(owner, node, expression, visible)
		}
		return
	}
	// Only direct keys of an effective, independently located mapping carry
	// source tokens. Never walk raw merge values to discover their keys.
	if n.origin == "exact" {
		if values, ok := n.value.(map[string]any); ok {
			for _, member := range n.node.Members() {
				if _, exists := values[stringValue(member.Key)]; exists {
					b.scalar(owner, member.Key, false, visible)
				}
			}
		}
	}
	for _, member := range n.Members() {
		key, child := member.Name, member.Value
		if key == "inline" {
			continue
		}
		if (key == "from" || key == "parameter") && !strings.Contains(workflowString(child), "{{") {
			node := queryNode(child)
			text, base, ok := renameText(node)
			if !ok {
				continue
			}
			trimmed := strings.TrimLeft(text, " \t\r\n")
			base += len(text) - len(trimmed)
			text = trimmed
			expr := strings.HasPrefix(text, "=")
			if expr {
				text = text[1:]
				base++
			}
			var refs []memberReference
			if expr {
				var err error
				refs, err = expressionReferences(b.ctx, text)
				if err != nil {
					continue
				}
			} else {
				refs = memberReferences(text)
			}
			if !expr && (len(refs) != 1 || strings.Join(refs[0].parts, ".") != strings.TrimSpace(text)) {
				continue
			}
			for _, ref := range refs {
				b.chain(owner, ref, base, expr, visible)
			}
		} else {
			b.scanValue(owner, child, visible, key == "expression" || key == "fromExpression")
		}
	}
	for _, value := range n.Elements() {
		b.scanValue(owner, value, visible, false)
	}
}

// Only Argo control fields can introduce external or exported identities.
// Ordinary data maps (including labels and annotations) do not bind symbols.
func renameControlPath(path []string) bool {
	p := strings.Join(path, "/")
	p = strings.TrimPrefix(p, "spec/workflowSpec/")
	p = strings.TrimPrefix(p, "spec/")
	if p == "workflowTemplateRef" {
		return true
	}
	for _, prefix := range []string{"hooks/*/", "templates/*/hooks/*/", "templates/*/dag/tasks/*/", "templates/*/steps/*/*/", "templates/*/dag/tasks/*/hooks/*/", "templates/*/steps/*/*/hooks/*/"} {
		if p == prefix+"inline" || p == prefix+"templateRef" {
			return true
		}
	}
	return p == "templates/*/outputs/parameters/*/globalName" || p == "templates/*/outputs/artifacts/*/globalName"
}
