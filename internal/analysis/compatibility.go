package analysis

import (
	"context"
	"encoding/json"
	"fmt"
	"maps"
	"sort"
	"sync"

	"github.com/lazysnail1024/k8s-crd-toolkit/source"
	"github.com/lazysnail1024/zed-argo-workflows/compat"
	"github.com/lazysnail1024/zed-argo-workflows/internal/assets"
	"github.com/lazysnail1024/zed-argo-workflows/internal/workspace"
	jsonschema "github.com/santhosh-tekuri/jsonschema/v6"
)

// Version sets are bounded by the immutable catalog, never workspace names.
// Only the wire projection expands them to ordered exact version strings.
type versionSet []uint64

func newVersionSet() versionSet {
	return make(versionSet, (len(compatibilityVersions().versions)+63)/64)
}
func (s versionSet) add(index int)           { s[index/64] |= 1 << (index % 64) }
func (s versionSet) contains(index int) bool { return s[index/64]&(1<<(index%64)) != 0 }
func (s versionSet) merge(other versionSet) {
	for i := range s {
		s[i] |= other[i]
	}
}
func (s versionSet) remove(other versionSet) {
	for i := range s {
		s[i] &^= other[i]
	}
}
func (s versionSet) intersect(other versionSet) versionSet {
	r := newVersionSet()
	for i := range s {
		r[i] = s[i] & other[i]
	}
	return r
}
func (s versionSet) strings() []string {
	r := []string{}
	for i, version := range compatibilityVersions().versions {
		if s.contains(i) {
			r = append(r, version)
		}
	}
	return r
}

type semanticGroup struct {
	version  string
	versions versionSet
}
type versionInventory struct {
	versions []string
	index    map[string]int
	groups   []semanticGroup
}

var compatibilityVersions = sync.OnceValue(func() versionInventory {
	versions := compat.SupportedVersions()
	inventory := versionInventory{versions: versions, index: map[string]int{}}
	groups := map[semanticFeature]int{}
	for i, version := range versions {
		inventory.index[version] = i
		feature := featureIndex()[version]
		group, ok := groups[feature]
		if !ok {
			group = len(inventory.groups)
			groups[feature] = group
			inventory.groups = append(inventory.groups, semanticGroup{version, make(versionSet, (len(versions)+63)/64)})
		}
		inventory.groups[group].versions.add(i)
	}
	return inventory
})

type CompatibilityExclusion struct {
	Code     string   `json:"code"`
	RuleIDs  []string `json:"ruleIds"`
	Message  string   `json:"message"`
	Location Location `json:"location"`
	Versions []string `json:"versions"`
	versions versionSet
}
type CompatibilityTarget struct {
	Version         string `json:"version"`
	SchemaAccepted  bool   `json:"schemaAccepted"`
	ModeledAccepted bool   `json:"modeledAccepted"`
}
type CompatibilityWork struct {
	SchemaBranches   int `json:"schemaBranches"`
	SemanticProfiles int `json:"semanticProfiles"`
}
type ResourceCompatibility struct {
	DocumentIndex      int                       `json:"documentIndex"`
	APIVersion         string                    `json:"apiVersion"`
	Kind               string                    `json:"kind"`
	Name               string                    `json:"name"`
	Location           Location                  `json:"location"`
	Evaluated          bool                      `json:"evaluated"`
	ModeledComplete    bool                      `json:"modeledComplete"`
	SchemaCandidates   []string                  `json:"schemaCandidates"`
	SemanticCandidates []string                  `json:"semanticCandidates"`
	Candidates         []string                  `json:"candidates"`
	Exclusions         []*CompatibilityExclusion `json:"exclusions"`
	Unknowns           []*Refusal                `json:"unknowns"`
	SelectedTarget     *CompatibilityTarget      `json:"selectedTarget,omitempty"`
	Work               CompatibilityWork         `json:"work"`
}
type CompatibilityExplanation struct {
	Label            string                  `json:"label"`
	CatalogHash      string                  `json:"catalogHash"`
	ModeledRulesHash string                  `json:"modeledRulesHash"`
	ModeledRuleIDs   []string                `json:"modeledRuleIds"`
	Unmodeled        []string                `json:"unmodeled"`
	Evaluated        bool                    `json:"evaluated"`
	ScopeComplete    bool                    `json:"scopeComplete"`
	NotEvaluated     []string                `json:"notEvaluated"`
	Resources        []ResourceCompatibility `json:"resources"`
}

