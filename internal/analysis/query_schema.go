package analysis

import (
	"context"
	"sort"
	"strconv"
	"strings"

	jsonschema "github.com/santhosh-tekuri/jsonschema/v6"
)

func queryChild(schema *jsonschema.Schema, name string, value any) *jsonschema.Schema {
	if _, ok := value.([]any); ok {
		index, err := strconv.Atoi(name)
		if err != nil {
			return nil
		}
		if index < len(schema.PrefixItems) {
			return schema.PrefixItems[index]
		}
		if schema.Items2020 != nil {
			return schema.Items2020
		}
		child, _ := schema.Items.(*jsonschema.Schema)
		return child
	}
	if child := schema.Properties[name]; child != nil {
		return child
	}
	child, _ := schema.AdditionalProperties.(*jsonschema.Schema)
	return child
}
func queryMember(value any, name string) any {
	switch v := value.(type) {
	case map[string]any:
		return v[name]
	case []any:
		i, err := strconv.Atoi(name)
		if err == nil && i >= 0 && i < len(v) {
			return v[i]
		}
	}
	return nil
}

// Only explicit finite sibling constraints can select a branch. The active
// field is excluded, so its partial value cannot eliminate its alternatives.
func queryCompatible(ctx context.Context, s *jsonschema.Schema, path []string, value any, depth int) bool {
	if s == nil || depth > 256 || ctx.Err() != nil {
		return false
	}
	if s.Bool != nil && !*s.Bool {
		return false
	}
	if object, ok := value.(map[string]any); ok {
		for name, child := range s.Properties {
			if len(path) > 0 && name == path[0] {
				continue
			}
			if v, exists := object[name]; exists && queryFinite(ctx, child, 0) && child.Validate(v) != nil {
				return false
			}
		}
	}
	if s.Ref != nil && !queryCompatible(ctx, s.Ref, path, value, depth+1) {
		return false
	}
	for _, child := range s.AllOf {
		if !queryCompatible(ctx, child, path, value, depth+1) {
			return false
		}
	}
	for _, branches := range [][]*jsonschema.Schema{s.AnyOf, s.OneOf} {
		if len(branches) == 0 {
			continue
		}
		possible := false
		for _, child := range branches {
			possible = possible || queryCompatible(ctx, child, path, value, depth+1)
		}
		if !possible {
			return false
		}
	}
	return true
}
func queryFinite(ctx context.Context, s *jsonschema.Schema, depth int) bool {
	if s == nil || depth > 256 || ctx.Err() != nil {
		return false
	}
	if s.Const != nil || s.Enum != nil {
		return true
	}
	if queryFinite(ctx, s.Ref, depth+1) {
		return true
	}
	for _, group := range [][]*jsonschema.Schema{s.AllOf, s.AnyOf, s.OneOf} {
		for _, child := range group {
			if queryFinite(ctx, child, depth+1) {
				return true
			}
		}
	}
	return false
}

// Traversal reads the same compiled graph used by validation. Alternatives are
// not globally deduplicated: duplicate oneOf branches still count separately.
func queryNodes(ctx context.Context, s *jsonschema.Schema, path []string, value any, depth int) ([]*jsonschema.Schema, bool) {
	if s == nil || depth > 256 || ctx.Err() != nil {
		return nil, false
	}
	result := []*jsonschema.Schema{}
	ambiguous := false
	if len(path) == 0 {
		result = append(result, s)
	} else if child := queryChild(s, path[0], value); child != nil {
		nodes, a := queryNodes(ctx, child, path[1:], queryMember(value, path[0]), depth+1)
		result = append(result, nodes...)
		ambiguous = ambiguous || a
	}
	if s.Ref != nil {
		nodes, a := queryNodes(ctx, s.Ref, path, value, depth+1)
		result = append(result, nodes...)
		ambiguous = ambiguous || a
	}
	for _, child := range s.AllOf {
		nodes, a := queryNodes(ctx, child, path, value, depth+1)
		result = append(result, nodes...)
		ambiguous = ambiguous || a
	}
	for _, branches := range [][]*jsonschema.Schema{s.AnyOf, s.OneOf} {
		possible := []*jsonschema.Schema{}
		for _, child := range branches {
			if queryCompatible(ctx, child, path, value, depth+1) {
				possible = append(possible, child)
			}
		}
		if len(possible) > 1 {
			ambiguous = true
		}
		for _, child := range possible {
			nodes, a := queryNodes(ctx, child, path, value, depth+1)
			result = append(result, nodes...)
			ambiguous = ambiguous || a
		}
	}
	return result, ambiguous
}

