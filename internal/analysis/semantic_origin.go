package analysis

import (
	"sort"
	"strconv"

	"github.com/lazysnail1024/k8s-crd-toolkit/source"
	"github.com/lazysnail1024/k8s-crd-toolkit/yamlview"
)

// workflowValue carries resolved workflow values and their independent source
// origin. A missing direct CST child cannot erase a projected declaration.
type workflowValue struct {
	value   any
	node    yamlview.Node
	origin  string
	present bool
	shared  map[source.Span]bool
}

func workflowRoot(value any, node yamlview.Node) workflowValue {
	return workflowValue{value: value, node: node, origin: "exact", present: true}
}
func workflowMember(n workflowValue, key string) workflowValue {
	values, _ := n.value.(map[string]any)
	value, exists := values[key]
	node, origin := location(n.node, []string{key})
	if n.origin != "exact" {
		origin = n.origin
	}
	if sharedOrigin(n.shared, node.Span()) {
		origin = "derived"
	}
	return workflowValue{value, node, origin, exists, n.shared}
}
func workflowString(n workflowValue) string { value, _ := n.value.(string); return value }
func (n workflowValue) Span() source.Span   { return n.node.Span() }
func (n workflowValue) Kind() yamlview.NodeKind {
	if !n.present {
		return yamlview.Empty
	}
	switch n.value.(type) {
	case map[string]any:
		return yamlview.Mapping
	case []any:
		return yamlview.Sequence
	default:
		return yamlview.Scalar
	}
}
func (n workflowValue) Elements() []workflowValue {
	values, _ := n.value.([]any)
	result := make([]workflowValue, 0, len(values))
	// Node.Elements returns a detached slice. Fetch it once, not once per
	// semantic item through location/at: dense parameter arrays must take
	// linear work and allocation while keeping each item's source origin.
	sequence := n.node.Kind() == yamlview.Sequence
	var nodes []yamlview.Node
	if sequence {
		nodes = n.node.Elements()
	}
	for i, value := range values {
		var node yamlview.Node
		var origin string
		if sequence {
			node, origin = n.node, "ambiguous"
			if i < len(nodes) && nodes[i].Span() != n.node.Span() {
				node, origin = nodes[i], "exact"
				if node.Kind() == yamlview.Alias {
					origin = "derived"
				}
			}
		} else {
			node, origin = location(n.node, []string{strconv.Itoa(i)})
		}
		if n.origin != "exact" {
			origin = n.origin
		}
		result = append(result, workflowValue{value, node, origin, true, n.shared})
	}
	return result
}

type workflowMemberValue struct {
	Name  string
	Value workflowValue
}

func (n workflowValue) Members() []workflowMemberValue {
	values, _ := n.value.(map[string]any)
	keys := make([]string, 0, len(values))
	for key := range values {
		keys = append(keys, key)
	}
	sort.Strings(keys)
	result := make([]workflowMemberValue, 0, len(keys))
	for _, key := range keys {
		result = append(result, workflowMemberValue{Name: key, Value: workflowMember(n, key)})
	}
	return result
}
