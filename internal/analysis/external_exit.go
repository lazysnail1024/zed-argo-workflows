package analysis

import (
	"context"
	"strings"

	"github.com/lazysnail1024/k8s-crd-toolkit/yamlview"
)

// The whole-resource lookup already established the complete resource set.
// Explicit exit tokens and actual input arguments have independent source roles;
// an inherited exit without a caller token does not invent a source location.
func externalExitQueries(ctx context.Context, query *externalQuery, caller resource, candidates []resource, bind func(yamlview.Node, resource, yamlview.Node)) {
	if query == nil || !query.definitions && !query.references || !workflowVersionAtLeast(caller.document.Target.Version, 4, 6) {
		return
	}
	if query.definitions && !containsPosition(nodeLocation(caller.document.Snapshot, caller.root), query.uri, query.position) {
		return
	}
	exit := workflowMember(caller.semanticSpec, "onExit")
	selected := func(value workflowValue) bool {
		return value.origin == "exact" && containsPosition(nodeLocation(caller.document.Snapshot, value.node), query.uri, query.position)
	}
	exitSelected := exit.present && selected(exit)
	type argument struct {
		value workflowValue
		kind  string
	}
	arguments := []argument{}
	for _, kind := range []string{"parameters", "artifacts"} {
		for _, item := range workflowMember(workflowMember(caller.semanticSpec, "arguments"), kind).Elements() {
			value := workflowMember(item, "name")
			if !query.definitions || selected(value) {
				arguments = append(arguments, argument{value, kind})
			}
		}
	}
	if query.definitions && !exitSelected && len(arguments) == 0 {
		return
	}
	for _, callee := range candidates {
		if ctx.Err() != nil {
			return
		}
		name := workflowString(exit)
		if name == "" {
			name = workflowString(workflowMember(callee.semanticSpec, "onExit"))
		}
		if name == "" || strings.Contains(name, "{{") {
			continue
		}
		templates := []workflowValue{}
		for _, template := range workflowMember(callee.semanticSpec, "templates").Elements() {
			if workflowString(workflowMember(template, "name")) == name {
				templates = append(templates, template)
			}
		}
		unique := len(candidates) == 1 && len(templates) == 1
		if !query.definitions && exit.present && len(templates) > 0 && !unique {
			query.block(nodeLocation(caller.document.Snapshot, exit.node))
		}
		for _, template := range templates {
			if query.definitions && exitSelected || !query.definitions && unique && exit.present {
				bind(queryNode(exit), callee, queryNode(workflowMember(template, "name")))
			}
			// Keep every semantic declaration, including inexact origins, while
			// enumerating each input collection only once for this template.
			inputNames := map[string]map[string][]workflowValue{}
			for _, kind := range []string{"parameters", "artifacts"} {
				inputNames[kind] = map[string][]workflowValue{}
				for _, input := range workflowMember(workflowMember(template, "inputs"), kind).Elements() {
					value := workflowMember(input, "name")
					name := workflowString(value)
					inputNames[kind][name] = append(inputNames[kind][name], value)
				}
			}
			for _, arg := range arguments {
				if ctx.Err() != nil {
					return
				}
				name := workflowString(arg.value)
				if name == "" {
					continue
				}
				inputs := inputNames[arg.kind][name]
				if !query.definitions && len(inputs) > 0 && (!unique || len(inputs) != 1) {
					query.block(nodeLocation(caller.document.Snapshot, arg.value.node))
					continue
				}
				if query.definitions || unique {
					for _, input := range inputs {
						bind(queryNode(arg.value), callee, queryNode(input))
					}
				}
			}
		}
	}
}
