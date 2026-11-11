package analysis

import (
	"context"
	"errors"
	"strconv"
	"strings"

	"github.com/lazysnail1024/k8s-crd-toolkit/schemaquery"
	"github.com/lazysnail1024/k8s-crd-toolkit/source"
	"github.com/lazysnail1024/k8s-crd-toolkit/yamlvalue"
	"github.com/lazysnail1024/k8s-crd-toolkit/yamlview"
	"github.com/lazysnail1024/zed-argo-workflows/compat"
	"github.com/lazysnail1024/zed-argo-workflows/internal/assets"
	jsonschema "github.com/santhosh-tekuri/jsonschema/v6"
)

type fieldContext struct {
	schema                                 *jsonschema.Schema
	path                                   []string
	value                                  any
	span                                   source.Span
	key                                    bool
	shared                                 bool
	name, prefix, before, after, api, kind string
	existing                               map[string]bool
}

// Recover only the current token in a temporary snapshot. The original snapshot
// owns all returned edits/ranges; the marker never reaches a workspace or cache.
func queryField(ctx context.Context, snapshot source.Snapshot, version string, pos source.Position) (fieldContext, error) {
	offset, err := snapshot.Offset(pos)
	if err != nil {
		return fieldContext{}, err
	}
	view, err := parseView(ctx, snapshot)
	if err != nil {
		return fieldContext{}, err
	}
	lineStart := strings.LastIndexByte(snapshot.Text()[:offset], '\n') + 1
	flowDepth := 0
	for _, doc := range view.Documents() {
		flowDepth += queryFlowDepth(doc.Root(), lineStart)
	}
	span, key, prefix, before, after, ok := queryToken(snapshot.Text(), offset, flowDepth)
	if !ok {
		return fieldContext{}, nil
	}
	shared := false
	keyMarker := "__argo_query_key__"
	for _, doc := range view.Documents() {
		if span.Start >= doc.Span().Start && span.End <= doc.Span().End {
			original, candidate := snapshot, doc
			if key && after == ": " && !doc.Complete() {
				// Make only the unfinished key syntactically complete. Keep its
				// original spelling so projection still catches duplicate keys,
				// tags and aliases before replacing the active token with a marker.
				text := snapshot.Text()[:span.End] + ": null" + snapshot.Text()[span.End:]
				original, err = source.New(snapshot.URI(), snapshot.Revision(), text)
				if err != nil {
					return fieldContext{}, err
				}
				recovered, err := yamlview.Parse(ctx, original)
				if err != nil {
					return fieldContext{}, err
				}
				for _, recoveredDoc := range recovered.Documents() {
					if span.Start >= recoveredDoc.Span().Start && span.End <= recoveredDoc.Span().End {
						candidate = recoveredDoc
						break
					}
				}
			}
			projected, err := yamlvalue.Project(ctx, original, candidate, yamlvalue.Options{MaxNodes: 100000, MaxVisits: 100000, RejectExplicitTags: true})
			if err != nil {
				return fieldContext{}, err
			}
			shared = sharedOrigin(projected.Shared, span)
			if key {
				keyMarker = queryKeyMarker(ctx, doc.Root())
			}
		}
	}
	for attempt := 0; attempt < 2; attempt++ {
		marker := "__argo_query_value__"
		replacement := before + strconv.Quote(marker) + after
		if key {
			marker = keyMarker
			replacement = marker
			if after == ": " {
				replacement += ": null"
			}
		}
		text := snapshot.Text()[:span.Start] + replacement + snapshot.Text()[span.End:]
		temporary, err := source.New(snapshot.URI(), snapshot.Revision(), text)
		if err != nil {
			return fieldContext{}, err
		}
		// Artificial tokens have a separate result budget and cannot read or
		// populate the invocation's immutable snapshot/projection handoff.
		queryCtx := context.WithValue(WithLogicalResult(ctx), invocationKey{}, (*invocation)(nil))
		view, err := yamlview.Parse(queryCtx, temporary)
		if err != nil {
			return fieldContext{}, err
		}
		retryValue := false
		for _, doc := range view.Documents() {
			root := doc.Root()
			path, name, found := markerPath(queryCtx, root, marker, span.Start+len(before), nil)
			if !found {
				continue
			}
			value, err := project(queryCtx, temporary, doc, version)
			if err != nil {
				return fieldContext{}, err
			}
			api, _ := queryMember(value, "apiVersion").(string)
			kind, _ := queryMember(value, "kind").(string)
			actualName := name
			if key {
				actualName = strings.Trim(snapshot.Text()[span.Start:span.End], "\"'")
			}
			if actualName == "apiVersion" || actualName == "kind" {
				original, parseErr := parseView(ctx, snapshot)
				if parseErr != nil {
					return fieldContext{}, parseErr
				}
				for _, prior := range original.Documents() {
					if offset >= prior.Span().Start && offset <= prior.Span().End {
						originalValue, err := project(WithLogicalResult(ctx), snapshot, prior, version)
						if err != nil {
							return fieldContext{}, err
						}
						api, _ = queryMember(originalValue, "apiVersion").(string)
						kind, _ = queryMember(originalValue, "kind").(string)
					}
				}
			}
			if !owned(api, kind) {
				continue
			}
			schema, err := assets.Compile(ctx, version, api, kind)
			if errors.Is(err, compat.ErrSchemaUnavailable) {
				return fieldContext{}, nil
			}
			if err != nil {
				return fieldContext{}, err
			}
			if version == compat.DefaultVersion {
				activePath := path
				if key {
					activePath = append(append([]string{}, path...), marker)
				}
				schema, err = queryAutomatic(ctx, schema, value, activePath)
				if err != nil {
					return fieldContext{}, err
				}
			}
			field := fieldContext{shared: shared, schema: schema, path: path, value: value, span: span, key: key, name: name, prefix: prefix, before: before, after: after, api: api, kind: kind, existing: map[string]bool{}}
			parentValue := value
			parentPath := path
			if !key && len(parentPath) > 0 {
				parentPath = parentPath[:len(parentPath)-1]
			}
			for _, part := range parentPath {
				parentValue = queryMember(parentValue, part)
			}
			if properties, ok := parentValue.(map[string]any); ok {
				for k := range properties {
					if k != marker {
						field.existing[k] = true
					}
				}
			}
			if key {
				field.name = strings.Trim(snapshot.Text()[span.Start:span.End], "\"'")
				if field.name != "" {
					field.existing[field.name] = true
				}
				// A first sequence token can be either an object key or a scalar item.
				query, err := schemaquery.Lookup(ctx, schema, path, value, schemaquery.Limits{})
				if err != nil {
					return fieldContext{}, err
				}
				hasFields := false
				for _, n := range query.Nodes {
					if len(n.Properties) > 0 {
						hasFields = true
					}
				}
				sequenceItem := false
				if len(path) > 0 {
					_, e := strconv.Atoi(path[len(path)-1])
					sequenceItem = e == nil
				}
				if !hasFields && attempt == 0 && after == ": " && sequenceItem {
					key = false
					retryValue = true
					before = ""
					after = ""
					break
				}
			}
			return field, nil
		}
		if retryValue {
			continue
		}
		break
	}
	return fieldContext{}, ctx.Err()
}

