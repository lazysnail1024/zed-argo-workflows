package analysis

import (
	"context"
	"github.com/lazysnail1024/k8s-crd-toolkit/source"
	"github.com/lazysnail1024/k8s-crd-toolkit/yamlview"
)

type Location struct {
	URI   string `json:"uri"`
	Range Range  `json:"range"`
}

func nodeLocation(s source.Snapshot, n yamlview.Node) Location {
	a, _ := s.Position(n.Span().Start)
	b, _ := s.Position(n.Span().End)
	return Location{string(s.URI()), Range{Position{a.Line, a.UTF16Column}, Position{b.Line, b.UTF16Column}}}
}
func DocumentSymbols(ctx context.Context, s source.Snapshot, version string) []map[string]any {
	result := []map[string]any{}
	for _, symbol := range DomainSymbols(ctx, s, version) {
		result = append(result, map[string]any{"name": symbol.Name, "kind": symbol.Kind, "detail": symbol.Container, "range": symbol.Location.Range, "selectionRange": symbol.Location.Range})
	}
	return result
}