// Key candidates have no chosen value yet. Apply enclosing closure and branch
// constraints without validating a placeholder value or requiring a complete
// object. Multiple possible oneOf branches remain candidates at this stage.
func queryAllowsKey(ctx context.Context, s *jsonschema.Schema, path []string, value any, depth int) bool {
	if s == nil || depth > 256 || ctx.Err() != nil || s.Bool != nil && !*s.Bool {
		return false
	}
	if len(path) > 0 {
		if child := queryChild(s, path[0], value); child != nil {
			if !queryAllowsKey(ctx, child, path[1:], queryMember(value, path[0]), depth+1) {
				return false
			}
		} else if allowed, ok := s.AdditionalProperties.(bool); ok && !allowed {
			return false
		}
	}
	if s.Ref != nil && !queryAllowsKey(ctx, s.Ref, path, value, depth+1) {
		return false
	}
	for _, child := range s.AllOf {
		if !queryAllowsKey(ctx, child, path, value, depth+1) {
			return false
		}
	}
	for _, branches := range [][]*jsonschema.Schema{s.AnyOf, s.OneOf} {
		if len(branches) == 0 {
			continue
		}
		possible := false
		for _, child := range branches {
			// Invalid ordinary siblings do not forbid independent keys.
			// Finite siblings only constrain the selection of alternatives.
			possible = possible || queryCompatible(ctx, child, path, value, depth+1) && queryAllowsKey(ctx, child, path, value, depth+1)
		}
		if !possible {
			return false
		}
	}
	return true
}

// Project constraints along the active path, preserving conjunction, inclusive
// alternatives and exclusive cardinality. Leaf assertions use Schema.Validate.
func queryAccepts(ctx context.Context, s *jsonschema.Schema, path []string, value, candidate any, depth int) bool {
	if s == nil || depth > 256 || ctx.Err() != nil {
		return false
	}
	if len(path) == 0 {
		return s.Validate(candidate) == nil
	}
	if !queryCompatible(ctx, s, path, value, depth) {
		return false
	}
	if child := queryChild(s, path[0], value); child != nil {
		if !queryAccepts(ctx, child, path[1:], queryMember(value, path[0]), candidate, depth+1) {
			return false
		}
	} else if allowed, ok := s.AdditionalProperties.(bool); ok && !allowed {
		return false
	}
	if s.Ref != nil && !queryAccepts(ctx, s.Ref, path, value, candidate, depth+1) {
		return false
	}
	for _, child := range s.AllOf {
		if !queryAccepts(ctx, child, path, value, candidate, depth+1) {
			return false
		}
	}
	for i, branches := range [][]*jsonschema.Schema{s.AnyOf, s.OneOf} {
		if len(branches) == 0 {
			continue
		}
		matches := 0
		for _, child := range branches {
			if queryAccepts(ctx, child, path, value, candidate, depth+1) {
				matches++
			}
		}
		if matches == 0 || i == 1 && matches != 1 {
			return false
		}
	}
	return true
}

func queryType(ctx context.Context, s *jsonschema.Schema, path []string, value any) string {
	types := queryTypes(ctx, s, path, value, 0)
	if types == nil || len(types) == 7 {
		return "unspecified"
	}
	if len(types) == 0 {
		return "never"
	}
	names := []string{}
	for name := range types {
		if name != "integer" || !types["number"] {
			names = append(names, name)
		}
	}
	sort.Strings(names)
	return strings.Join(names, " | ")
}
func queryTypes(ctx context.Context, s *jsonschema.Schema, path []string, value any, depth int) map[string]bool {
	if s == nil || depth > 256 || ctx.Err() != nil {
		return nil
	}
	result := map[string]bool{"null": true, "boolean": true, "number": true, "integer": true, "string": true, "array": true, "object": true}
	intersect := func(other map[string]bool) {
		if other != nil {
			for name := range result {
				if !other[name] {
					delete(result, name)
				}
			}
		}
	}
	if s.Bool != nil && !*s.Bool {
		return map[string]bool{}
	}
	if len(path) == 0 {
		if s.Types != nil {
			types := map[string]bool{}
			for _, name := range s.Types.ToStrings() {
				types[name] = true
				if name == "number" {
					types["integer"] = true
				}
			}
			intersect(types)
		}
	} else if child := queryChild(s, path[0], value); child != nil {
		intersect(queryTypes(ctx, child, path[1:], queryMember(value, path[0]), depth+1))
	}
	if s.Ref != nil {
		intersect(queryTypes(ctx, s.Ref, path, value, depth+1))
	}
	for _, child := range s.AllOf {
		intersect(queryTypes(ctx, child, path, value, depth+1))
	}
	for _, branches := range [][]*jsonschema.Schema{s.AnyOf, s.OneOf} {
		if len(branches) == 0 {
			continue
		}
		union := map[string]bool{}
		for _, child := range branches {
			if queryCompatible(ctx, child, path, value, depth+1) {
				for name := range queryTypes(ctx, child, path, value, depth+1) {
					union[name] = true
				}
			}
		}
		intersect(union)
	}
	return result
}