// Choose a marker absent from decoded source keys, including quoted/escaped
// names and keys supplied through merge anchors. Raw spelling is not identity.
func queryKeyMarker(ctx context.Context, root yamlview.Node) string {
	keys := map[string]bool{}
	var collect func(yamlview.Node)
	collect = func(node yamlview.Node) {
		if ctx.Err() != nil {
			return
		}
		for _, member := range node.Members() {
			keys[stringValue(member.Key)] = true
			collect(member.Value)
		}
		for _, child := range node.Elements() {
			collect(child)
		}
	}
	collect(root)
	const base = "__argo_query_key__"
	marker := base
	for suffix := 1; keys[marker]; suffix++ {
		marker = base + strconv.Itoa(suffix)
	}
	return marker
}

func markerPath(ctx context.Context, node yamlview.Node, marker string, markerStart int, path []string) ([]string, string, bool) {
	if ctx.Err() != nil {
		return nil, "", false
	}
	if node.Kind() == yamlview.Mapping {
		for _, m := range node.Members() {
			name := stringValue(m.Key)
			if name == marker && m.Key.Span().Start == markerStart {
				return path, name, true
			}
			next := append(append([]string(nil), path...), name)
			if m.Value.Kind() == yamlview.Scalar && m.Value.Span().Start == markerStart && stringValue(m.Value) == marker {
				return next, name, true
			}
			if p, k, ok := markerPath(ctx, m.Value, marker, markerStart, next); ok {
				return p, k, true
			}
		}
	}
	if node.Kind() == yamlview.Sequence {
		for i, n := range node.Elements() {
			next := append(append([]string(nil), path...), strconv.Itoa(i))
			if n.Kind() == yamlview.Scalar && n.Span().Start == markerStart && stringValue(n) == marker {
				return next, "", true
			}
			if p, k, ok := markerPath(ctx, n, marker, markerStart, next); ok {
				return p, k, true
			}
		}
	}
	return nil, "", false
}

