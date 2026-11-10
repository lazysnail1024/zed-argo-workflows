package analysis

import (
	"context"
	"fmt"
	"strings"

	"github.com/lazysnail1024/k8s-crd-toolkit/source"
	"github.com/lazysnail1024/k8s-crd-toolkit/yamlview"
)

// A declaration identity includes its semantic sequence position, even when its
// source is encoded or derived. Exact locations are only a presentation filter.
type workflowParameterID struct {
	uri             source.URI
	resource, index int
}
type workflowParameterSource struct {
	id       workflowParameterID
	location Location
	exact    bool
}

func workflowParameterSources(r resource, name string) []workflowParameterSource {
	result := []workflowParameterSource{}
	for i, parameter := range workflowMember(workflowMember(r.semanticSpec, "arguments"), "parameters").Elements() {
		value := workflowMember(parameter, "name")
		if workflowString(value) != name {
			continue
		}
		_, span, exact := exactName(queryNode(value))
		exact = exact && !sharedOrigin(r.shared, span)
		result = append(result, workflowParameterSource{workflowParameterID{r.document.Snapshot.URI(), r.root.Span().Start, i}, spanLocation(r.document.Snapshot, span), exact})
	}
	return result
}
func effectiveWorkflowParameterSources(caller, callee resource, name string) []workflowParameterSource {
	if own := workflowParameterSources(caller, name); len(own) != 0 {
		return own
	}
	return workflowParameterSources(callee, name)
}

type inheritedParameterUse struct {
	sources    map[workflowParameterID]workflowParameterSource
	incomplete bool
}

// One requested parameter name is resolved across existing resource facts. This
// never expands every call into every parameter or copies a callee template.
type inheritedParameterQuery struct {
	name             string
	uses             map[Location]*inheritedParameterUse
	localDefinitions map[Location]bool
}

// A WorkflowTemplate body can inherit a workflow parameter from any caller.
// Its definition query therefore needs the reverse dependency closure too;
// ordinary template/name navigation only needs outgoing resource identities.
func DefinitionNeedsCallers(ctx context.Context, snapshot source.Snapshot, version string, position source.Position) bool {
	offset, err := snapshot.Offset(position)
	if err != nil {
		return false
	}
	bindings, _ := collectLocalBindings(ctx, snapshot, version, offset, true)
	if bindings == nil {
		return false
	}
	for span, keys := range bindings.queryPositions {
		if offset < span.Start || offset > span.End {
			continue
		}
		for key := range keys {
			if key.kind == "workflow.parameters" {
				return true
			}
		}
	}
	return false
}

func (p *inheritedParameterQuery) add(loc Location, sources []workflowParameterSource, incomplete bool) {
	use := p.uses[loc]
	if use == nil {
		use = &inheritedParameterUse{sources: map[workflowParameterID]workflowParameterSource{}}
		p.uses[loc] = use
	}
	for _, declaration := range sources {
		use.sources[declaration.id] = declaration
	}
	use.incomplete = use.incomplete || incomplete
}
func parameterOccurrences(r resource, b *localRenameBindings, name string, bodyTemplates []workflowValue) []Location {
	result := []Location{}
	if b == nil {
		return result
	}
	key := renameKey{-1, "workflow.parameters", name}
	for span, keys := range b.queryPositions {
		if !keys[key] {
			continue
		}
		declaration := false
		for _, d := range b.declarations[key] {
			if d.span == span {
				declaration = true
			}
		}
		if declaration {
			continue
		}
		if bodyTemplates != nil {
			inside := false
			for _, template := range bodyTemplates {
				host := template.Span()
				if span.Start >= host.Start && span.End <= host.End {
					inside = true
					break
				}
			}
			if !inside {
				continue
			}
		}
		result = append(result, spanLocation(r.document.Snapshot, span))
	}
	return result
}
func (p *inheritedParameterQuery) resolve(ctx context.Context, q *externalQuery, caller resource, candidates []resource, complete bool, localFor func(resource) *localRenameBindings) {
	own := workflowParameterSources(caller, p.name)
	for _, loc := range parameterOccurrences(caller, localFor(caller), p.name, nil) {
		if q.definitions && !containsPosition(loc, q.uri, q.position) {
			continue
		}
		if len(own) > 0 {
			p.add(loc, own, false)
			continue
		}
		if len(candidates) == 0 {
			p.add(loc, nil, true)
		}
		for _, callee := range candidates {
			sources := workflowParameterSources(callee, p.name)
			p.add(loc, sources, !complete || len(candidates) != 1 || len(sources) == 0 || workflowMember(callee.semanticSpec, "workflowTemplateRef").present)
		}
	}
	for _, callee := range candidates {
		for _, loc := range parameterOccurrences(callee, localFor(callee), p.name, inheritedValidationTemplates(ctx, caller, callee)) {
			if q.definitions && !containsPosition(loc, q.uri, q.position) {
				continue
			}
			// Independent template validation is a real public source context, but
			// permitting an undeclared parameter does not invent a declaration.
			p.add(loc, workflowParameterSources(callee, p.name), false)
			sources := effectiveWorkflowParameterSources(caller, callee, p.name)
			p.add(loc, sources, !complete || len(candidates) != 1 || len(sources) == 0 || workflowMember(callee.semanticSpec, "workflowTemplateRef").present)
		}
	}
}
func (p *inheritedParameterQuery) finish(q *externalQuery, bindings []externalBinding) []externalBinding {
	for loc, use := range p.uses {
		if q.definitions {
			for _, declaration := range use.sources {
				if declaration.exact && !p.localDefinitions[declaration.location] {
					q.candidates = append(q.candidates, externalBinding{from: loc, to: declaration.location, selection: loc})
				}
			}
			continue
		}
		if len(use.sources) != 1 || use.incomplete {
			q.omittedParameterUses[loc] = true
			q.block(loc)
			continue
		}
		for _, declaration := range use.sources {
			if !declaration.exact {
				q.omittedParameterUses[loc] = true
				q.block(loc)
				continue
			}
			bindings = append(bindings, externalBinding{from: loc, to: declaration.location, selection: loc})
		}
	}
	return bindings
}

