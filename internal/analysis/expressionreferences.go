package analysis

import (
	"context"
	"fmt"
	"github.com/expr-lang/expr/ast"
	"github.com/expr-lang/expr/file"
	"github.com/expr-lang/expr/parser"
	"github.com/expr-lang/expr/parser/lexer"
	"github.com/lazysnail1024/k8s-crd-toolkit/source"
)

// Bound recursive grammar constructs before invoking the pinned parser. Tokens
// distinguish operators from literal/comment text; wide comma-separated values
// remain independent. The scalar and syntax-depth limits match YAML projection.
func expressionTokens(ctx context.Context, text string) ([]lexer.Token, error) {
	if len(text) > 1024*1024 {
		return nil, fmt.Errorf("expression scalar exceeds 1 MiB")
	}
	if err := ctx.Err(); err != nil {
		return nil, err
	}
	tokens, err := lexer.Lex(file.NewSource(text))
	if err != nil {
		return nil, err
	}
	type nesting struct{ recursive, unary int }
	stack := []nesting{{}}
	for _, token := range tokens {
		if err := ctx.Err(); err != nil {
			return nil, err
		}
		at := len(stack) - 1
		if token.Is(lexer.Bracket, "(", "[", "{") {
			stack = append(stack, nesting{})
			if len(stack)-1 > 256 {
				return nil, fmt.Errorf("expression syntax depth exceeds 256")
			}
		} else if token.Is(lexer.Bracket, ")", "]", "}") {
			if len(stack) > 1 {
				stack = stack[:len(stack)-1]
			}
		} else if token.Is(lexer.Operator, ",") {
			stack[at] = nesting{}
		} else if token.Is(lexer.Operator, "let", "**", "^", "?") {
			stack[at].recursive++
			stack[at].unary = 0
		} else if token.Is(lexer.Operator, "!", "not", "+", "-") {
			stack[at].unary++
		} else {
			stack[at].unary = 0
		}
		recursive := 0
		for _, level := range stack {
			recursive += level.recursive + level.unary
		}
		if recursive > 256 {
			return nil, fmt.Errorf("expression syntax depth exceeds 256")
		}
	}
	return tokens, ctx.Err()
}

type expressionFrame struct {
	node   ast.Node
	depth  int
	bind   string
	unbind bool
}

// Walk once with lexical scope entry/exit frames. A let initializer sees the
// outer binding; only its body sees the new name. No recursive subtree rescans.
func expressionTree(ctx context.Context, root ast.Node) ([]ast.Node, map[ast.Node]bool, error) {
	nodes := []ast.Node{}
	shadowed := map[ast.Node]bool{}
	bindings := map[string]int{}
	stack := []expressionFrame{{node: root}}
	for len(stack) > 0 {
		if err := ctx.Err(); err != nil {
			return nil, nil, err
		}
		f := stack[len(stack)-1]
		stack = stack[:len(stack)-1]
		if f.bind != "" {
			if f.unbind {
				bindings[f.bind]--
			} else {
				bindings[f.bind]++
			}
			continue
		}
		if f.node == nil {
			continue
		}
		if f.depth > 256 {
			return nil, nil, fmt.Errorf("expression syntax depth exceeds 256")
		}
		nodes = append(nodes, f.node)
		push := func(n ast.Node) { stack = append(stack, expressionFrame{node: n, depth: f.depth + 1}) }
		switch n := f.node.(type) {
		case *ast.IdentifierNode:
			if bindings[n.Value] > 0 {
				shadowed[n] = true
			}
		case *ast.UnaryNode:
			push(n.Node)
		case *ast.BinaryNode:
			push(n.Right)
			push(n.Left)
		case *ast.ChainNode:
			push(n.Node)
		case *ast.MemberNode:
			push(n.Property)
			push(n.Node)
		case *ast.SliceNode:
			push(n.To)
			push(n.From)
			push(n.Node)
		case *ast.CallNode:
			for i := len(n.Arguments) - 1; i >= 0; i-- {
				push(n.Arguments[i])
			}
			push(n.Callee)
		case *ast.BuiltinNode:
			for i := len(n.Arguments) - 1; i >= 0; i-- {
				push(n.Arguments[i])
			}
		case *ast.ClosureNode:
			push(n.Node)
		case *ast.VariableDeclaratorNode:
			stack = append(stack, expressionFrame{bind: n.Name, unbind: true})
			push(n.Expr)
			stack = append(stack, expressionFrame{bind: n.Name})
			push(n.Value)
		case *ast.ConditionalNode:
			push(n.Exp2)
			push(n.Exp1)
			push(n.Cond)
		case *ast.ArrayNode:
			for i := len(n.Nodes) - 1; i >= 0; i-- {
				push(n.Nodes[i])
			}
		case *ast.MapNode:
			for i := len(n.Pairs) - 1; i >= 0; i-- {
				push(n.Pairs[i])
			}
		case *ast.PairNode:
			push(n.Value)
			push(n.Key)
		}
	}
	return nodes, shadowed, nil
}

