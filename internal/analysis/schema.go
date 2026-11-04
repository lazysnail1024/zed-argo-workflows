// Package analysis evaluates product-owned documents from immutable snapshots.
package analysis

import (
	"bytes"
	"context"
	"encoding/json"
	"github.com/lazysnail1024/k8s-crd-toolkit/source"
	"github.com/lazysnail1024/k8s-crd-toolkit/yamlview"
	"github.com/lazysnail1024/zed-argo-workflows/internal/assets"
	jsonschema "github.com/santhosh-tekuri/jsonschema/v6"
	"github.com/santhosh-tekuri/jsonschema/v6/kind"
	yaml "sigs.k8s.io/yaml"
	"sort"
	"strconv"
	"strings"
)

type Position struct {
	Line      uint32 `json:"line"`
	Character uint32 `json:"character"`
}
type Range struct {
	Start Position `json:"start"`
	End   Position `json:"end"`
}
type Diagnostic struct {
	URI          string `json:"uri"`
	Range        Range  `json:"range"`
	Code         string `json:"code"`
	Source       string `json:"source"`
	Severity     string `json:"severity"`
	Message      string `json:"message"`
	TargetID     string `json:"targetId"`
	PositionKind string `json:"positionKind"`
}
type Result struct {
	Diagnostics  []Diagnostic
	NotEvaluated []string
	Resources    []source.Span
	Completed    bool
}

func decode(raw string) (any, error) {
	data, err := yaml.YAMLToJSONStrict([]byte(raw))
	if err != nil {
		return nil, err
	}
	var value any
	d := json.NewDecoder(bytes.NewReader(data))
	d.UseNumber()
	err = d.Decode(&value)
	return value, err
}

