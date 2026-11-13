package assets

import (
	"context"
	"crypto/sha256"
	"encoding/hex"
	"encoding/json"
)

// compactSchema factors repeated expanded CRD subtrees before cold compilation.
// The compiler still validates every assertion against the metaschema. Only its
// private decoded input changes; Document and the catalog's bytes/hashes retain
// the original schemas. The definitions and decoding maps live for one compile,
// under the existing compilation gate, rather than in another process cache.
func compactSchema(ctx context.Context, document map[string]any) (map[string]any, error) {
	if err := ctx.Err(); err != nil {
		return nil, err
	}
	if document["$schema"] != "https://json-schema.org/draft/2020-12/schema" {
		return document, nil
	}
	// Moving a schema containing identifiers or references can change its base
	// URI or dynamic scope. Leave those documents, including the already factored
	// automatic schemas, intact. This conservative scan also excludes future
	// vocabularies whose location-dependent behavior has not been reviewed.
	var scoped func(any, bool) (bool, error)
	scoped = func(value any, root bool) (bool, error) {
		if err := ctx.Err(); err != nil {
			return false, err
		}
		switch value := value.(type) {
		case map[string]any:
			for key, child := range value {
				if len(key) > 0 && key[0] == '$' && !(root && key == "$schema") {
					return true, nil
				}
				if found, err := scoped(child, false); found || err != nil {
					return found, err
				}
			}
		case []any:
			for _, child := range value {
				if found, err := scoped(child, false); found || err != nil {
					return found, err
				}
			}
		}
		return false, nil
	}
	if found, err := scoped(document, true); found || err != nil {
		return document, err
	}

	definitions := map[string]any{}
	var intern func(any, bool) (any, error)
	intern = func(value any, root bool) (any, error) {
		if err := ctx.Err(); err != nil {
			return nil, err
		}
		node, ok := value.(map[string]any)
		if !ok {
			return value, nil
		}
		// Visit schema positions only. Objects in const, enum, default, examples,
		// or extension annotations are literal data and must never become refs.
		for _, keyword := range []string{"properties", "patternProperties", "dependentSchemas"} {
			if children, ok := node[keyword].(map[string]any); ok {
				for key, child := range children {
					compact, err := intern(child, false)
					if err != nil {
						return nil, err
					}
					children[key] = compact
				}
			}
		}
		for _, keyword := range []string{"items", "additionalProperties", "not", "contains", "propertyNames", "if", "then", "else", "unevaluatedItems", "unevaluatedProperties", "contentSchema"} {
			if child, ok := node[keyword]; ok {
				compact, err := intern(child, false)
				if err != nil {
					return nil, err
				}
				node[keyword] = compact
			}
		}
		for _, keyword := range []string{"allOf", "anyOf", "oneOf", "prefixItems"} {
			if children, ok := node[keyword].([]any); ok {
				for index, child := range children {
					compact, err := intern(child, false)
					if err != nil {
						return nil, err
					}
					children[index] = compact
				}
			}
		}
		if root {
			return node, nil
		}
		// Canonical JSON includes every assertion and annotation. Child refs make
		// this bottom-up encoding proportional to the expanded schema's size.
		payload, err := json.Marshal(node)
		if err != nil {
			return nil, err
		}
		// As in automatic schema generation, tiny nodes are cheaper inline.
		if len(payload) < 160 {
			return node, nil
		}
		digest := sha256.Sum256(payload)
		key := hex.EncodeToString(digest[:])
		if _, exists := definitions[key]; !exists {
			definitions[key] = node
		}
		return map[string]any{"$ref": "#/$defs/" + key}, nil
	}
	if _, err := intern(document, true); err != nil {
		return nil, err
	}
	if len(definitions) > 0 {
		document["$defs"] = definitions
	}
	return document, nil
}
