package analysis

import (
	"context"
	"fmt"
	"github.com/lazysnail1024/k8s-crd-toolkit/edit"
	"github.com/lazysnail1024/k8s-crd-toolkit/source"
	"github.com/lazysnail1024/k8s-crd-toolkit/yamlview"
	"github.com/lazysnail1024/zed-argo-workflows/compat"
	"github.com/lazysnail1024/zed-argo-workflows/internal/workspace"
	"strings"
)

// ConfigPin changes only an exact block mapping in an already validated config.
// Inheritance is valid: no synthetic diagnostic is created for this explicit action.
func ConfigPin(ctx context.Context, s source.Snapshot, w workspace.Workspace, override string, requested Range) (Fix, error) {
	view, err := parseView(ctx, s)
	if err != nil {
		return Fix{}, err
	}
	docs := view.Documents()
	if len(docs) != 1 || !docs[0].Complete() {
		return Fix{}, fmt.Errorf("configuration is not exact")
	}
	root := docs[0].Root()
	var safe func(yamlview.Node) bool
	safe = func(n yamlview.Node) bool {
		if n.Kind() == yamlview.Alias || n.Damaged() || strings.HasPrefix(n.Raw(), "&") || strings.HasPrefix(n.Raw(), "!") {
			return false
		}
		for _, m := range n.Members() {
			if stringValue(m.Key) == "<<" || !singleLiteral(m.Key) || !safe(m.Value) {
				return false
			}
		}
		for _, v := range n.Elements() {
			if !safe(v) {
				return false
			}
		}
		return true
	}
	if root.Kind() != yamlview.Mapping || !safe(root) {
		return Fix{}, fmt.Errorf("configuration is not exact")
	}
	target := root
	version := w.Config.TargetVersion
	scope := "root"
	offset, err := s.Offset(source.Position{Line: requested.Start.Line, UTF16Column: requested.Start.Character})
	if err != nil {
		return Fix{}, err
	}
	for _, item := range member(root, "scopes").Elements() {
		if offset >= item.Span().Start && offset <= item.Span().End {
			target = item
			scope = stringValue(member(item, "name"))
			if v := stringValue(member(item, "targetVersion")); v != "" {
				version = v
			}
			break
		}
	}
	if override != "" {
		version = override
	}
	if !compat.Accepted(version) || compat.Resolve(version) != nil || target.Kind() != yamlview.Mapping || strings.HasPrefix(target.Raw(), "{") {
		return Fix{}, fmt.Errorf("no resolved exact pin")
	}
	old := member(target, "targetVersion")
	if stringValue(old) == version {
		return Fix{}, fmt.Errorf("already pinned")
	}
	plan := edit.Plan{BaseRevision: s.Revision()}
	if old.Kind() != yamlview.Empty {
		var ok bool
		plan, ok = scalarPlan(s, old, version)
		if !ok {
			return Fix{}, fmt.Errorf("pin location is not a literal")
		}
	} else {
		text := s.Text()
		ending := "\n"
		if strings.Contains(text, "\r\n") {
			ending = "\r\n"
		}
		at := len(text)
		// A stream terminator belongs after the mapping, not before a new pin.
		// Scan only the suffix beyond this exact mapping; block scalar content
		// and adjacent mappings cannot be mistaken for a document boundary.
		if target.Span() == root.Span() {
			start := strings.LastIndex(text[:root.Span().End], "\n") + 1
			for start < len(text) {
				end := strings.IndexByte(text[start:], '\n')
				if end < 0 {
					end = len(text) - start
				}
				line := strings.TrimSuffix(text[start:start+end], "\r")
				if strings.HasPrefix(line, "...") && (len(line) == 3 || line[3] == ' ' || line[3] == '\t') {
					at = start
					break
				}
				start += end + 1
			}
		}
		indent := ""
		if target.Span() != root.Span() {
			members := target.Members()
			if len(members) == 0 {
				return Fix{}, fmt.Errorf("empty scope")
			}
			first, _ := s.Position(members[0].Key.Span().Start)
			indent = strings.Repeat(" ", int(first.UTF16Column))
			at = target.Span().End
			if at > 0 && text[at-1] != '\n' {
				if next := strings.IndexByte(text[at:], '\n'); next >= 0 {
					at += next + 1
				} else {
					at = len(text)
				}
			}
		}
		prefix := ""
		if at > 0 && text[at-1] != '\n' {
			prefix = ending
		}
		plan.Replacements = []edit.Replacement{{Span: source.Span{Start: at, End: at}, NewText: prefix + indent + "targetVersion: \"" + version + "\"" + ending}}
	}
	next, err := edit.Apply(s, s.Revision()+1, plan)
	if err != nil {
		return Fix{}, err
	}
	changed, err := parseView(ctx, next)
	if err != nil || len(changed.Documents()) != 1 || !changed.Documents()[0].Complete() {
		return Fix{}, fmt.Errorf("pin must preserve one complete configuration document")
	}
	persisted := changed.Documents()[0].Root()
	if target.Span() != root.Span() {
		found := false
		for _, item := range member(persisted, "scopes").Elements() {
			if stringValue(member(item, "name")) == scope {
				persisted = item
				found = true
				break
			}
		}
		if !found {
			return Fix{}, fmt.Errorf("pin lost its target scope")
		}
	}
	if stringValue(member(persisted, "targetVersion")) != version {
		return Fix{}, fmt.Errorf("pin was not persisted in the target mapping")
	}
	return Fix{Title: "FIX_PIN_VERSION: " + scope + " = " + version, Code: "FIX_PIN_VERSION", Plan: plan}, nil
}