// Use CST collection boundaries to inherit flow context across physical lines.
// Braces inside comments, quoted values or block scalars are not collections.
func queryFlowDepth(node yamlview.Node, lineStart int) int {
	span := node.Span()
	if span.Start >= lineStart || span.End < lineStart {
		return 0
	}
	depth := 0
	raw := node.Raw()
	if (node.Kind() == yamlview.Mapping || node.Kind() == yamlview.Sequence) && len(raw) > 0 && (raw[0] == '{' || raw[0] == '[') {
		depth++
	}
	for _, member := range node.Members() {
		depth += queryFlowDepth(member.Value, lineStart)
	}
	for _, element := range node.Elements() {
		depth += queryFlowDepth(element, lineStart)
	}
	return depth
}

func queryToken(text string, offset, depth int) (source.Span, bool, string, string, string, bool) {
	start := strings.LastIndexByte(text[:offset], '\n') + 1
	end := len(text)
	if n := strings.IndexByte(text[offset:], '\n'); n >= 0 {
		end = offset + n
	}
	line := text[start:end]
	cursor := offset - start
	quote := byte(0)
	colon := -1
	segment := 0
	comment := len(line)
	for i := 0; i < len(line); i++ {
		c := line[i]
		if quote != 0 {
			if quote == '"' && c == '\\' {
				i++
				continue
			}
			if c == quote {
				if quote == '\'' && i+1 < len(line) && line[i+1] == '\'' {
					i++
					continue
				}
				quote = 0
			}
			continue
		}
		if c == '#' && (i == 0 || line[i-1] == ' ' || line[i-1] == '\t' || line[i-1] == ':') {
			comment = i
			break
		}
		if c == '\'' || c == '"' {
			quote = c
			continue
		}
		if i >= cursor {
			continue
		}
		switch c {
		case '{', '[':
			depth++
			segment = i + 1
			colon = -1
		case '}', ']':
			depth--
			segment = i + 1
			colon = -1
		case ',':
			if depth > 0 {
				segment = i + 1
				colon = -1
			}
		case ':':
			if colon < 0 {
				colon = i
			}
		}
	}
	if cursor > comment {
		return source.Span{}, false, "", "", "", false
	}
	key := colon < 0
	if cursor == comment && comment < len(line) && key {
		return source.Span{}, false, "", "", "", false
	}
	a, b := cursor, cursor
	if key {
		a = segment
		for a < cursor && (line[a] == ' ' || line[a] == '\t') {
			a++
		}
		if a+1 < cursor && line[a] == '-' && (line[a+1] == ' ' || line[a+1] == '\t') {
			a += 2
			for a < cursor && (line[a] == ' ' || line[a] == '\t') {
				a++
			}
		}
		b = a
		quote = 0
		for b < comment {
			c := line[b]
			if quote != 0 {
				if quote == '"' && c == '\\' {
					b += 2
					continue
				}
				if c == quote {
					quote = 0
				}
				b++
				continue
			}
			if c == '\'' || c == '"' {
				quote = c
				b++
				continue
			}
			if c == ':' || c == ',' || c == '}' || c == ']' || c == '\r' {
				break
			}
			b++
		}
		for b > a && (line[b-1] == ' ' || line[b-1] == '\t') {
			b--
		}
		if cursor < a || cursor > b && b > a {
			return source.Span{}, false, "", "", "", false
		}
	} else {
		a = colon + 1
		for a < comment && (line[a] == ' ' || line[a] == '\t') {
			a++
		}
		if a > cursor {
			a = cursor
		}
		b = a
		quote = 0
		for b < comment {
			c := line[b]
			if quote != 0 {
				if quote == '"' && c == '\\' {
					b += 2
					continue
				}
				if c == quote {
					quote = 0
				}
				b++
				continue
			}
			if c == '\'' || c == '"' {
				quote = c
				b++
				continue
			}
			if c == '\r' || depth > 0 && (c == ',' || c == '}' || c == ']') {
				break
			}
			b++
		}
		for b > a && (line[b-1] == ' ' || line[b-1] == '\t') {
			b--
		}
		if cursor > b && b > a {
			return source.Span{}, false, "", "", "", false
		}
	}
	if b > len(line) {
		b = len(line)
	}
	if a > b {
		return source.Span{}, false, "", "", "", false
	}
	prefix := ""
	if key && cursor >= a && cursor <= b {
		prefix = strings.TrimLeft(line[a:cursor], "\"'")
	}
	before, after := "", ""
	if key {
		next := b
		for next < len(line) && (line[next] == ' ' || line[next] == '\t') {
			next++
		}
		if next == len(line) || line[next] != ':' {
			after = ": "
		}
	} else {
		if a > 0 && line[a-1] == ':' {
			before = " "
		}
		if b < len(line) && line[b] == '#' {
			after = " "
		}
	}
	return source.Span{Start: start + a, End: start + b}, key, prefix, before, after, true
}