// Earlier validators visit the selected entry and static local child calls;
// later validators additionally visit every template in the referenced spec.
func inheritedValidationTemplates(ctx context.Context, caller, callee resource) []workflowValue {
	templates := workflowMember(callee.semanticSpec, "templates").Elements()
	if workflowVersionAtLeast(caller.document.Target.Version, 4, 6) {
		return templates
	}
	byName := map[string][]workflowValue{}
	for _, template := range templates {
		if ctx.Err() != nil {
			return nil
		}
		name := workflowString(workflowMember(template, "name"))
		byName[name] = append(byName[name], template)
	}
	selected := workflowString(workflowMember(caller.semanticSpec, "entrypoint"))
	if selected == "" {
		selected = workflowString(workflowMember(callee.semanticSpec, "entrypoint"))
	}
	result := []workflowValue{}
	visited := map[string]bool{}
	var visit func(string)
	visit = func(name string) {
		if ctx.Err() != nil || name == "" || strings.Contains(name, "{{") || visited[name] || len(byName[name]) != 1 {
			return
		}
		visited[name] = true
		template := byName[name][0]
		result = append(result, template)
		child := func(call workflowValue) {
			if !workflowMember(call, "templateRef").present {
				visit(workflowString(workflowMember(call, "template")))
			}
		}
		for _, task := range workflowMember(workflowMember(template, "dag"), "tasks").Elements() {
			child(task)
		}
		for _, group := range workflowMember(template, "steps").Elements() {
			for _, step := range group.Elements() {
				child(step)
			}
		}
	}
	visit(selected)
	return result
}

// Missing globals depend on the calling workflow. Keep this diagnostic at the
// actual reference, rather than publishing a caller-specific error on shared YAML.
func inheritedParameterDiagnostics(ctx context.Context, caller, callee resource, emit func(workflowValue, string, string, string)) {
	if caller.kind == "WorkflowTemplate" || caller.kind == "ClusterWorkflowTemplate" || workflowMember(callee.semanticSpec, "workflowTemplateRef").present {
		return
	}
	known := namedInputs(workflowMember(callee.semanticSpec, "arguments"), "parameters")
	for name := range namedInputs(workflowMember(caller.semanticSpec, "arguments"), "parameters") {
		known[name] = true
	}
	callerParameters, _ := workflowMember(workflowMember(caller.semanticSpec, "arguments"), "parameters").value.([]any)
	baseParameters, _ := workflowMember(workflowMember(callee.semanticSpec, "arguments"), "parameters").value.([]any)
	aggregateAvailable := len(callerParameters) > 0 || len(baseParameters) > 0
	ref := workflowMember(workflowMember(caller.semanticSpec, "workflowTemplateRef"), "name")
	reported := map[string]bool{}
	for _, template := range inheritedValidationTemplates(ctx, caller, callee) {
		templateName := workflowString(workflowMember(template, "name"))
		check := func(text string) {
			refs, err := scalarReferences(ctx, text)
			if err != nil {
				return
			}
			for _, reference := range refs {
				parts := reference.parts
				if reference.dynamic || len(parts) < 2 || parts[0] != "workflow" || parts[1] != "parameters" {
					continue
				}
				plainAggregate := len(parts) == 2
				if plainAggregate && aggregateAvailable || !plainAggregate && (parts[2] == "json" && workflowVersionAtLeast(caller.document.Target.Version, 4, 2) && aggregateAvailable || known[parts[2]]) {
					continue
				}
				name := "workflow.parameters"
				if !plainAggregate {
					name = parts[2]
				}
				key := templateName + "\x00" + name
				if reported[key] {
					continue
				}
				reported[key] = true
				if plainAggregate {
					emit(ref, "WF_REFERENCE_SCOPE", "Error", fmt.Sprintf("workflow parameters aggregate is not available for template %s in workflowTemplateRef %s", templateName, workflowString(ref)))
				} else {
					emit(ref, "WF_REFERENCE_SCOPE", "Error", fmt.Sprintf("workflow parameter %s is not declared for template %s in workflowTemplateRef %s", name, templateName, workflowString(ref)))
				}
			}
		}
		var scan func(workflowValue)
		scan = func(value workflowValue) {
			if ctx.Err() != nil {
				return
			}
			if value.Kind() == yamlview.Scalar {
				check(workflowString(value))
			}
			for _, member := range value.Members() {
				check(member.Name)
				scan(member.Value)
			}
			for _, child := range value.Elements() {
				scan(child)
			}
		}
		scan(template)
	}
}
