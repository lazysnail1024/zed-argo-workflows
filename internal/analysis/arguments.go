package analysis

import (
	"github.com/lazysnail1024/zed-argo-workflows/compat"
	"strconv"
	"strings"
)

// Feature introduction gates are relative to the 3.x baseline. Automatic
// editing permits features from any supported release; exact pins stay strict.
func workflowVersionAtLeast(version string, minor, patch int) bool {
	if version == compat.DefaultVersion {
		return true
	}
	parts := strings.Split(version, ".")
	if len(parts) != 3 {
		return false
	}
	major, e0 := strconv.Atoi(parts[0])
	m, e1 := strconv.Atoi(parts[1])
	p, e2 := strconv.Atoi(parts[2])
	return e0 == nil && e1 == nil && e2 == nil && (major > 3 || major == 3 && (m > minor || m == minor && p >= patch))
}

// Argument validity is independent of whether any callee declares this name.
func argumentValueDecision(argument any, version string, templateValidation bool) (string, bool, []string) {
	name, _ := queryMember(argument, "name").(string)
	from := queryMember(argument, "valueFrom")
	if from != nil && workflowFeature(version, argumentValueFromRestriction) {
		allowed := argumentValue(from, "configMapKeyRef", "supplied")
		event, _ := queryMember(from, "event").(string)
		allowed = allowed || event != ""
		forbidden := false
		for _, field := range []string{"path", "jsonPath", "parameter", "expression"} {
			text, _ := queryMember(from, field).(string)
			forbidden = forbidden || text != ""
		}
		if !allowed || forbidden {
			return "argument parameter " + name + " has a valueFrom form that is not allowed", true, []string{"argument-value-from-restriction"}
		}
	}
	if templateValidation && workflowFeature(version, templateEmptyArguments) {
		return "", false, nil
	}
	if queryMember(argument, "value") == nil && (!workflowFeature(version, argumentValueFrom) || from == nil) {
		rules := []string{}
		if from != nil {
			rules = append(rules, "argument-value-from")
		}
		if templateValidation {
			rules = append(rules, "template-empty-arguments")
		}
		return "argument parameter " + name + " requires value or an allowed valueFrom", false, rules
	}
	return "", false, nil
}

func argumentValue(node any, fields ...string) bool {
	for _, field := range fields {
		if queryMember(node, field) != nil {
			return true
		}
	}
	return false
}

type missingArgument struct {
	name    string
	ruleIDs []string
}

func missingArguments(version string, callee any, arguments ...any) []missingArgument {
	missing := []missingArgument{}
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
			rules := []string{}
			if kind == "parameters" {
				optional = argumentValue(input, "default", "value", "valueFrom")
				value := queryMember(arg, "value")
				literal, _ := value.(string)
				if workflowFeature(version, absentOutputMarker) && literal == "__argo-internal.absent-optional-output__" {
					// An exact absent-output marker leaves the input fallback intact.
					rules = append(rules, "absent-output-marker")
				} else if value != nil {
					optional = true
				} else if supplied {
					if workflowFeature(version, argumentOverwritesDefault) {
						if optional {
							rules = append(rules, "argument-overwrites-default")
						}
						optional = false
					}
					if queryMember(queryMember(arg, "valueFrom"), "configMapKeyRef") != nil {
						if workflowFeature(version, argumentConfigMap) {
							optional = true
						} else {
							rules = append(rules, "argument-configmap")
						}
					}
				}
			} else {
				optional = queryMember(input, "optional") == true || argumentValue(input, "s3", "artifactory", "git", "http", "raw", "oss", "gcs", "azure", "hdfs")
			}
			if !optional {
				missing = append(missing, missingArgument{kind + "." + name, rules})
			}
		}
	}
	return missing
}