// Expr locations count runes, while toolkit spans count UTF-8 bytes. Only AST
// member accesses contribute references; strings and comments are never scanned.
func expressionReferences(ctx context.Context, text string) ([]memberReference, error) {
	tokens, err := expressionTokens(ctx, text)
	if err != nil {
		return nil, err
	}
	tree, err := parser.Parse(text)
	if err != nil {
		return nil, err
	}
	if err := ctx.Err(); err != nil {
		return nil, err
	}
	offsets := []int{}
	for at := range text {
		offsets = append(offsets, at)
	}
	offsets = append(offsets, len(text))
	span := func(loc file.Location) source.Span {
		return source.Span{Start: offsets[loc.From], End: offsets[loc.To]}
	}
	dots := map[int]int{}
	for i, token := range tokens {
		if i > 0 && tokens[i-1].Is(lexer.Operator, ".", "?.") {
			dots[token.From] = offsets[tokens[i-1].From]
		}
	}
	nodes, shadowed, err := expressionTree(ctx, tree.Node)
	if err != nil {
		return nil, err
	}
	consumed := map[ast.Node]bool{}
	for _, node := range nodes {
		if err := ctx.Err(); err != nil {
			return nil, err
		}
		switch n := node.(type) {
		case *ast.MemberNode:
			consumed[n.Node] = true
		case *ast.ChainNode:
			consumed[n.Node] = true
		}
	}
	var chain func(ast.Node) (memberReference, bool)
	chain = func(node ast.Node) (memberReference, bool) {
		switch n := node.(type) {
		case *ast.IdentifierNode:
			if shadowed[node] {
				return memberReference{}, false
			}
			return memberReference{parts: []string{n.Value}, spans: []source.Span{span(n.Location())}, dots: []int{-1}}, true
		case *ast.ChainNode:
			return chain(n.Node)
		case *ast.MemberNode:
			ref, ok := chain(n.Node)
			if !ok {
				return ref, false
			}
			name, location, dot := "*", source.Span{}, -1
			if property, ok := n.Property.(*ast.StringNode); ok {
				name, location = property.Value, span(property.Location())
				raw := text[location.Start:location.End]
				if len(raw) >= 2 && (raw[0] == '\'' || raw[0] == '"' || raw[0] == '`') {
					location.Start++
					location.End--
				} else if at, ok := dots[property.Location().From]; ok {
					dot = at
				}
			}
			ref.dynamic = ref.dynamic || name == "*"
			ref.parts = append(ref.parts, name)
			ref.spans = append(ref.spans, location)
			ref.dots = append(ref.dots, dot)
			return ref, true
		}
		return memberReference{}, false
	}
	refs := []memberReference{}
	for _, node := range nodes {
		if err := ctx.Err(); err != nil {
			return nil, err
		}
		if consumed[node] {
			continue
		}
		if ref, ok := chain(node); ok {
			refs = append(refs, ref)
		}
	}
	return refs, nil
}
