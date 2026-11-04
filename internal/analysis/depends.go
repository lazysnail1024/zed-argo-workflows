package analysis

import (
	"fmt"
	"strings"
	"unicode"

	"github.com/lazysnail1024/k8s-crd-toolkit/source"
)

// dependsOccurrence preserves each task token independently of version checks.
type dependsOccurrence struct {
	name   string
	result string
	span   source.Span
}

type dependsParser struct {
	text        string
	offset      int
	occurrences []dependsOccurrence
}

func (p *dependsParser) space() {
	for p.offset < len(p.text) && unicode.IsSpace(rune(p.text[p.offset])) {
		p.offset++
	}
}
func (p *dependsParser) take(token string) bool {
	p.space()
	if strings.HasPrefix(p.text[p.offset:], token) {
		p.offset += len(token)
		return true
	}
	return false
}
func (p *dependsParser) atom() error {
	if p.take("!") {
		return p.atom()
	}
	if p.take("(") {
		if err := p.or(); err != nil {
			return err
		}
		if !p.take(")") {
			return fmt.Errorf("missing closing parenthesis")
		}
		return nil
	}
	p.space()
	start := p.offset
	for p.offset < len(p.text) {
		c := p.text[p.offset]
		if c >= 'a' && c <= 'z' || c >= 'A' && c <= 'Z' || c >= '0' && c <= '9' || c == '-' || c == '_' {
			p.offset++
		} else {
			break
		}
	}
	if start == p.offset {
		return fmt.Errorf("expected task name")
	}
	occurrence := dependsOccurrence{name: p.text[start:p.offset], span: source.Span{Start: start, End: p.offset}}
	if p.take(".") {
		start = p.offset
		for p.offset < len(p.text) && unicode.IsLetter(rune(p.text[p.offset])) {
			p.offset++
		}
		suffix := p.text[start:p.offset]
		occurrence.result = suffix
		switch suffix {
		case "Succeeded", "Failed", "Errored", "Skipped", "Omitted", "Daemoned", "AnySucceeded", "AllFailed":
		default:
			return fmt.Errorf("unknown task result %q", suffix)
		}
	}
	p.occurrences = append(p.occurrences, occurrence)
	return nil
}
func (p *dependsParser) and() error {
	if err := p.atom(); err != nil {
		return err
	}
	for p.take("&&") {
		if err := p.atom(); err != nil {
			return err
		}
	}
	return nil
}
func (p *dependsParser) or() error {
	if err := p.and(); err != nil {
		return err
	}
	for p.take("||") {
		if err := p.and(); err != nil {
			return err
		}
	}
	return nil
}
func parseDepends(text string) ([]dependsOccurrence, error) {
	p := dependsParser{text: text}
	if err := p.or(); err != nil {
		return nil, err
	}
	p.space()
	if p.offset != len(text) {
		return nil, fmt.Errorf("unexpected depends token")
	}
	return p.occurrences, nil
}
