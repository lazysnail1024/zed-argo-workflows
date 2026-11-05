package analysis

import (
	"context"
	"fmt"
	"github.com/lazysnail1024/k8s-crd-toolkit/source"
	"github.com/lazysnail1024/k8s-crd-toolkit/yamlview"
	"strings"
)

type memberReference struct {
	parts    []string
	dynamic  bool
	spans    []source.Span
	dots     []int
	computed []source.Span
}

func identifierByte(c byte) bool {
	return c >= 'a' && c <= 'z' || c >= 'A' && c <= 'Z' || c >= '0' && c <= '9' || c == '_' || c == '-'
}
func quoted(text string, start int) (string, int, bool) {
	quote := text[start]
	var value strings.Builder
	for i := start + 1; i < len(text); i++ {
		if text[i] == quote {
			return value.String(), i + 1, true
		}
		if text[i] == '\\' {
			i++
			if i >= len(text) {
				break
			}
			switch text[i] {
			case '\\', '\'', '"':
				value.WriteByte(text[i])
			default:
				return "", i + 1, false
			}
		} else {
			value.WriteByte(text[i])
		}
	}
	return "", len(text), false
}

// References are parsed as member chains without evaluating expressions or functions.
func memberReferences(text string) []memberReference {
	refs := []memberReference{}
	for i := 0; i < len(text); {
		if text[i] == '\'' || text[i] == '"' {
			_, next, _ := quoted(text, i)
			i = next
			continue
		}
		if !identifierByte(text[i]) {
			i++
			continue
		}
		start := i
		for i < len(text) && identifierByte(text[i]) {
			i++
		}
		ref := memberReference{parts: []string{text[start:i]}, spans: []source.Span{{Start: start, End: i}}, dots: []int{-1}}
		for i < len(text) {
			for i < len(text) && (text[i] == ' ' || text[i] == '\t') {
				i++
			}
			if i >= len(text) {
				break
			}
			if text[i] == '.' {
				dot := i
				i++
				start = i
				for i < len(text) && identifierByte(text[i]) {
					i++
				}
				if start == i {
					break
				}
				ref.parts = append(ref.parts, text[start:i])
				ref.spans = append(ref.spans, source.Span{Start: start, End: i})
				ref.dots = append(ref.dots, dot)
				continue
			}
			if text[i] == '[' {
				i++
				innerStart := i
				for i < len(text) && text[i] == ' ' {
					i++
				}
				if i < len(text) && (text[i] == '\'' || text[i] == '"') {
					quoteStart := i
					value, next, ok := quoted(text, i)
					i = next
					for i < len(text) && text[i] == ' ' {
						i++
					}
					if ok && i < len(text) && text[i] == ']' {
						i++
						ref.parts = append(ref.parts, value)
						ref.spans = append(ref.spans, source.Span{Start: quoteStart + 1, End: next - 1})
						ref.dots = append(ref.dots, -1)
						continue
					}
				}
				ref.dynamic = true
				depth := 1
				for i < len(text) && depth > 0 {
					if text[i] == '[' {
						depth++
					}
					if text[i] == ']' {
						depth--
					}
					i++
				}
				ref.computed = append(ref.computed, source.Span{Start: innerStart, End: max(innerStart, i-1)})
				ref.parts = append(ref.parts, "*")
				ref.spans = append(ref.spans, source.Span{})
				ref.dots = append(ref.dots, -1)
				continue
			}
			break
		}
		if len(ref.parts) > 0 {
			refs = append(refs, ref)
			for _, inner := range ref.computed {
				for _, nested := range memberReferences(text[inner.Start:inner.End]) {
					for j := range nested.spans {
						nested.spans[j].Start += inner.Start
						nested.spans[j].End += inner.Start
						if nested.dots[j] >= 0 {
							nested.dots[j] += inner.Start
						}
					}
					refs = append(refs, nested)
				}
			}
		}
	}
	return refs
}
func scalarReferences(ctx context.Context, text string) ([]memberReference, error) {
	refs := []memberReference{}
	for {
		start := strings.Index(text, "{{")
		if start < 0 {
			break
		}
		text = text[start+2:]
		end := strings.Index(text, "}}")
		if end < 0 {
			break
		}
		if strings.HasPrefix(text[:end], "=") {
			parsed, err := expressionReferences(ctx, text[1:end])
			if err != nil {
				return nil, fmt.Errorf("invalid expression: %w", err)
			}
			refs = append(refs, parsed...)
		} else {
			refs = append(refs, memberReferences(strings.TrimSpace(text[:end]))...)
		}
		text = text[end+2:]
	}
	return refs, nil
}
func namedInputs(n workflowValue, kind string) map[string]bool {
	result := map[string]bool{}
	for _, item := range workflowMember(n, kind).Elements() {
		if name := workflowString(workflowMember(item, "name")); name != "" {
			result[name] = true
		}
	}
	return result
}

