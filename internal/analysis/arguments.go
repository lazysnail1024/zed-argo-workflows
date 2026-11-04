package analysis

import (
	"strconv"
	"strings"
)

// The locked workflow catalog contains exact 3.x versions.
func workflowVersionAtLeast(version string, minor, patch int) bool {
	parts := strings.Split(version, ".")
	if len(parts) != 3 || parts[0] != "3" {
		return false
	}
	m, e1 := strconv.Atoi(parts[1])
	p, e2 := strconv.Atoi(parts[2])
	return e1 == nil && e2 == nil && (m > minor || m == minor && p >= patch)
}

// Argument validity is independent of whether any callee declares this name.
func argumentValueError(argument any, version string, templateValidation bool) (string, bool) {
	name, _ := queryMember(argument, "name").(string)
	from := queryMember(argument, "valueFrom")
	if from != nil && (workflowVersionAtLeast(version, 5, 5) || strings.HasPrefix(version, "3.4.") && workflowVersionAtLeast(version, 4, 17)) {
		allowed := argumentValue(from, "configMapKeyRef", "supplied")
		event, _ := queryMember(from, "event").(string)
		allowed = allowed || event != ""
		forbidden := false
		for _, field := range []string{"path", "jsonPath", "parameter", "expression"} {
			text, _ := queryMember(from, field).(string)
			forbidden = forbidden || text != ""
		}
		if !allowed || forbidden {
			return "argument parameter " + name + " has a valueFrom form that is not allowed", true
		}
	}
	if templateValidation && workflowVersionAtLeast(version, 2, 4) {
		return "", false
	}
	if queryMember(argument, "value") == nil && (!workflowVersionAtLeast(version, 2, 0) || from == nil) {
		return "argument parameter " + name + " requires value or an allowed valueFrom", false
	}
	return "", false
}

func argumentValue(node any, fields ...string) bool {
	for _, field := range fields {
		if queryMember(node, field) != nil {
			return true
		}
	}
	return false
}
func missingArguments(version string, callee any, arguments ...any) []string {
	missing := []string{}
	for _, kind := range []string{"parameters", "artifacts"} {
		selected := map[string]any{}
		for _, args := range arguments {
			values, _ := queryMember(args, kind).([]any)
			for _, arg := range values {
				name, _ := queryMember(arg, "name").(string)
				if _, exists := selected[name]; exists {
					continue
				}
				selected[name] = arg
			}
		}
		inputs, _ := queryMember(queryMember(callee, "inputs"), kind).([]any)
		for _, input := range inputs {
			name, _ := queryMember(input, "name").(string)
			arg, supplied := selected[name]
			if name == "" || kind == "artifacts" && argumentValue(arg, "from", "fromExpression", "s3", "artifactory", "git", "http", "raw", "oss", "gcs", "azure", "hdfs") {
				continue
			}
			optional := false
			if kind == "parameters" {
				optional = argumentValue(input, "default", "value", "valueFrom")
				value := queryMember(arg, "value")
				literal, _ := value.(string)
				if workflowVersionAtLeast(version, 7, 16) && literal == "__argo-internal.absent-optional-output__" {
					// An exact absent-output marker leaves the input fallback intact.
				} else if value != nil {
					optional = true
				} else if supplied {
					if workflowVersionAtLeast(version, 4, 10) {
						optional = false
					}
					if workflowVersionAtLeast(version, 2, 5) && queryMember(queryMember(arg, "valueFrom"), "configMapKeyRef") != nil {
						optional = true
					}
				}
			} else {
				optional = queryMember(input, "optional") == true || argumentValue(input, "s3", "artifactory", "git", "http", "raw", "oss", "gcs", "azure", "hdfs")
			}
			if !optional {
				missing = append(missing, kind+"."+name)
			}
		}
	}
	return missing
}
