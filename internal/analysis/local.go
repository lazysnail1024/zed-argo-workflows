package analysis

import (
	"context"
	"fmt"
	"github.com/lazysnail1024/k8s-crd-toolkit/source"
	"github.com/lazysnail1024/k8s-crd-toolkit/yamlview"
	"strings"
)

func localDiagnostics(ctx context.Context, snapshot source.Snapshot, sourceRoot yamlview.Node, value any, shared map[source.Span]bool, kind, version string) []Diagnostic {
	diagnostics := []Diagnostic{}
	var major, minor, patch int
	fmt.Sscanf(version, "%d.%d.%d", &major, &minor, &patch)
	omittedAvailable := major == 3 && (minor > 3 || minor == 3 && patch >= 9)
	root := workflowRoot(value, sourceRoot)
	root.shared = shared
	spec := workflowMember(root, "spec")
	if kind == "CronWorkflow" {
		spec = workflowMember(spec, "workflowSpec")
	}
	templates := workflowMember(spec, "templates").Elements()
	names := map[string]workflowValue{}
	ambiguousNames := map[string]bool{}
	emit := func(node workflowValue, code, message string) {
		start, e1 := snapshot.Position(node.Span().Start)
		end, e2 := snapshot.Position(node.Span().End)
		if e1 != nil || e2 != nil {
			return
		}
		severity := "Error"
		if code == "WF_DYNAMIC_REFERENCE" {
			severity = "Information"
		}
		diagnostics = append(diagnostics, Diagnostic{URI: string(snapshot.URI()), Range: Range{Position{start.Line, start.UTF16Column}, Position{end.Line, end.UTF16Column}}, Code: code, Source: "argo-workflows", Severity: severity, Message: message, TargetID: version, PositionKind: node.origin})
	}
	if workflowMember(spec, "workflowTemplateRef").present && len(templates) > 0 {
		emit(workflowMember(spec, "templates"), "WF_TEMPLATE_REF_TEMPLATES", "local templates cannot be combined with workflowTemplateRef")
	}
	for _, template := range templates {
		if ctx.Err() != nil {
			return diagnostics
		}
		nameNode := workflowMember(template, "name")
		name := workflowString(nameNode)
		if name == "" {
			continue
		}
		if _, exists := names[name]; exists {
			ambiguousNames[name] = true
			emit(nameNode, "WF_DUPLICATE_SYMBOL", "duplicate local template "+name)
		} else {
			names[name] = template
		}
	}
	duplicateNames := func(sequence workflowValue) {
		seen := map[string]bool{}
		for _, item := range sequence.Elements() {
			if ctx.Err() != nil {
				return
			}
			node := workflowMember(item, "name")
			name := workflowString(node)
			if name != "" && seen[name] {
				emit(node, "WF_DUPLICATE_SYMBOL", "duplicate parameter or artifact "+name)
			}
			seen[name] = true
		}
	}
	// A workflow argument is validated even if no callee consumes it.
	for _, arg := range workflowMember(workflowMember(spec, "arguments"), "parameters").Elements() {
		if ctx.Err() != nil {
			return diagnostics
		}
		message, atFrom := argumentValueError(arg.value, version, kind == "WorkflowTemplate" || kind == "ClusterWorkflowTemplate")
		if message != "" {
			field := "name"
			if atFrom {
				field = "valueFrom"
			}
			emit(workflowMember(arg, field), "WF_ARGUMENT_VALUE_INVALID", message)
		}
	}
	duplicateNames(workflowMember(workflowMember(spec, "arguments"), "parameters"))
	duplicateNames(workflowMember(workflowMember(spec, "arguments"), "artifacts"))
	for _, template := range templates {
		if ctx.Err() != nil {
			return diagnostics
		}
		for _, section := range []string{"inputs", "outputs"} {
			for _, kind := range []string{"parameters", "artifacts"} {
				duplicateNames(workflowMember(workflowMember(template, section), kind))
			}
		}
	}
	checkArgs := func(reference, arguments workflowValue) {
		name := workflowString(reference)
		callee, ok := names[name]
		if !ok || strings.Contains(name, "{{") {
			return
		}
		duplicateNames(workflowMember(arguments, "parameters"))
		duplicateNames(workflowMember(arguments, "artifacts"))
		if ambiguousNames[name] {
			return
		}
		for _, missing := range missingArguments(version, callee.value, arguments.value) {
			emit(reference, "WF_ARGUMENT_MISSING", "required input "+missing+" was not supplied")
		}
	}

	checkReference := func(node workflowValue) {
		name := workflowString(node)
		if name == "" {
			return
		}
		if strings.Contains(name, "{{") {
			emit(node, "WF_DYNAMIC_REFERENCE", "dynamic template reference is not statically resolved")
			return
		}
		if _, ok := names[name]; !ok {
			emit(node, "WF_LOCAL_TEMPLATE_MISSING", "local template does not exist: "+name)
		}
	}
	if workflowMember(spec, "workflowTemplateRef").Kind() == yamlview.Empty {
		checkReference(workflowMember(spec, "entrypoint"))
		if kind == "Workflow" || kind == "CronWorkflow" {
			checkArgs(workflowMember(spec, "entrypoint"), workflowMember(spec, "arguments"))
		}
		checkReference(workflowMember(spec, "onExit"))
		checkArgs(workflowMember(spec, "onExit"), workflowMember(spec, "arguments"))
	}
	if workflowMember(spec, "workflowTemplateRef").present && !workflowVersionAtLeast(version, 4, 6) {
		// Older validators resolve explicit workflow exits in the caller context.
		checkReference(workflowMember(spec, "onExit"))
		checkArgs(workflowMember(spec, "onExit"), workflowMember(spec, "arguments"))
	}
	checkHooks := func(node workflowValue) {
		for _, hook := range workflowMember(node, "hooks").Members() {
			if workflowMember(hook.Value, "templateRef").Kind() == yamlview.Empty {
				checkReference(workflowMember(hook.Value, "template"))
				checkArgs(workflowMember(hook.Value, "template"), workflowMember(hook.Value, "arguments"))
			}
		}
	}
	checkHooks(spec)
	for _, template := range templates {
		if ctx.Err() != nil {
			return diagnostics
		}
		checkReference(workflowMember(template, "onExit"))
		checkHooks(template)
		stepNames := map[string]bool{}
		for _, group := range workflowMember(template, "steps").Elements() {
			for _, step := range group.Elements() {
				nameNode := workflowMember(step, "name")
				name := workflowString(nameNode)
				if name != "" && stepNames[name] {
					emit(nameNode, "WF_DUPLICATE_SYMBOL", "duplicate parallel step "+name)
				}
				stepNames[name] = true
				if workflowMember(step, "templateRef").Kind() == yamlview.Empty {
					checkReference(workflowMember(step, "template"))
					checkArgs(workflowMember(step, "template"), workflowMember(step, "arguments"))
				}
				checkHooks(step)
			}
		}
		tasks := workflowMember(workflowMember(template, "dag"), "tasks").Elements()
		taskNames := map[string][]workflowValue{}
		dependsMode := false
		order := []string{}
		for _, task := range tasks {
			if ctx.Err() != nil {
				return diagnostics
			}
			if workflowString(workflowMember(task, "depends")) != "" {
				dependsMode = true
			}
			nameNode := workflowMember(task, "name")
			name := workflowString(nameNode)
			if name == "" {
				continue
			}
			if _, ok := taskNames[name]; ok {
				emit(nameNode, "WF_DUPLICATE_SYMBOL", "duplicate DAG task "+name)
			} else {
				order = append(order, name)
			}
			taskNames[name] = append(taskNames[name], task)
		}
		edges := map[string][]string{}
		for _, task := range tasks {
			if ctx.Err() != nil {
				return diagnostics
			}
			name := workflowString(workflowMember(task, "name"))
			if workflowMember(task, "templateRef").Kind() == yamlview.Empty {
				checkReference(workflowMember(task, "template"))
				checkArgs(workflowMember(task, "template"), workflowMember(task, "arguments"))
			}
			checkHooks(task)
			dependencies := workflowMember(task, "dependencies")
			depends := workflowMember(task, "depends")
			dependsText := workflowString(depends)
			if dependsMode {
				dependencyValues, _ := dependencies.value.([]any)
				if len(dependencyValues) > 0 {
					emit(dependencies, "WF_DEPENDENCY_MODE_CONFLICT", "DAG cannot combine nonempty depends with dependencies")
				}
				continueOn := workflowMember(task, "continueOn")
				if continueOn.value != nil {
					emit(continueOn, "WF_DEPENDENCY_MODE_CONFLICT", "DAG cannot combine nonempty depends with continueOn")
				}
			}
			refs := []string{}
			for _, dependency := range dependencies.Elements() {
				ref := workflowString(dependency)
				if ref == "" {
					continue
				}
				refs = append(refs, ref)
				if _, ok := taskNames[ref]; !ok {
					emit(dependency, "WF_DEPENDENCY_MISSING", "DAG task does not exist: "+ref)
				}
			}
			if dependsText != "" {
				if strings.Contains(dependsText, "{{") {
					emit(depends, "WF_DYNAMIC_REFERENCE", "dynamic depends is not statically resolved")
				} else {
					parsed, err := parseDepends(dependsText)
					if err != nil {
						emit(depends, "WF_DEPENDS_SYNTAX", err.Error())
					} else {
						loopReported := map[string]bool{}
						omittedReported := false
						for _, occurrence := range parsed {
							ref := occurrence.name
							refs = append(refs, ref)
							producers := taskNames[ref]
							if len(producers) == 0 {
								emit(depends, "WF_DEPENDENCY_MISSING", "DAG task does not exist: "+ref)
							}
							if occurrence.result == "Omitted" && !omittedAvailable && !omittedReported {
								emit(depends, "WF_DEPENDS_SYNTAX", `task result "Omitted" is not available before 3.3.9`)
								omittedReported = true
							}
							// Ambiguous producers cannot justify a loop conclusion. Every
							// occurrence still contributes its real dependency identity.
							if (occurrence.result == "AnySucceeded" || occurrence.result == "AllFailed") && len(producers) == 1 && !loopReported[ref] {
								producer := producers[0]
								items, _ := workflowMember(producer, "withItems").value.([]any)
								if len(items) == 0 && workflowString(workflowMember(producer, "withParam")) == "" && workflowMember(producer, "withSequence").value == nil {
									emit(depends, "WF_DEPENDENCY_LOOP_REQUIRED", "DAG task "+ref+" must declare a loop for an items result")
									loopReported[ref] = true
								}
							}
						}
					}
				}
			}
			edges[name] = refs
		}
		colors := map[string]int{}
		stack := []string{}
		reported := false
		var visit func(string)
		visit = func(name string) {
			if ctx.Err() != nil {
				return
			}
			if colors[name] == 2 {
				return
			}
			if colors[name] == 1 {
				if !reported {
					emit(workflowMember(taskNames[name][0], "name"), "WF_DAG_CYCLE", fmt.Sprintf("DAG dependency cycle: %s", strings.Join(append(append([]string{}, stack...), name), " -> ")))
					reported = true
				}
				return
			}
			if _, ok := taskNames[name]; !ok {
				return
			}
			colors[name] = 1
			stack = append(stack, name)
			for _, dependency := range edges[name] {
				visit(dependency)
			}
			stack = stack[:len(stack)-1]
			colors[name] = 2
		}
		for _, name := range order {
			visit(name)
		}
	}
	return diagnostics
}
