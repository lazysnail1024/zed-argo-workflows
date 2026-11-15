package analysis

import (
	"strings"
	"sync"

	"github.com/lazysnail1024/zed-argo-workflows/compat"
)

// These are the predicates used by real local/external decisions. Candidate
// explanation groups releases by this same finite signature; it does not keep a
// second implementation of the rules or run a complete analysis per release.
type semanticFeature uint16

const (
	argumentValueFrom semanticFeature = 1 << iota
	templateEmptyArguments
	argumentConfigMap
	dagOmitted
	workflowParametersJSON
	externalExit
	argumentOverwritesDefault
	argumentValueFromRestriction
	absentOutputMarker
)

var modeledRuleIDs = []string{
	"argument-value-from", "template-empty-arguments", "argument-configmap",
	"dag-omitted", "workflow-parameters-json", "external-exit",
	"argument-overwrites-default", "argument-value-from-restriction", "absent-output-marker",
}

func versionFeatures(version string) semanticFeature {
	if version == compat.DefaultVersion {
		// Existing automatic diagnostics permit forms available somewhere in the
		// catalog. This signature is deliberately not a purported real release.
		return argumentValueFrom | templateEmptyArguments | argumentConfigMap | dagOmitted | workflowParametersJSON | externalExit
	}
	var features semanticFeature
	for _, rule := range []struct {
		feature      semanticFeature
		minor, patch int
	}{
		{argumentValueFrom, 2, 0}, {templateEmptyArguments, 2, 4}, {argumentConfigMap, 2, 5},
		{dagOmitted, 3, 9}, {workflowParametersJSON, 4, 2}, {externalExit, 4, 6}, {argumentOverwritesDefault, 4, 10},
	} {
		if workflowVersionAtLeast(version, rule.minor, rule.patch) {
			features |= rule.feature
		}
	}
	if workflowVersionAtLeast(version, 5, 5) || strings.HasPrefix(version, "3.4.") && workflowVersionAtLeast(version, 4, 17) {
		features |= argumentValueFromRestriction
	}
	// ProcessArgs acquired the absent-output marker on two release lines. The
	// earlier 4.0.0–4.0.6 commits do not contain the 3.7.16 backport. See the
	// immutable source/function evidence in compat/modeled-rules.json.
	if workflowVersionAtLeast(version, 7, 16) && !versionBefore40Marker(version) {
		features |= absentOutputMarker
	}
	return features
}

func versionBefore40Marker(version string) bool {
	switch version {
	case "4.0.0", "4.0.1", "4.0.2", "4.0.3", "4.0.4", "4.0.5", "4.0.6":
		return true
	}
	return false
}

var featureIndex = sync.OnceValue(func() map[string]semanticFeature {
	index := map[string]semanticFeature{compat.DefaultVersion: versionFeatures(compat.DefaultVersion)}
	for _, version := range compat.SupportedVersions() {
		index[version] = versionFeatures(version)
	}
	return index
})

func workflowFeature(version string, feature semanticFeature) bool {
	features, ok := featureIndex()[version]
	if !ok {
		features = versionFeatures(version)
	}
	return features&feature != 0
}