// These plain ASCII scalars cannot be YAML numeric, null, boolean, tagged or
// quoted values. Everything outside this subset still uses strict decoding.
func plainString(raw string) bool {
	if raw == "" || !(raw[0] >= 'a' && raw[0] <= 'z' || raw[0] >= 'A' && raw[0] <= 'Z' || raw[0] == '_') {
		return false
	}
	for i := 0; i < len(raw); i++ {
		c := raw[i]
		if !(c >= 'a' && c <= 'z' || c >= 'A' && c <= 'Z' || c >= '0' && c <= '9' || c == '_' || c == '-' || c == '.' || c == '/') {
			return false
		}
	}
	switch strings.ToLower(raw) {
	case "y", "n", "yes", "no", "on", "off", "true", "false", "null":
		return false
	}
	return true
}
func stringValue(n yamlview.Node) string {
	if n.Kind() == yamlview.Scalar && plainString(n.Raw()) {
		return n.Raw()
	}
	v, err := decode(n.Raw())
	if err != nil {
		return ""
	}
	s, _ := v.(string)
	return s
}
func member(n yamlview.Node, key string) yamlview.Node {
	for _, m := range n.Members() {
		if stringValue(m.Key) == key {
			return m.Value
		}
	}
	return yamlview.Node{}
}
func owned(api, kind string) bool {
	if !strings.HasPrefix(api, "argoproj.io/") {
		return false
	}
	switch kind {
	case "Workflow", "WorkflowTemplate", "ClusterWorkflowTemplate", "CronWorkflow":
		return true
	}
	return false
}
func at(root yamlview.Node, path []string) yamlview.Node {
	node := root
	for _, part := range path {
		if node.Kind() == yamlview.Mapping {
			next := member(node, part)
			if next.Kind() == yamlview.Empty {
				return node
			}
			node = next
		} else if node.Kind() == yamlview.Sequence {
			index, err := strconv.Atoi(part)
			elements := node.Elements()
			if err != nil || index < 0 || index >= len(elements) {
				return node
			}
			node = elements[index]
		} else {
			return node
		}
	}
	return node
}
func AnalyzeSchema(ctx context.Context, snapshot source.Snapshot, version string) Result {
	ctx = WithInvocation(ctx)
	result := Result{Diagnostics: []Diagnostic{}, NotEvaluated: []string{}}
	view, err := parseView(ctx, snapshot)
	if err != nil {
		result.NotEvaluated = append(result.NotEvaluated, err.Error())
		return result
	}
	for _, doc := range view.Documents() {
		if ctx.Err() != nil {
			result.NotEvaluated = append(result.NotEvaluated, ctx.Err().Error())
			return result
		}
		if !doc.Complete() {
			result.NotEvaluated = append(result.NotEvaluated, "incomplete YAML document")
			continue
		}
		root := doc.Root()
		value, err := project(ctx, snapshot, doc, version)
		if err != nil {
			result.NotEvaluated = append(result.NotEvaluated, err.Error())
			continue
		}
		api, _ := queryMember(value, "apiVersion").(string)
		resourceKind, _ := queryMember(value, "kind").(string)
		if !owned(api, resourceKind) {
			continue
		}
		document, err := assets.Document(ctx, version, api, resourceKind)
		if err != nil {
			result.NotEvaluated = append(result.NotEvaluated, err.Error())
			continue
		}
		schema, err := assets.Compile(ctx, version, api, resourceKind)
		if err != nil {
			result.NotEvaluated = append(result.NotEvaluated, err.Error())
			continue
		}
		result.Diagnostics = append(result.Diagnostics, unknownFields(snapshot, root, value, document, version, sharedOrigins(ctx, snapshot, doc, version))...)
		result.Diagnostics = append(result.Diagnostics, localDiagnostics(ctx, snapshot, root, value, sharedOrigins(ctx, snapshot, doc, version), resourceKind, version)...)
		references, referenceErr := referenceDiagnostics(ctx, snapshot, root, value, sharedOrigins(ctx, snapshot, doc, version), resourceKind, version)
		result.Diagnostics = append(result.Diagnostics, references...)
		if referenceErr != nil {
			result.NotEvaluated = append(result.NotEvaluated, referenceErr.Error())
			continue
		}
		if err = schema.Validate(value); err != nil {
			validation, ok := err.(*jsonschema.ValidationError)
			if !ok {
				result.NotEvaluated = append(result.NotEvaluated, err.Error())
				continue
			}
			var report func(*jsonschema.ValidationError)
			report = func(v *jsonschema.ValidationError) {
				if len(v.Causes) > 0 {
					for _, cause := range v.Causes {
						report(cause)
					}
					return
				}
				code := "SCHEMA_VALIDATION"
				if _, ok := v.ErrorKind.(*kind.Type); ok {
					code = "SCHEMA_TYPE"
				}
				node, positionKind := location(root, v.InstanceLocation)
				if sharedOrigin(sharedOrigins(ctx, snapshot, doc, version), node.Span()) {
					positionKind = "derived"
				}
				start, e1 := snapshot.Position(node.Span().Start)
				end, e2 := snapshot.Position(node.Span().End)
				if e1 != nil || e2 != nil {
					result.NotEvaluated = append(result.NotEvaluated, "unmappable schema location")
					return
				}
				result.Diagnostics = append(result.Diagnostics, Diagnostic{URI: string(snapshot.URI()), Range: Range{Position{start.Line, start.UTF16Column}, Position{end.Line, end.UTF16Column}}, Code: code, Source: "argo-workflows", Severity: "Error", Message: v.Error(), TargetID: version, PositionKind: positionKind})
			}
			report(validation)
		}
		if ctx.Err() != nil {
			result.NotEvaluated = append(result.NotEvaluated, ctx.Err().Error())
			return result
		}
		result.Resources = append(result.Resources, root.Span())
	}
	result.Completed = ctx.Err() == nil && len(result.NotEvaluated) == 0
	sort.SliceStable(result.Diagnostics, func(i, j int) bool {
		a, b := result.Diagnostics[i], result.Diagnostics[j]
		if a.Range.Start.Line != b.Range.Start.Line {
			return a.Range.Start.Line < b.Range.Start.Line
		}
		if a.Range.Start.Character != b.Range.Start.Character {
			return a.Range.Start.Character < b.Range.Start.Character
		}
		if a.Code != b.Code {
			return a.Code < b.Code
		}
		return a.Message < b.Message
	})

	return result
}
