package analysis

import (
	"context"
	"github.com/lazysnail1024/k8s-crd-toolkit/source"
	"github.com/lazysnail1024/k8s-crd-toolkit/yamlview"
	"github.com/lazysnail1024/zed-argo-workflows/internal/workspace"
	"sort"
	"strings"
)

type resource struct {
	document              workspace.Document
	root, spec            yamlview.Node
	kind, name, namespace string
	declarations          map[renameKey]int
	templateValues        []any
	shared                map[source.Span]bool
	semanticSpec          workflowValue
}
type externalQuery struct {
	omittedParameterUses map[Location]bool
	candidates           []externalBinding
	definitions          bool
	references           bool
	position             source.Position
	blocked              []Location
	uri                  string
	offset               int
	names                map[string]int
	origins              map[string]string
	inexact              map[string]bool
}

// Only the selected source position can block a references request. All unique
// bindings are still collected for reverse references from declarations.
func (q *externalQuery) block(loc Location) {
	if !q.references || containsPosition(loc, q.uri, q.position) {
		q.blocked = append(q.blocked, loc)
	}
}

func (q *externalQuery) at(uri source.URI, span source.Span) bool {
	return q != nil && q.names != nil && q.uri == string(uri) && q.offset >= span.Start && q.offset < span.End
}

func externalOrigin(r resource) string {
	namespace := r.namespace
	if r.kind == "ClusterWorkflowTemplate" {
		namespace = "cluster"
	}
	if namespace == "" {
		namespace = "unknown"
	}
	return r.kind + " · namespace: " + namespace + " · source: " + string(r.document.Snapshot.URI())
}
func (q *externalQuery) suggest(name string, r resource, exact bool) {
	q.origins[name] = externalOrigin(r)
	q.inexact[name] = q.inexact[name] || !exact
}

type externalBinding struct{ from, to, selection Location }

func (b externalBinding) selected(uri string, pos source.Position) bool {
	return containsPosition(b.from, uri, pos) || containsPosition(b.selection, uri, pos)
}

// ExternalCompletion counts only resources whose external-reference walk finished.
type ExternalCompletion struct {
	Resources map[source.Span]bool
	Completed bool
}

