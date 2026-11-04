package analysis

import (
	"fmt"
	"github.com/lazysnail1024/k8s-crd-toolkit/source"
	"github.com/lazysnail1024/k8s-crd-toolkit/yamlview"
	"sort"
	"strconv"
)

func unknownFields(snapshot source.Snapshot, root yamlview.Node, value any, schema map[string]any, version string, shared map[source.Span]bool) []Diagnostic {
	result := []Diagnostic{}
	var walk func(any, map[string]any, []string)
	walk = func(value any, schema map[string]any, path []string) {
		switch object := value.(type) {
		case map[string]any:
			props, _ := schema["properties"].(map[string]any)
			additional, hasAdditional := schema["additionalProperties"]
			preserve, _ := schema["x-kubernetes-preserve-unknown-fields"].(bool)
			keys := make([]string, 0, len(object))
			for key := range object {
				keys = append(keys, key)
			}
			sort.Strings(keys)
			for _, key := range keys {
				childPath := append(append([]string{}, path...), key)
				if child, ok := props[key].(map[string]any); ok {
					walk(object[key], child, childPath)
					continue
				}
				if child, ok := additional.(map[string]any); ok {
					walk(object[key], child, childPath)
					continue
				}
				if preserve || additional == true || len(props) == 0 && !hasAdditional {
					continue
				}
				node, positionKind := location(root, path)
				if positionKind == "exact" {
					found := false
					for _, m := range node.Members() {
						if stringValue(m.Key) == key {
							node = m.Key
							found = true
							break
						}
					}
					if !found {
						positionKind = "derived"
					}
				}
				if sharedOrigin(shared, node.Span()) {
					positionKind = "derived"
				}
				start, e1 := snapshot.Position(node.Span().Start)
				end, e2 := snapshot.Position(node.Span().End)
				if e1 != nil || e2 != nil {
					continue
				}
				severity := "Warning"
				if additional == false {
					severity = "Error"
				}
				result = append(result, Diagnostic{URI: string(snapshot.URI()), Range: Range{Position{start.Line, start.UTF16Column}, Position{end.Line, end.UTF16Column}}, Code: "SCHEMA_UNKNOWN_FIELD", Source: "argo-workflows", Severity: severity, Message: fmt.Sprintf("field %q is not defined for target %s", key, version), TargetID: version, PositionKind: positionKind})
			}
		case []any:
			if items, ok := schema["items"].(map[string]any); ok {
				for i, item := range object {
					walk(item, items, append(append([]string{}, path...), strconv.Itoa(i)))
				}
			}
		}
	}
	walk(value, schema, nil)
	return result
}
