package analysis

import (
	"bytes"
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"github.com/lazysnail1024/k8s-crd-toolkit/source"
	"github.com/lazysnail1024/k8s-crd-toolkit/yamlview"
	"github.com/lazysnail1024/zed-argo-workflows/compat"
	"regexp"
	yaml "sigs.k8s.io/yaml"
	"strings"
)

var anchorPattern = regexp.MustCompile(`(?:^|[\s:\-,\[{])&([^\s\[\]{},]+)`)
var tagPattern = regexp.MustCompile(`(?:^|[\s:\-,\[{])!`)

// preflight uses CST gaps because anchors and tags precede semantic node spans.
// It bounds expansion before handing bytes to the strict YAML value decoder.
func preflight(ctx context.Context, snapshot source.Snapshot, doc yamlview.Document, shared map[source.Span]bool) error {
	anchors := map[string][]yamlview.Node{}
	var collect func(yamlview.Node, int) error
	collect = func(n yamlview.Node, prefix int) error {
		if err := ctx.Err(); err != nil {
			return err
		}
		if n.Kind() == yamlview.Scalar && len(n.Raw()) > 1024*1024 {
			return fmt.Errorf("scalar exceeds 1 MiB")
		}
		if prefix < n.Span().Start {
			gap := snapshot.Text()[prefix:n.Span().Start]
			lines := strings.Split(gap, "\n")
			for i, line := range lines {
				if before, _, ok := strings.Cut(line, "#"); ok {
					lines[i] = before
				}
			}
			gap = strings.Join(lines, "\n")
			if tagPattern.MatchString(gap) {
				return fmt.Errorf("explicit YAML tag is not evaluated")
			}
			for _, match := range anchorPattern.FindAllStringSubmatch(gap, -1) {
				anchors[match[1]] = append(anchors[match[1]], n)
			}
		}
		keys := map[string]bool{}
		for _, m := range n.Members() {
			key := stringValue(m.Key)
			if keys[key] {
				return fmt.Errorf("duplicate mapping key %s", key)
			}
			keys[key] = true
		}
		previous := n.Span().Start
		for _, m := range n.Members() {
			if err := collect(m.Key, previous); err != nil {
				return err
			}
			if !plainString(m.Key.Raw()) {
				v, err := decode(m.Key.Raw())
				if err != nil {
					return err
				}
				if _, ok := v.(string); !ok {
					return fmt.Errorf("non-string mapping key")
				}
			}
			if err := collect(m.Value, m.Key.Span().End); err != nil {
				return err
			}
			previous = m.Value.Span().End
		}
		for _, e := range n.Elements() {
			if err := collect(e, previous); err != nil {
				return err
			}
			previous = e.Span().End
		}
		return nil
	}
	if err := collect(doc.Root(), doc.Span().Start); err != nil {
		return err
	}
	nodes := 0
	visiting := map[source.Span]bool{}
	var expand func(yamlview.Node) error
	expand = func(n yamlview.Node) error {
		if err := ctx.Err(); err != nil {
			return err
		}
		nodes++
		if nodes > 100000 {
			err := fmt.Errorf("alias expansion exceeds 100000 nodes")
			if budget, ok := ctx.Value(expansionKey{}).(*expansionBudget); ok {
				budget.err = err
			}
			return err
		}
		if visiting[n.Span()] {
			return fmt.Errorf("recursive alias is not allowed")
		}
		visiting[n.Span()] = true
		defer delete(visiting, n.Span())
		if n.Kind() == yamlview.Alias {
			name := strings.TrimPrefix(n.Raw(), "*")
			var target yamlview.Node
			found := false
			for _, candidate := range anchors[name] {
				if candidate.Span().Start < n.Span().Start {
					target = candidate
					found = true
				}
			}
			if !found {
				return fmt.Errorf("unknown alias %s", name)
			}
			shared[target.Span()] = true
			return expand(target)
		}
		for _, m := range n.Members() {
			if err := expand(m.Key); err != nil {
				return err
			}
			if err := expand(m.Value); err != nil {
				return err
			}
		}
		for _, e := range n.Elements() {
			if err := expand(e); err != nil {
				return err
			}
		}
		return nil
	}
	return expand(doc.Root())
}

func location(root yamlview.Node, path []string) (yamlview.Node, string) {
	node := root
	for _, part := range path {
		if node.Kind() == yamlview.Alias {
			return node, "derived"
		}
		if node.Kind() == yamlview.Mapping {
			next := member(node, part)
			if next.Kind() == yamlview.Empty {
				return node, "derived"
			}
			node = next
		} else if node.Kind() == yamlview.Sequence {
			next := at(node, []string{part})
			if next.Span() == node.Span() {
				return node, "ambiguous"
			}
			node = next
		} else {
			return node, "ambiguous"
		}
	}
	if node.Kind() == yamlview.Alias {
		return node, "derived"
	}
	return node, "exact"
}

// project is the shared strict semantic and target-ownership boundary. CST remains
// the location source; a failed projection is never a complete domain resource.
func project(ctx context.Context, snapshot source.Snapshot, doc yamlview.Document, version string) (any, error) {
	if err := ctx.Err(); err != nil {
		return nil, err
	}
	state, _ := ctx.Value(invocationKey{}).(*invocation)
	key := projectionIdentity{snapshotKey(snapshot), version, doc.Span()}
	if state != nil {
		if value, ok := state.projections[key]; ok {
			if err := admitProjection(ctx, key, value.graphNodes); err != nil {
				return nil, err
			}
			return value.value, nil
		}
	}

	if !doc.Complete() {
		return nil, fmt.Errorf("incomplete YAML document")
	}
	shared := map[source.Span]bool{}
	if err := preflight(ctx, snapshot, doc, shared); err != nil {
		return nil, err
	}
	data, err := yaml.YAMLToJSON([]byte(doc.Root().Raw()))
	var value any
	if err == nil {
		decoder := json.NewDecoder(bytes.NewReader(data))
		decoder.UseNumber()
		err = decoder.Decode(&value)
	}
	if err != nil {
		return nil, fmt.Errorf("strict YAML projection (duplicate or invalid key/value): %w", err)
	}
	root, _ := value.(map[string]any)
	api, _ := root["apiVersion"].(string)
	kind, _ := root["kind"].(string)
	if owned(api, kind) {
		if _, err := compat.SchemaFile(version, api, kind); err != nil {
			// A schema-only CRD absent from an older release is valid YAML with
			// an unavailable resource kind. Keep it projectable so validation
			// can report that version boundary without inventing template scopes.
			if !schemaOnlyKind(kind) || !errors.Is(err, compat.ErrSchemaUnavailable) {
				return nil, err
			}
		}
	}
	cost := 0
	if graphDocument(doc.Root()) {
		cost = logicalNodes(value)
	}
	if err := admitProjection(ctx, key, cost); err != nil {
		return nil, err
	}
	if state != nil && ctx.Err() == nil {
		if _, ok := state.views[key.snapshotIdentity]; ok {
			state.projections[key] = projectedDocument{value: value, graphNodes: cost, shared: shared}
		}
	}
	return value, nil
}
