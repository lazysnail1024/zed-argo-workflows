package analysis

import (
	"context"
	"strconv"

	"github.com/lazysnail1024/k8s-crd-toolkit/schemaquery"
	jsonschema "github.com/santhosh-tekuri/jsonschema/v6"
)

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

// Selecting the release catalog is Workflow policy; shared queries only
// understand the existing compiled JSON Schema graph and finite constraints.
func queryAutomatic(ctx context.Context, schema *jsonschema.Schema, value any, active []string) (*jsonschema.Schema, error) {
	possible, err := schemaquery.SelectShapes(ctx, schema.AnyOf, value, active, schemaquery.Limits{})
	if err != nil {
		return nil, err
	}
	if len(possible) == 0 || len(possible) == len(schema.AnyOf) {
		return schema, nil
	}
	selected := *schema
	selected.AnyOf = possible
	return &selected, nil
}