func referenceDiagnostics(ctx context.Context, snapshot source.Snapshot, sourceRoot yamlview.Node, value any, shared map[source.Span]bool, resourceKind, version string) ([]Diagnostic, error) {
	if schemaOnlyKind(resourceKind) {
		return nil, nil
	}
	var analysisErr error
	diagnostics := []Diagnostic{}
	root := workflowRoot(value, sourceRoot)
	root.shared = shared
	spec := workflowMember(root, "spec")
	if resourceKind == "CronWorkflow" {
		spec = workflowMember(spec, "workflowSpec")
	}
	templateList := workflowMember(spec, "templates").Elements()
	templates := map[string][]workflowValue{}
	for _, template := range templateList {
		if name := workflowString(workflowMember(template, "name")); name != "" {
			templates[name] = append(templates[name], template)
		}
	}
	workflowParameters := namedInputs(workflowMember(spec, "arguments"), "parameters")
	inherited := workflowMember(spec, "workflowTemplateRef").present
	// Both the JSON aggregate alias and standalone-template allowance start
	// at 3.4.2 in the fixed catalog. Other validation families keep their gates.
	parameterJSON := workflowVersionAtLeast(version, 4, 2)
	independentTemplate := parameterJSON && (resourceKind == "WorkflowTemplate" || resourceKind == "ClusterWorkflowTemplate")
	parameters, _ := workflowMember(workflowMember(spec, "arguments"), "parameters").value.([]any)
	aggregateAvailable := len(parameters) > 0
	emit := func(node workflowValue, code, message string) {
		loc := nodeLocation(snapshot, node.node)
		severity := "Error"
		if code == "WF_REFERENCE_INCOMPLETE" {
			severity = "Warning"
		}
		if code == "WF_DYNAMIC_REFERENCE" {
			severity = "Information"
		}
		// A direct scalar still describes the whole expression, not a proven
		// member-token edit. Derived/shared values point to their real host.
		origin := "ambiguous"
		if node.origin == "derived" || sharedOrigin(shared, node.Span()) {
			origin = "derived"
		}
		diagnostics = append(diagnostics, Diagnostic{URI: loc.URI, Range: loc.Range, Code: code, Severity: severity, Message: message, Source: "argo-workflows", TargetID: version, PositionKind: origin})
	}
	checkWorkflowParameter := func(node workflowValue, parts []string) {
		if len(parts) < 2 || parts[0] != "workflow" || parts[1] != "parameters" || inherited || independentTemplate {
			return
		}
		if len(parts) == 2 {
			if !aggregateAvailable {
				emit(node, "WF_REFERENCE_SCOPE", "workflow parameters aggregate is not available")
			}
			return
		}
		if workflowParameters[parts[2]] || parts[2] == "json" && parameterJSON && aggregateAvailable {
			return
		}
		emit(node, "WF_REFERENCE_SCOPE", "workflow parameter is not declared: "+parts[2])
	}
	for _, template := range templateList {
		inputs := workflowMember(template, "inputs")
		inputParameters, inputArtifacts := namedInputs(inputs, "parameters"), namedInputs(inputs, "artifacts")
		var scan func(workflowValue, map[string][]workflowValue, map[string][]workflowValue)
		scan = func(node workflowValue, tasks, steps map[string][]workflowValue) {
			if analysisErr != nil {
				return
			}
			if err := ctx.Err(); err != nil {
				analysisErr = err
				return
			}
			if node.Kind() == yamlview.Scalar {
				refs, err := scalarReferences(ctx, workflowString(node))
				if err != nil {
					analysisErr = err
					if ctx.Err() == nil {
						emit(node, "WF_REFERENCE_INCOMPLETE", "Reference analysis incomplete: "+err.Error())
					}
					return
				}
				for _, ref := range refs {
					parts := ref.parts
					if ref.dynamic {
						switch parts[0] {
						case "inputs", "workflow", "tasks", "steps":
							emit(node, "WF_DYNAMIC_REFERENCE", "dynamic member access is not statically resolved")
						}
						continue
					}
					switch parts[0] {
					case "inputs":
						if len(parts) >= 3 {
							known := inputParameters
							if parts[1] == "artifacts" {
								known = inputArtifacts
							} else if parts[1] != "parameters" {
								continue
							}
							if !known[parts[2]] {
								emit(node, "WF_REFERENCE_SCOPE", "input is not declared: "+strings.Join(parts[:3], "."))
							}
						}
					case "workflow":
						checkWorkflowParameter(node, parts)
					case "tasks", "steps":
						if len(parts) < 3 {
							continue
						}
						visible := tasks
						if parts[0] == "steps" {
							visible = steps
						}
						calls, ok := visible[parts[1]]
						if !ok {
							emit(node, "WF_REFERENCE_SCOPE", parts[0]+"."+parts[1]+" is not visible in this context")
							continue
						}
						if len(calls) != 1 || parts[2] != "outputs" || len(parts) < 4 {
							continue
						}
						call := calls[0]
						if workflowMember(call, "templateRef").present {
							continue
						}
						name := workflowString(workflowMember(call, "template"))
						callees := templates[name]
						if len(callees) != 1 || strings.Contains(name, "{{") {
							continue
						}
						callee := callees[0]
						outputKind := parts[3]
						if outputKind == "result" {
							if !workflowMember(callee, "container").present && !workflowMember(callee, "script").present && !workflowMember(callee, "http").present {
								emit(node, "WF_OUTPUT_MISSING", "callee does not expose outputs.result")
							}
						} else if len(parts) >= 5 && (outputKind == "parameters" || outputKind == "artifacts") {
							if !namedInputs(workflowMember(callee, "outputs"), outputKind)[parts[4]] {
								emit(node, "WF_OUTPUT_MISSING", "callee does not declare output "+outputKind+"."+parts[4])
							}
						}
					}
				}
				return
			}
			// This rule family also reads effective mapping keys. Source nodes
			// only locate a direct key; merged keys retain their real host.
			keyNodes := map[string]yamlview.Node{}
			if node.origin == "exact" {
				for _, member := range node.node.Members() {
					keyNodes[stringValue(member.Key)] = member.Key
				}
			}
			for _, member := range node.Members() {
				key := workflowValue{value: member.Name, node: node.node, origin: "derived", present: true, shared: node.shared}
				if direct, ok := keyNodes[member.Name]; ok {
					key.node, key.origin = direct, "exact"
				}
				refs, err := scalarReferences(ctx, member.Name)
				if err != nil {
					analysisErr = err
					return
				}
				for _, ref := range refs {
					if !ref.dynamic {
						checkWorkflowParameter(key, ref.parts)
					}
				}
				scan(member.Value, tasks, steps)
			}
			for _, child := range node.Elements() {
				scan(child, tasks, steps)
			}
		}
		taskList := workflowMember(workflowMember(template, "dag"), "tasks").Elements()
		allTasks := map[string][]workflowValue{}
		edges := map[string][]string{}
		dependencies := func(task workflowValue) []string {
			refs := []string{}
			for _, dependency := range workflowMember(task, "dependencies").Elements() {
				refs = append(refs, workflowString(dependency))
			}
			if parsed, err := parseDepends(workflowString(workflowMember(task, "depends"))); err == nil {
				for _, occurrence := range parsed {
					refs = append(refs, occurrence.name)
				}
			}
			return refs
		}
		for _, task := range taskList {
			name := workflowString(workflowMember(task, "name"))
			if name != "" {
				allTasks[name] = append(allTasks[name], task)
				edges[name] = append(edges[name], dependencies(task)...)
			}
		}
		for _, task := range taskList {
			visible := map[string][]workflowValue{}
			visited := map[string]bool{}
			var add func(string, bool)
			add = func(name string, uncertain bool) {
				if ctx.Err() != nil {
					return
				}
				calls := allTasks[name]
				uncertain = uncertain || len(calls) != 1
				previous, seen := visited[name]
				// A proven path remains certain; a later proven path upgrades a possible one.
				if seen && (!previous || uncertain) {
					return
				}
				visited[name] = uncertain
				visible[name] = calls
				if uncertain {
					// Presence can be possible through an ambiguous ancestor;
					// it cannot select a unique output-producing call.
					visible[name] = nil
				}
				for _, dependency := range edges[name] {
					add(dependency, uncertain)
				}
			}
			for _, dependency := range dependencies(task) {
				add(dependency, false)
			}
			scan(task, visible, nil)
		}
		previousSteps := map[string][]workflowValue{}
		for _, group := range workflowMember(template, "steps").Elements() {
			for _, step := range group.Elements() {
				scan(step, nil, previousSteps)
			}
			for _, step := range group.Elements() {
				if name := workflowString(workflowMember(step, "name")); name != "" {
					previousSteps[name] = append(previousSteps[name], step)
				}
			}
		}
		for _, member := range template.Members() {
			if member.Name == "dag" || member.Name == "steps" {
				continue
			}
			if member.Name == "outputs" {
				scan(member.Value, allTasks, previousSteps)
			} else {
				scan(member.Value, nil, nil)
			}
		}
	}
	return diagnostics, analysisErr
}