func externalResources(ctx context.Context, w workspace.Workspace, readOnly bool) ([]resource, map[string]bool, map[source.URI]ExternalCompletion, map[source.URI]int) {
	progress := map[source.URI]ExternalCompletion{}
	remaining := map[source.URI]int{}
	resources := []resource{}
	complete := map[string]bool{}
	for _, d := range w.Documents {
		complete[d.Target.ID] = !w.IncompleteScopes[d.Target.ID]
	}
	for _, d := range w.Documents {
		if ctx.Err() != nil {
			return resources, complete, progress, remaining
		}
		view, err := parseView(ctx, d.Snapshot)
		if err != nil {
			complete[d.Target.ID] = false
			continue
		}
		for _, doc := range view.Documents() {
			if ctx.Err() != nil {
				return resources, complete, progress, remaining
			}
			root := doc.Root()
			if !doc.Complete() {
				complete[d.Target.ID] = false
				continue
			}
			projected, err := project(ctx, d.Snapshot, doc, d.Target.Version)
			if err != nil {
				complete[d.Target.ID] = false
				continue
			}
			api, _ := queryMember(projected, "apiVersion").(string)
			kind, _ := queryMember(projected, "kind").(string)
			if !owned(api, kind) {
				continue
			}
			meta := queryMember(projected, "metadata")
			name, _ := queryMember(meta, "name").(string)
			namespace, _ := queryMember(meta, "namespace").(string)
			if namespace == "" {
				namespace = w.Config.DefaultNamespace
			}
			spec := member(root, "spec")
			if kind == "CronWorkflow" {
				spec = member(spec, "workflowSpec")
			}
			var declarations map[renameKey]int
			var templateValues []any
			if readOnly {
				declarations = semanticDeclarations(projected, kind)
				semanticSpec := queryMember(projected, "spec")
				if kind == "CronWorkflow" {
					semanticSpec = queryMember(semanticSpec, "workflowSpec")
				}
				templateValues, _ = queryMember(semanticSpec, "templates").([]any)
			}
			semanticRoot := workflowRoot(projected, root)
			semanticRoot.shared = sharedOrigins(ctx, d.Snapshot, doc, d.Target.Version)
			semanticSpec := workflowMember(semanticRoot, "spec")
			if kind == "CronWorkflow" {
				semanticSpec = workflowMember(semanticSpec, "workflowSpec")
			}
			resources = append(resources, resource{d, root, spec, kind, name, namespace, declarations, templateValues, sharedOrigins(ctx, d.Snapshot, doc, d.Target.Version), semanticSpec})
			remaining[d.Snapshot.URI()]++
		}
	}
	for _, d := range w.Documents {
		progress[d.Snapshot.URI()] = ExternalCompletion{Completed: remaining[d.Snapshot.URI()] == 0}
	}
	return resources, complete, progress, remaining
}
func external(ctx context.Context, w workspace.Workspace, query *externalQuery) ([]Diagnostic, []externalBinding, map[string]bool, map[source.URI]ExternalCompletion) {
	resources, complete, progress, remaining := externalResources(ctx, w, query != nil)
	diagnostics := []Diagnostic{}
	bindings := []externalBinding{}
	type resourcePosition struct {
		uri   source.URI
		start int
	}
	localFacts := map[resourcePosition]*localRenameBindings{}
	localFor := func(r resource) *localRenameBindings {
		if query == nil {
			return nil
		}
		key := resourcePosition{r.document.Snapshot.URI(), r.root.Span().Start}
		if b, ok := localFacts[key]; ok {
			return b
		}
		b, _ := collectLocalBindings(ctx, r.document.Snapshot, r.document.Target.Version, r.root.Span().Start, true)
		localFacts[key] = b
		return b
	}
	var parameters *inheritedParameterQuery
	if query != nil && (query.definitions || query.references) {
		query.omittedParameterUses = map[Location]bool{}
		for _, r := range resources {
			if string(r.document.Snapshot.URI()) != query.uri {
				continue
			}
			b := localFor(r)
			if b == nil {
				continue
			}
			for span, keys := range b.queryPositions {
				if !containsPosition(spanLocation(r.document.Snapshot, span), query.uri, query.position) {
					continue
				}
				for key := range keys {
					if key.kind != "workflow.parameters" {
						continue
					}
					parameters = &inheritedParameterQuery{name: key.name, uses: map[Location]*inheritedParameterUse{}, localDefinitions: map[Location]bool{}}
					for _, d := range b.declarations[key] {
						parameters.localDefinitions[spanLocation(r.document.Snapshot, d.span)] = true
					}
				}
			}
		}
	}
	for _, caller := range resources {
		var local *localRenameBindings
		if query != nil {
			local = localFor(caller)
		}
		if ctx.Err() != nil {
			return diagnostics, bindings, complete, progress
		}
		emit := func(node workflowValue, code, severity, message string) {
			loc := nodeLocation(caller.document.Snapshot, node.node)
			diagnostics = append(diagnostics, Diagnostic{URI: loc.URI, Range: loc.Range, Code: code, Severity: severity, Message: message, Source: "argo-workflows", TargetID: caller.document.Target.ID, PositionKind: node.origin})
		}
		bind := func(from yamlview.Node, callee resource, to yamlview.Node) {
			_, a, fromExact := exactName(from)
			_, z, toExact := exactName(to)
			fromExact = fromExact && !sharedOrigin(caller.shared, a)
			toExact = toExact && !sharedOrigin(callee.shared, z)
			if query != nil && !query.definitions && fromExact && !toExact {
				query.block(spanLocation(caller.document.Snapshot, a))
			}
			if fromExact && toExact {
				bindings = append(bindings, externalBinding{from: spanLocation(caller.document.Snapshot, a), to: spanLocation(callee.document.Snapshot, z), selection: nodeLocation(caller.document.Snapshot, from)})
			}
		}
		resolve := func(semanticRef, semanticCall workflowValue, whole bool) {
			call := semanticCall.node
			nameValue := workflowMember(semanticRef, "name")
			nameNode := nameValue.node
			name := workflowString(nameValue)
			templateValue := workflowMember(semanticRef, "template")
			templateNode := templateValue.node
			template := workflowString(templateValue)
			if whole && parameters != nil && (name == "" || strings.Contains(name, "{{")) {
				parameters.resolve(ctx, query, caller, nil, false, localFor)
			}
			if name == "" {
				return
			}
			if strings.Contains(name, "{{") || strings.Contains(template, "{{") {
				emit(nameValue, "WF_DYNAMIC_REFERENCE", "Information", "dynamic external reference is not statically resolved")
				return
			}
			cluster := false
			clusterValue := workflowMember(semanticRef, "clusterScope")
			if clusterValue.present {
				boolean, ok := clusterValue.value.(bool)
				if !ok {
					return
				}
				cluster = boolean
			}
			wantedKind := "WorkflowTemplate"
			if cluster {
				wantedKind = "ClusterWorkflowTemplate"
			}
			candidates := []resource{}
			for _, candidate := range resources {
				if ctx.Err() != nil {
					return
				}
				if candidate.document.Target.ID != caller.document.Target.ID || candidate.kind != wantedKind || candidate.name != name {
					continue
				}
				if !cluster && (caller.namespace == "" || candidate.namespace != caller.namespace) {
					continue
				}
				candidates = append(candidates, candidate)
			}
			if whole && parameters != nil {
				parameters.resolve(ctx, query, caller, candidates, complete[caller.document.Target.ID], localFor)
			}
			if whole {
				externalExitQueries(ctx, query, caller, candidates, bind)
			}
			if len(candidates) == 0 {
				severity := "Information"
				if w.Config.ReferencePolicy == "closed" && complete[caller.document.Target.ID] && (cluster || caller.namespace != "") {
					severity = "Error"
				}
				emit(nameValue, "WF_TEMPLATE_REF_UNRESOLVED", severity, "external template resource is not resolved: "+name)
				return
			}
			// Proven input roles constrain references even when resource or
			// template uniqueness is lost.
			// Read semantic templates so encoded or derived origins cannot erase
			// a role; a known candidate set without the input adds no role.
			explicitTemplate := template
			if whole {
				explicitTemplate = workflowString(workflowMember(semanticCall, "entrypoint"))
			}
			if query != nil && query.definitions && containsPosition(nodeLocation(caller.document.Snapshot, call), query.uri, query.position) {
				// Select the source token before matching candidate inputs. The
				// complete semantic resource set still determines ambiguity.
				selected := func(node yamlview.Node) bool {
					return containsPosition(nodeLocation(caller.document.Snapshot, node), query.uri, query.position)
				}
				fromValue := templateValue
				if whole {
					fromValue = workflowMember(semanticCall, "entrypoint")
				}
				from := fromValue.node
				resourceSelected := nameValue.origin == "exact" && selected(nameNode)
				templateSelected := fromValue.origin == "exact" && selected(from)
				var argument yamlview.Node
				argumentKind := ""
				if !resourceSelected && !templateSelected {
					for _, kind := range []string{"parameters", "artifacts"} {
						for _, arg := range member(member(call, "arguments"), kind).Elements() {
							if node := member(arg, "name"); selected(node) {
								argument, argumentKind = node, kind
							}
						}
					}
				}
				candidate := func(from yamlview.Node, callee resource, to yamlview.Node) {
					_, a, fromExact := exactName(from)
					_, z, toExact := exactName(to)
					fromExact = fromExact && !sharedOrigin(caller.shared, a)
					toExact = toExact && !sharedOrigin(callee.shared, z)
					if fromExact && toExact {
						query.candidates = append(query.candidates, externalBinding{from: spanLocation(caller.document.Snapshot, a), to: spanLocation(callee.document.Snapshot, z), selection: nodeLocation(caller.document.Snapshot, from)})
					}
				}
				for _, callee := range candidates {
					if ctx.Err() != nil {
						return
					}
					if resourceSelected {
						candidate(nameNode, callee, member(member(callee.root, "metadata"), "name"))
						continue
					}
					if (!templateSelected && argumentKind == "") || explicitTemplate == "" || strings.Contains(explicitTemplate, "{{") {
						continue
					}
					for _, candidateTemplate := range workflowMember(callee.semanticSpec, "templates").Elements() {
						t := candidateTemplate.node
						if ctx.Err() != nil {
							return
						}
						if workflowString(workflowMember(candidateTemplate, "name")) != explicitTemplate {
							continue
						}
						if templateSelected {
							candidate(from, callee, member(t, "name"))
							continue
						}
						name := stringValue(argument)
						if name == "" {
							continue
						}
						for _, input := range member(member(t, "inputs"), argumentKind).Elements() {
							if stringValue(member(input, "name")) == name {
								candidate(argument, callee, member(input, "name"))
							}
						}
					}
				}
			}
			if query != nil && !query.definitions && explicitTemplate != "" && !strings.Contains(explicitTemplate, "{{") {
				roleArguments := map[string][]yamlview.Node{}
				for _, kind := range []string{"parameters", "artifacts"} {
					for _, arg := range member(member(call, "arguments"), kind).Elements() {
						if !query.references || containsPosition(nodeLocation(caller.document.Snapshot, member(arg, "name")), query.uri, query.position) {
							roleArguments[kind] = append(roleArguments[kind], arg)
						}
					}
				}
				for _, candidate := range candidates {
					if ctx.Err() != nil {
						return
					}
					if len(candidates) == 1 && candidate.declarations[renameKey{-1, "template", explicitTemplate}] == 1 {
						continue
					}
					for _, value := range candidate.templateValues {
						if ctx.Err() != nil {
							return
						}
						if queryMember(value, "name") != explicitTemplate {
							continue
						}
						for _, kind := range []string{"parameters", "artifacts"} {
							inputs, _ := queryMember(queryMember(value, "inputs"), kind).([]any)
							for _, arg := range roleArguments[kind] {
								argument := member(arg, "name")
								name := stringValue(argument)
								if name == "" {
									continue
								}
								for _, input := range inputs {
									if queryMember(input, "name") == name {
										query.block(nodeLocation(caller.document.Snapshot, argument))
										break
									}
								}
							}
						}
					}
				}
			}
			if len(candidates) > 1 {
				emit(nameValue, "WF_TEMPLATE_REF_AMBIGUOUS", "Warning", "multiple external template resources match "+name)
				return
			}
			callee := candidates[0]
			if whole && query == nil && complete[caller.document.Target.ID] {
				inheritedParameterDiagnostics(ctx, caller, callee, emit)
			}
			if whole && query == nil {
				// Caller objects win as whole named arguments. Base-winning values
				// are checked in the caller context without relocating base errors.
				own := namedInputs(workflowMember(caller.semanticSpec, "arguments"), "parameters")
				counts := map[string]int{}
				inherited := workflowMember(workflowMember(callee.semanticSpec, "arguments"), "parameters").Elements()
				for _, arg := range inherited {
					if ctx.Err() != nil {
						return
					}
					counts[workflowString(workflowMember(arg, "name"))]++
				}
				for _, arg := range inherited {
					if ctx.Err() != nil {
						return
					}
					name := workflowString(workflowMember(arg, "name"))
					if own[name] || counts[name] != 1 {
						continue
					}
					message, _ := argumentValueError(arg.value, caller.document.Target.Version, caller.kind == "WorkflowTemplate" || caller.kind == "ClusterWorkflowTemplate")
					if message != "" {
						emit(nameValue, "WF_ARGUMENT_VALUE_INVALID", "Error", message)
					}
				}
			}
			bind(nameNode, callee, member(member(callee.root, "metadata"), "name"))
			args := []any{workflowMember(semanticCall, "arguments").value}
			if whole {
				templateValue = workflowMember(semanticCall, "entrypoint")
				templateNode = templateValue.node
				template = workflowString(templateValue)
				if template == "" {
					template = workflowString(workflowMember(callee.semanticSpec, "entrypoint"))
					templateValue = nameValue
					templateNode = nameNode
				}
				args = append(args, workflowMember(callee.semanticSpec, "arguments").value)
			}
			templateValidation := caller.kind == "WorkflowTemplate" || caller.kind == "ClusterWorkflowTemplate"
			if whole && query == nil {
				if workflowVersionAtLeast(caller.document.Target.Version, 4, 6) {
					// onExit uses the caller's original arguments, unlike entrypoint.
					exitValue := workflowMember(semanticCall, "onExit")
					exitName := workflowString(exitValue)
					if exitName == "" {
						exitName = workflowString(workflowMember(callee.semanticSpec, "onExit"))
						exitValue = nameValue
					}
					if strings.Contains(exitName, "{{") {
						emit(exitValue, "WF_DYNAMIC_REFERENCE", "Information", "dynamic template reference is not statically resolved")
					} else if exitName != "" {
						count := 0
						var selectedExit workflowValue
						for _, candidate := range workflowMember(callee.semanticSpec, "templates").Elements() {
							if workflowString(workflowMember(candidate, "name")) == exitName {
								count++
								selectedExit = candidate
							}
						}
						if count == 0 {
							emit(exitValue, "WF_TEMPLATE_REF_UNRESOLVED", "Error", "resolved resource does not declare template "+exitName)
						} else if count == 1 {
							for _, missing := range missingArguments(caller.document.Target.Version, selectedExit.value, workflowMember(semanticCall, "arguments").value) {
								emit(exitValue, "WF_ARGUMENT_MISSING", "Error", "required input "+missing+" was not supplied")
							}
						}
					}

				}
				// Template validation decides IgnoreEntrypoint from the caller,
				// before resolving a referenced spec's fallback entrypoint.
				if templateValidation && workflowString(workflowMember(semanticCall, "entrypoint")) == "" {
					return
				}
			}
			if template == "" {
				if whole && query == nil && !templateValidation {
					emit(nameValue, "WF_TEMPLATE_REF_UNRESOLVED", "Error", "effective entrypoint is required")
				}
				return
			}
			found := false
			templateCount := callee.declarations[renameKey{-1, "template", template}]
			for templateIndex, semanticTemplate := range workflowMember(callee.semanticSpec, "templates").Elements() {
				t := semanticTemplate.node
				if workflowString(workflowMember(semanticTemplate, "name")) == template {
					found = true
					// Workflow-template entrypoints are checked with FakeArguments.
					if query == nil && (!whole || !templateValidation) {
						for _, missing := range missingArguments(caller.document.Target.Version, semanticTemplate.value, args...) {
							emit(templateValue, "WF_ARGUMENT_MISSING", "Error", "required input "+missing+" was not supplied")
						}
					}
					if templateCount == 1 {
						if !whole || member(call, "entrypoint").Kind() != yamlview.Empty {
							bind(templateNode, callee, member(t, "name"))
						}
						for _, kind := range []string{"parameters", "artifacts"} {
							inputNames := map[string][]yamlview.Node{}
							for _, input := range member(member(t, "inputs"), kind).Elements() {
								node := member(input, "name")
								name := stringValue(node)
								inputNames[name] = append(inputNames[name], node)
							}
							for _, arg := range member(member(call, "arguments"), kind).Elements() {
								if query != nil && query.definitions && !containsPosition(nodeLocation(caller.document.Snapshot, member(arg, "name")), query.uri, query.position) {
									continue
								}
								name := stringValue(member(arg, "name"))
								matches := inputNames[name]
								count := callee.declarations[renameKey{templateIndex, "inputs." + kind, name}]
								if query != nil && !query.definitions && count > 0 && (count != 1 || len(matches) != 1) {
									query.block(nodeLocation(caller.document.Snapshot, member(arg, "name")))
								}
								if count == 1 && len(matches) == 1 {
									bind(member(arg, "name"), callee, matches[0])
								}
								if query.at(caller.document.Snapshot.URI(), member(arg, "name").Span()) {
									for key, count := range callee.declarations {
										if key.owner == templateIndex && key.kind == "inputs."+kind {
											query.names[key.name] = count
										}
									}
									for _, input := range member(member(t, "inputs"), kind).Elements() {
										node := member(input, "name")
										name := stringValue(node)
										_, _, exact := exactName(node)
										exact = exact && !sharedOrigin(callee.shared, node.Span())
										if name != "" {
											query.suggest(name, callee, exact)
										}
									}
								}
							}
						}
						if local != nil {
							for _, ref := range local.externalOutputs {
								decl := local.declarations[ref.producer]
								if len(decl) != 1 || decl[0].span.Start < call.Span().Start || decl[0].span.End > call.Span().End {
									continue
								}
								matches := []yamlview.Node{}
								semanticMatches := callee.declarations[renameKey{templateIndex, "outputs." + ref.kind, ref.name}]
								if query.at(caller.document.Snapshot.URI(), ref.span) {
									for key, count := range callee.declarations {
										if key.owner == templateIndex && key.kind == "outputs."+ref.kind {
											query.names[key.name] = count
										}
									}
								}
								for _, output := range member(member(t, "outputs"), ref.kind).Elements() {
									node := member(output, "name")
									name := stringValue(node)
									_, _, exact := exactName(node)
									exact = exact && !sharedOrigin(callee.shared, node.Span())
									if name == ref.name {
										if exact {
											matches = append(matches, node)
										}
									}
									if query.at(caller.document.Snapshot.URI(), ref.span) {
										query.suggest(name, callee, exact)
									}
								}
								if semanticMatches == 1 && len(matches) == 1 {
									_, span, _ := exactName(matches[0])
									bindings = append(bindings, externalBinding{from: spanLocation(caller.document.Snapshot, ref.span), to: spanLocation(callee.document.Snapshot, span)})
								}
							}
						}
					}
				}
			}
			if !found {
				emit(templateValue, "WF_TEMPLATE_REF_UNRESOLVED", "Error", "resolved resource does not declare template "+template)
			}
		}
		if ref := workflowMember(caller.semanticSpec, "workflowTemplateRef"); ref.present {
			resolve(ref, caller.semanticSpec, true)
		}
		hooks := func(n workflowValue) {
			for _, h := range workflowMember(n, "hooks").Members() {
				if ref := workflowMember(h.Value, "templateRef"); ref.present {
					resolve(ref, h.Value, false)
				}
			}
		}
		hooks(caller.semanticSpec)
		for _, t := range workflowMember(caller.semanticSpec, "templates").Elements() {
			if ctx.Err() != nil {
				return diagnostics, bindings, complete, progress
			}
			hooks(t)
			for _, task := range workflowMember(workflowMember(t, "dag"), "tasks").Elements() {
				if ref := workflowMember(task, "templateRef"); ref.present {
					resolve(ref, task, false)
				}
				hooks(task)
			}
			for _, group := range workflowMember(t, "steps").Elements() {
				for _, step := range group.Elements() {
					if ref := workflowMember(step, "templateRef"); ref.present {
						resolve(ref, step, false)
					}
					hooks(step)
				}
			}
		}
		if ctx.Err() != nil {
			return diagnostics, bindings, complete, progress
		}

		uri := caller.document.Snapshot.URI()
		remaining[uri]--
		done := progress[uri]
		if done.Resources == nil {
			done.Resources = map[source.Span]bool{}
		}
		done.Resources[caller.root.Span()] = true
		done.Completed = remaining[uri] == 0
		progress[uri] = done
	}
	if parameters != nil {
		bindings = parameters.finish(query, bindings)
	}
	return diagnostics, bindings, complete, progress
}
func ExternalDiagnostics(ctx context.Context, w workspace.Workspace) ([]Diagnostic, map[source.URI]ExternalCompletion) {
	d, _, _, progress := external(ctx, w, nil)
	return d, progress
}
func containsPosition(loc Location, uri string, pos source.Position) bool {
	if loc.URI != uri {
		return false
	}
	a, b := loc.Range.Start, loc.Range.End
	return (pos.Line > a.Line || pos.Line == a.Line && pos.UTF16Column >= a.Character) && (pos.Line < b.Line || pos.Line == b.Line && pos.UTF16Column <= b.Character)
}
func ExternalDefinitions(ctx context.Context, w workspace.Workspace, uri string, pos source.Position) []Location {
	result := []Location{}
	query := &externalQuery{offset: -1, definitions: true, uri: uri, position: pos}
	_, bindings, _, _ := external(ctx, w, query)
	seen := map[Location]bool{}
	for _, b := range append(bindings, query.candidates...) {
		if b.selected(uri, pos) && !seen[b.to] {
			seen[b.to] = true
			result = append(result, b.to)
		}
	}
	sort.Slice(result, func(i, j int) bool {
		a, b := result[i], result[j]
		if a.URI != b.URI {
			return a.URI < b.URI
		}
		if a.Range.Start.Line != b.Range.Start.Line {
			return a.Range.Start.Line < b.Range.Start.Line
		}
		if a.Range.Start.Character != b.Range.Start.Character {
			return a.Range.Start.Character < b.Range.Start.Character
		}
		if a.Range.End.Line != b.Range.End.Line {
			return a.Range.End.Line < b.Range.End.Line
		}
		return a.Range.End.Character < b.Range.End.Character
	})
	return result
}

