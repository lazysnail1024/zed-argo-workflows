// Package helmsource recognizes a deliberately small, read-only Helm subset.
// It never evaluates templates. Projection keeps every original byte offset.
package helmsource

import (
	"context"
	"github.com/lazysnail1024/k8s-crd-toolkit/source"
	"github.com/lazysnail1024/k8s-crd-toolkit/yamlview"
	"strings"
)

type action struct {
	start, end int
	body       string
}

func actions(text string) ([]action, bool) {
	var out []action
	for at := 0; at < len(text); {
		i := strings.Index(text[at:], "{{")
		if i < 0 {
			break
		}
		i += at
		j := i + 2
		var quote byte
		for ; j < len(text)-1; j++ {
			c := text[j]
			if quote != 0 {
				if c == '\\' && quote == '"' {
					j++
					continue
				}
				if c == quote {
					quote = 0
				}
				continue
			}
			if c == '`' || c == '"' || c == '\'' {
				quote = c
				continue
			}
			if c == '}' && text[j+1] == '}' {
				break
			}
		}
		if j >= len(text)-1 {
			return out, false
		}
		body := strings.TrimSpace(text[i+2 : j])
		body = strings.TrimSpace(strings.TrimSuffix(strings.TrimPrefix(body, "-"), "-"))
		out = append(out, action{i, j + 2, body})
		at = j + 2
	}
	return out, true
}
func argo(body string) bool {
	for _, p := range []string{"workflow.", "inputs.", "outputs.", "tasks.", "steps.", "item", "=", "pod.", "retries"} {
		if strings.HasPrefix(body, p) {
			return true
		}
	}
	return false
}
func HasActions(text string) bool {
	aa, ok := actions(text)
	if !ok {
		return strings.Contains(text, "{{")
	}
	for _, a := range aa {
		if !argo(a.body) {
			return true
		}
	}
	return false
}

// Project accepts literal raw-string emission and scalar Values/Release/Chart
// accesses. Control flow and helpers can emit YAML structure, so are refused.
// Unknown scalars remain Argo-dynamic values, never invented symbol names.
func Project(ctx context.Context, s source.Snapshot) (source.Snapshot, bool) {
	aa, ok := actions(s.Text())
	if !ok {
		return s, false
	}
	text := s.Text()
	raw := []byte(text)
	for _, a := range aa {
		if argo(a.body) {
			continue
		}
		if strings.ContainsAny(text[a.start:a.end], "\r\n") {
			return s, false
		}
		for _, r := range text[a.start:a.end] {
			if r > 127 {
				return s, false
			}
		}
		line := text[strings.LastIndex(text[:a.start], "\n")+1 : a.start]
		// A projection is only safe within a scalar on a mapping/sequence line.
		if !strings.Contains(line, ":") && !strings.Contains(line, "- ") {
			return s, false
		}
		var quoted byte
		for i := 0; i < len(line); i++ {
			if quoted != 0 {
				if line[i] == '\\' && quoted == '"' {
					i++
					continue
				}
				if line[i] == quoted {
					quoted = 0
				}
			} else if line[i] == '\'' || line[i] == '"' {
				quoted = line[i]
			}
		}
		for i := a.start; i < a.end; i++ {
			raw[i] = ' '
		}
		if len(a.body) >= 2 && a.body[0] == '`' && a.body[len(a.body)-1] == '`' {
			literal := a.body[1 : len(a.body)-1]
			if strings.ContainsAny(literal, "\r\n`") || quoted == 0 {
				return s, false
			}
			start := a.start + strings.Index(text[a.start:a.end], "`") + 1
			copy(raw[start:], literal)
		} else {
			body := a.body
			if !(strings.HasPrefix(body, ".Values.") || strings.HasPrefix(body, ".Release.") || strings.HasPrefix(body, ".Chart.")) {
				return s, false
			}
			// Only simple field accesses and the scalar quote filter are recognized.
			parts := strings.Split(body, "|")
			if len(parts) > 2 || len(parts) == 2 && strings.TrimSpace(parts[1]) != "quote" {
				return s, false
			}
			for _, r := range strings.TrimSpace(parts[0]) {
				if !(r == '.' || r == '_' || r >= 'a' && r <= 'z' || r >= 'A' && r <= 'Z' || r >= '0' && r <= '9') {
					return s, false
				}
			}
			replacement := "{{x}}"
			if quoted == 0 {
				replacement = "'{{x}}'"
			}
			if len(replacement) > a.end-a.start {
				return s, false
			}
			copy(raw[a.start:], replacement)
		}
	}
	projected, err := source.New(s.URI(), s.Revision(), string(raw))
	if err != nil {
		return s, false
	}
	view, err := yamlview.Parse(ctx, projected)
	if err != nil {
		return s, false
	}
	values := []source.Span{}
	valid := true
	var visit func(yamlview.Node, bool)
	visit = func(n yamlview.Node, value bool) {
		if n.Kind() == yamlview.Scalar {
			if value {
				values = append(values, n.Span())
			} else {
				for _, a := range aa {
					if a.start >= n.Span().Start && a.start < n.Span().End {
						valid = false
					}
				}
			}
		}
		for _, m := range n.Members() {
			visit(m.Key, false)
			visit(m.Value, true)
		}
		for _, e := range n.Elements() {
			visit(e, true)
		}
	}
	for _, doc := range view.Documents() {
		if !doc.Complete() {
			return s, false
		}
		visit(doc.Root(), false)
	}
	if !valid {
		return s, false
	}
	for _, a := range aa {
		if argo(a.body) {
			continue
		}
		inside := false
		for _, span := range values {
			if a.start >= span.Start && a.start < span.End {
				inside = true
				break
			}
		}
		if !inside {
			return s, false
		}
	}
	return projected, true
}

// Identity removes actions only to recognize a statically written GVK for an
// explicit render action. Its output must never drive navigation or validation.
func Identity(s source.Snapshot) (source.Snapshot, bool) {
	aa, ok := actions(s.Text())
	if !ok {
		return s, false
	}
	raw := []byte(s.Text())
	for _, a := range aa {
		for i := a.start; i < a.end; i++ {
			if raw[i] != '\r' && raw[i] != '\n' {
				raw[i] = ' '
			}
		}
	}
	result, err := source.New(s.URI(), s.Revision(), string(raw))
	return result, err == nil
}