// ExplainCompatibility only runs for explicit read-only requests. Each resource
// has its own sets. Referenced facts can inform the caller's modeled rules, but
// other resources' schema constraints are not silently intersected or retargeted.
func ExplainCompatibility(ctx context.Context, document workspace.Document, w workspace.Workspace) *CompatibilityExplanation {
	ctx = WithLogicalResult(WithInvocation(ctx))
	result := &CompatibilityExplanation{Label: "Candidates under modeled static constraints",
		CatalogHash: compat.Hash(), ModeledRulesHash: compat.ModeledRulesHash(), ModeledRuleIDs: append([]string{}, modeledRuleIDs...),
		Unmodeled: []string{"controller-execution", "kubernetes-admission", "expression-evaluation", "live-cluster-resources", "plugins-and-artifacts", "call-argument-value-forms"},
		Evaluated: true, NotEvaluated: []string{}, Resources: []ResourceCompatibility{}}
	view, err := parseView(ctx, document.Snapshot)
	if err != nil {
		result.Evaluated = false
		result.NotEvaluated = append(result.NotEvaluated, err.Error())
		return result
	}
	selected := w
	selected.Documents = nil
	for _, d := range w.Documents {
		if d.Target.ID == document.Target.ID {
			selected.Documents = append(selected.Documents, d)
		}
	}
	resources, complete, progress, remaining := externalResources(ctx, selected, false)
	result.ScopeComplete = complete[document.Target.ID]
	externalByGroup := make([][]Diagnostic, len(compatibilityVersions().groups))
	// Prepare once; references outside this scope remain unknown. Only selected
	// callers receive a hypothetical release. Callee targets retain their actual
	// identities, and their own deployment compatibility is explicitly unmodeled.
	for i, group := range compatibilityVersions().groups {
		if ctx.Err() != nil {
			break
		}
		// An unready scope cannot prove a unique binding. Existing ordinary
		// diagnostics may show partial findings; they cannot eliminate candidate
		// releases in this explicit explanation until that proof is complete.
		if !complete[document.Target.ID] {
			continue
		}
		prepared := append([]resource{}, resources...)
		for j := range prepared {
			if prepared[j].document.Snapshot.URI() == document.Snapshot.URI() {
				prepared[j].document.Target.Version = group.version
			}
		}
		copiedProgress := maps.Clone(progress)
		for uri, entry := range copiedProgress {
			entry.Resources = maps.Clone(entry.Resources)
			copiedProgress[uri] = entry
		}
		externalByGroup[i], _, _, _ = externalPrepared(ctx, selected, nil, prepared, complete, copiedProgress, maps.Clone(remaining), document.Snapshot.URI())
	}
	for docIndex, doc := range view.Documents() {
		if ctx.Err() != nil {
			result.NotEvaluated = append(result.NotEvaluated, ctx.Err().Error())
			break
		}
		if !doc.Complete() {
			result.NotEvaluated = append(result.NotEvaluated, "incomplete YAML document")
			continue
		}
		value, err := project(ctx, document.Snapshot, doc, document.Target.Version)
		if err != nil {
			result.NotEvaluated = append(result.NotEvaluated, err.Error())
			continue
		}
		api, _ := queryMember(value, "apiVersion").(string)
		kind, _ := queryMember(value, "kind").(string)
		if !owned(api, kind) {
			continue
		}
		name, _ := queryMember(queryMember(value, "metadata"), "name").(string)
		item := ResourceCompatibility{DocumentIndex: docIndex, APIVersion: api, Kind: kind, Name: name,
			Location: nodeLocation(document.Snapshot, doc.Root()), Evaluated: true, ModeledComplete: true,
			Exclusions: []*CompatibilityExclusion{}, Unknowns: []*Refusal{}}
		exclusions := map[string]*CompatibilityExclusion{}
		unknowns := map[string]bool{}
		unknown := func(reason *Refusal) {
			key, _ := json.Marshal(reason)
			if !unknowns[string(key)] {
				unknowns[string(key)] = true
				item.Unknowns = append(item.Unknowns, reason)
			}
			item.ModeledComplete = false
		}
		exclude := func(code, message string, loc Location, rules []string, versions versionSet) {
			if len(rules) == 0 {
				rules = []string{code}
			}
			key, _ := json.Marshal([]any{code, message, loc, rules})
			entry := exclusions[string(key)]
			if entry == nil {
				entry = &CompatibilityExclusion{Code: code, RuleIDs: rules, Message: message, Location: loc, versions: newVersionSet()}
				exclusions[string(key)] = entry
				item.Exclusions = append(item.Exclusions, entry)
			}
			entry.versions.merge(versions)
		}
		schemaSet := newVersionSet()
		schema, schemaErr := assets.Compile(ctx, compat.DefaultVersion, api, kind)
		branchVersions, mapErr := compat.SchemaBranchVersions(api, kind)
		if schemaErr != nil || mapErr != nil || len(schema.AnyOf) != len(branchVersions) {
			item.Evaluated = false
			unknown(Refuse("ANALYSIS_UNAVAILABLE", fmt.Sprintf("automatic schema or branch mapping unavailable: %v / %v", schemaErr, mapErr), item.Location))
		} else {
			available := newVersionSet()
			for i, branch := range schema.AnyOf {
				if ctx.Err() != nil {
					item.Evaluated = false
					break
				}
				versions := newVersionSet()
				for _, version := range branchVersions[i] {
					if n, ok := compatibilityVersions().index[version]; ok {
						versions.add(n)
					}
				}
				available.merge(versions)
				item.Work.SchemaBranches++
				if err := branch.Validate(value); err == nil {
					schemaSet.merge(versions)
				} else if validation, ok := err.(*jsonschema.ValidationError); ok {
					failures := schemaErrors(validation, false)
					failure := failures[0]
					node, _ := location(doc.Root(), failure.InstanceLocation)
					exclude("SCHEMA_NO_MATCH", failure.Error(), nodeLocation(document.Snapshot, node), []string{"whole-release-schema"}, versions)
				} else {
					item.Evaluated = false
					unknown(Refuse("ANALYSIS_UNAVAILABLE", err.Error(), item.Location))
				}
			}
			absent := newVersionSet()
			for i := range compatibilityVersions().versions {
				if !available.contains(i) {
					absent.add(i)
				}
			}
			if len(absent.strings()) > 0 {
				exclude("SCHEMA_UNAVAILABLE", "This resource kind is absent from these releases in the embedded catalog.", nodeLocation(document.Snapshot, member(doc.Root(), "kind")), []string{"whole-release-schema"}, absent)
			}
		}
		semanticSet := newVersionSet()
		for i := range compatibilityVersions().versions {
			semanticSet.add(i)
		}
		shared := sharedOrigins(ctx, document.Snapshot, doc, document.Target.Version)
		for _, resource := range resources {
			if resource.document.Snapshot.URI() != document.Snapshot.URI() || resource.root.Span() != doc.Root().Span() {
				continue
			}
			externalCalls(resource, func(ref, _ workflowValue, _ bool) {
				unknown(Refuse("EXTERNAL_CONTEXT_UNMODELED", "Referenced resource schema and execution constraints are not intersected with this resource's candidate versions.", nodeLocation(document.Snapshot, ref.node)))
				if !complete[document.Target.ID] {
					unknown(Refuse("INDEX_INCOMPLETE", "The selected scope cannot yet prove external bindings; partial external diagnostics do not exclude candidate versions.", nodeLocation(document.Snapshot, ref.node)))
				}
			})
		}
		if !schemaOnlyKind(kind) {
			root := workflowRoot(value, doc.Root())
			root.shared = shared
			spec := workflowMember(root, "spec")
			if kind == "CronWorkflow" {
				spec = workflowMember(spec, "workflowSpec")
			}
			checkCall := func(call workflowValue) {
				arguments := workflowMember(workflowMember(call, "arguments"), "parameters")
				if len(arguments.Elements()) > 0 {
					unknown(Refuse("UNMODELED_CALL_ARGUMENT_FORMS", "Callee input supply is modeled, but parameter value/valueFrom form validation at step, task and hook call sites is not yet modeled.", nodeLocation(document.Snapshot, arguments.node)))
				}
			}
			checkHooks := func(holder workflowValue) {
				for _, hook := range workflowMember(holder, "hooks").Members() {
					checkCall(hook.Value)
				}
			}
			checkHooks(spec)
			for _, template := range workflowMember(spec, "templates").Elements() {
				checkHooks(template)
				for _, task := range workflowMember(workflowMember(template, "dag"), "tasks").Elements() {
					checkCall(task)
					checkHooks(task)
				}
				for _, group := range workflowMember(template, "steps").Elements() {
					for _, step := range group.Elements() {
						checkCall(step)
						checkHooks(step)
					}
				}
			}
		}
		if schemaOnlyKind(kind) {
			unknown(Refuse("UNMODELED_RESOURCE_SEMANTICS", "This resource kind has schema coverage only.", item.Location))
		}
		for i, group := range compatibilityVersions().groups {
			if ctx.Err() != nil {
				item.Evaluated = false
				break
			}
			item.Work.SemanticProfiles++
			diagnostics := localDiagnostics(ctx, document.Snapshot, doc.Root(), value, shared, kind, group.version)
			references, err := referenceDiagnostics(ctx, document.Snapshot, doc.Root(), value, shared, kind, group.version)
			diagnostics = append(diagnostics, references...)
			if err != nil {
				item.Evaluated = false
				unknown(Refuse("ANALYSIS_UNAVAILABLE", err.Error(), item.Location))
			}
			for _, diagnostic := range externalByGroup[i] {
				if containsPosition(item.Location, diagnostic.URI, source.Position{Line: diagnostic.Range.Start.Line, UTF16Column: diagnostic.Range.Start.Character}) {
					diagnostics = append(diagnostics, diagnostic)
				}
			}
			for _, diagnostic := range diagnostics {
				loc := Location{URI: diagnostic.URI, Range: diagnostic.Range}
				if diagnostic.Severity == "Error" {
					semanticSet.remove(group.versions)
					exclude(diagnostic.Code, diagnostic.Message, loc, diagnostic.ruleIDs, group.versions)
				}
				switch diagnostic.Code {
				case "WF_DYNAMIC_REFERENCE":
					unknown(Refuse("DYNAMIC_REFERENCE", diagnostic.Message, loc))
				case "WF_TEMPLATE_REF_UNRESOLVED":
					unknown(Refuse("TARGET_UNKNOWN", diagnostic.Message, loc))
				case "WF_TEMPLATE_REF_AMBIGUOUS":
					unknown(Refuse("AMBIGUOUS_BINDING", diagnostic.Message, loc))
				case "WF_REFERENCE_INCOMPLETE":
					unknown(Refuse("ANALYSIS_UNAVAILABLE", diagnostic.Message, loc))
				}
			}
		}
		if ctx.Err() != nil {
			unknown(Refuse("ANALYSIS_UNAVAILABLE", ctx.Err().Error(), item.Location))
		}
		if item.Evaluated {
			item.SchemaCandidates = schemaSet.strings()
			item.SemanticCandidates = semanticSet.strings()
			item.Candidates = schemaSet.intersect(semanticSet).strings()
			if selected, ok := compatibilityVersions().index[document.Target.Version]; ok {
				item.SelectedTarget = &CompatibilityTarget{document.Target.Version, schemaSet.contains(selected), semanticSet.contains(selected)}
			}
		}
		for _, exclusion := range item.Exclusions {
			exclusion.Versions = exclusion.versions.strings()
		}
		sort.SliceStable(item.Exclusions, func(i, j int) bool {
			a, b := item.Exclusions[i], item.Exclusions[j]
			if a.Location.Range.Start.Line != b.Location.Range.Start.Line {
				return a.Location.Range.Start.Line < b.Location.Range.Start.Line
			}
			if a.Location.Range.Start.Character != b.Location.Range.Start.Character {
				return a.Location.Range.Start.Character < b.Location.Range.Start.Character
			}
			if a.Code != b.Code {
				return a.Code < b.Code
			}
			return a.Message < b.Message
		})
		result.Evaluated = result.Evaluated && item.Evaluated
		result.Resources = append(result.Resources, item)
	}
	result.Evaluated = result.Evaluated && len(result.NotEvaluated) == 0 && ctx.Err() == nil
	return result
}