// Resolve one identity across local and external uses in the same target scope.
// Returned declarations and references are deduplicated without choosing an
// ambiguous resource or local symbol.
func ScopeReferences(ctx context.Context, w workspace.Workspace, uri string, pos source.Position, includeDeclaration bool) []Location {
	result := []Location{}
	query := &externalQuery{offset: -1, references: true, uri: uri, position: pos}
	_, bindings, _, _ := external(ctx, w, query)
	for _, selection := range query.blocked {
		if containsPosition(selection, uri, pos) {
			return result
		}
	}
	targets := map[Location]bool{}
	localReferences := []Location{}
	for _, d := range w.Documents {
		if string(d.Snapshot.URI()) == uri {
			definitions, blocked := domainDefinitions(ctx, d.Snapshot, d.Target.Version, pos)
			if blocked {
				return result
			}
			localReferences = append(localReferences, References(ctx, d.Snapshot, d.Target.Version, pos, includeDeclaration)...)
			for _, target := range definitions {
				targets[target] = true
			}
		}
	}
	for _, b := range bindings {
		if b.selected(uri, pos) || containsPosition(b.to, uri, pos) {
			targets[b.to] = true
		}
	}
	// Uniqueness belongs to the combined local and external selection.
	if len(targets) != 1 {
		return result
	}
	seen := map[Location]bool{}
	add := func(loc Location) {
		if query.omittedParameterUses[loc] {
			return
		}
		if !seen[loc] {
			seen[loc] = true
			result = append(result, loc)
		}
	}
	for _, loc := range localReferences {
		add(loc)
	}
	for target := range targets {
		if ctx.Err() != nil {
			return nil
		}
		if includeDeclaration {
			add(target)
		}
		for _, d := range w.Documents {
			if string(d.Snapshot.URI()) == target.URI {
				for _, loc := range References(ctx, d.Snapshot, d.Target.Version, source.Position{Line: target.Range.Start.Line, UTF16Column: target.Range.Start.Character}, false) {
					add(loc)
				}
			}
		}
		for _, b := range bindings {
			if b.to == target {
				add(b.from)
			}
		}
	}
	return result
}
