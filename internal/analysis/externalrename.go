package analysis

import (
	"context"
	"fmt"
	"regexp"
	"sort"
	"strings"

	"github.com/lazysnail1024/k8s-crd-toolkit/edit"
	"github.com/lazysnail1024/k8s-crd-toolkit/source"
	"github.com/lazysnail1024/k8s-crd-toolkit/yamlview"
	"github.com/lazysnail1024/zed-argo-workflows/internal/workspace"
)

type resourceNameUse struct {
	resource int
	name     workflowValue
	identity externalIdentity
	call     int
}

type resourceRenameSelection struct {
	input       workspace.Workspace
	resources   []resource
	uses        []resourceNameUse
	definition  int
	identity    externalIdentity
	selection   Location
	occurrences []resourceNameUse
}

// ResourceRenameAt only chooses the rename domain from the current source.
// Namespace, binding and edit safety remain the resource rename proof's job.
// A local binding error must not be replaced by an unrelated resource error.
func ResourceRenameAt(ctx context.Context, snapshot source.Snapshot, pos source.Position) (bool, error) {
	at, err := snapshot.Offset(pos)
	if err != nil {
		return false, err
	}
	view, err := parseView(ctx, snapshot)
	if err != nil {
		return false, err
	}
	contains := func(n yamlview.Node) bool {
		return n.Kind() != yamlview.Empty && at >= n.Span().Start && at <= n.Span().End
	}
	var referenceAt func(yamlview.Node) bool
	referenceAt = func(n yamlview.Node) bool {
		if !contains(n) || ctx.Err() != nil {
			return false
		}
		for _, m := range n.Members() {
			key := stringValue(m.Key)
			if (key == "templateRef" || key == "workflowTemplateRef") && contains(member(m.Value, "name")) {
				return true
			}
			if referenceAt(m.Value) {
				return true
			}
		}
		for _, child := range n.Elements() {
			if referenceAt(child) {
				return true
			}
		}
		return false
	}
	for _, doc := range view.Documents() {
		root := doc.Root()
		if !contains(root) || !owned(stringValue(member(root, "apiVersion")), stringValue(member(root, "kind"))) {
			continue
		}
		kind := stringValue(member(root, "kind"))
		declaration := (kind == "WorkflowTemplate" || kind == "ClusterWorkflowTemplate") && contains(member(member(root, "metadata"), "name"))
		return declaration || referenceAt(member(root, "spec")), ctx.Err()
	}
	return false, ctx.Err()
}

func resourceIdentity(r resource) externalIdentity {
	namespace := r.namespace
	if r.kind == "ClusterWorkflowTemplate" {
		namespace = ""
	}
	return externalIdentity{r.document.Target.ID, r.kind, namespace, r.name}
}

func exactResourceName(n workflowValue) bool {
	name, _, ok := exactName(n.node)
	return ok && n.origin == "exact" && name == workflowString(n) && !sharedOrigin(n.shared, n.node.Span())
}

// resourceRenameInputs rejects references that our binding walk does not model.
// A partial positive-reference list is sufficient for navigation, never Rename.
func resourceRenameInputs(ctx context.Context, w workspace.Workspace) ([]resource, []resourceNameUse, error) {
	resources, complete, _, _ := externalResources(ctx, w, false)
	for _, d := range w.Documents {
		if !complete[d.Target.ID] {
			return nil, nil, fmt.Errorf("resource rename requires a complete scope")
		}
		view, err := parseView(ctx, d.Snapshot)
		if err != nil {
			return nil, nil, err
		}
		for _, doc := range view.Documents() {
			v, err := project(ctx, d.Snapshot, doc, d.Target.Version)
			if err != nil {
				return nil, nil, err
			}
			api, _ := queryMember(v, "apiVersion").(string)
			kind, _ := queryMember(v, "kind").(string)
			if owned(api, kind) && schemaOnlyKind(kind) {
				return nil, nil, fmt.Errorf("schema-only resources do not have a complete rename binding model")
			}
		}
	}
	uses := []resourceNameUse{}
	for i, r := range resources {
		if err := ctx.Err(); err != nil {
			return nil, nil, err
		}
		modeled := map[source.Span]int{}
		call := 0
		var problem error
		externalCalls(r, func(ref, _ workflowValue, _ bool) {
			name := workflowMember(ref, "name")
			key, known := candidateIdentity(r, ref)
			if !known || key.name == "" || strings.ContainsAny(key.name, "{}") || key.kind == "WorkflowTemplate" && !staticResourceNamespace(key.namespace) {
				problem = fmt.Errorf("dynamic reference or unknown namespace prevents proving resource rename")
			}
			modeled[ref.Span()]++
			uses = append(uses, resourceNameUse{i, name, key, call})
			call++
		})
		var inspect func(workflowValue)
		inspect = func(n workflowValue) {
			if problem != nil || ctx.Err() != nil {
				return
			}
			for _, m := range n.Members() {
				if m.Name == "inline" {
					problem = fmt.Errorf("inline template bindings are not modeled for resource rename")
					return
				}
				if m.Name == "templateRef" || m.Name == "workflowTemplateRef" {
					if modeled[m.Value.Span()] == 0 {
						problem = fmt.Errorf("nested reference is not modeled for resource rename")
						return
					}
					modeled[m.Value.Span()]--
				}
				inspect(m.Value)
			}
			for _, child := range n.Elements() {
				inspect(child)
			}
		}
		inspect(r.semanticSpec)
		if problem != nil {
			return nil, nil, problem
		}
		for _, remaining := range modeled {
			if remaining != 0 {
				return nil, nil, fmt.Errorf("resource reference walk is incomplete")
			}
		}
	}
	return resources, uses, ctx.Err()
}

func selectResourceRename(ctx context.Context, w workspace.Workspace, uri string, pos source.Position) (resourceRenameSelection, error) {
	selection := resourceRenameSelection{definition: -1}
	if w.Config.ReferencePolicy != "closed" {
		return selection, fmt.Errorf("resource rename requires closed reference policy")
	}
	target := ""
	for _, d := range w.Documents {
		if string(d.Snapshot.URI()) == uri {
			target = d.Target.ID
		}
	}
	if target == "" || w.IncompleteScopes[target] {
		return selection, fmt.Errorf("resource rename requires the complete selected scope")
	}
	selection.input = w
	selection.input.Documents = nil
	for _, d := range w.Documents {
		if d.Target.ID == target {
			selection.input.Documents = append(selection.input.Documents, d)
		}
	}
	var err error
	selection.resources, selection.uses, err = resourceRenameInputs(WithLogicalResult(ctx), selection.input)
	if err != nil {
		return selection, err
	}
	selected := false
	choose := func(n workflowValue, r resource, key externalIdentity) {
		loc := nodeLocation(r.document.Snapshot, n.node)
		if containsPosition(loc, uri, pos) {
			selection.identity, selection.selection, selected = key, loc, true
		}
	}
	for i, r := range selection.resources {
		if r.kind != "WorkflowTemplate" && r.kind != "ClusterWorkflowTemplate" {
			continue
		}
		node := member(member(r.root, "metadata"), "name")
		name := workflowRoot(r.name, node)
		name.shared = r.shared
		key := resourceIdentity(r)
		choose(name, r, key)
		selection.occurrences = append(selection.occurrences, resourceNameUse{i, name, key, -1})
	}
	for _, use := range selection.uses {
		choose(use.name, selection.resources[use.resource], use.identity)
	}
	if !selected || len(selection.identity.name) > 253 || !resourceNamePattern.MatchString(selection.identity.name) || selection.identity.kind == "WorkflowTemplate" && !staticResourceNamespace(selection.identity.namespace) {
		return selection, fmt.Errorf("position has no static resource name with known namespace")
	}
	declarations := 0
	for i, r := range selection.resources {
		if resourceIdentity(r) == selection.identity {
			selection.definition = i
			declarations++
		}
	}
	if declarations != 1 {
		return selection, fmt.Errorf("resource name does not have a unique declaration")
	}
	selection.occurrences = append(selection.occurrences, selection.uses...)
	exact := selection.occurrences[:0]
	for _, use := range selection.occurrences {
		if use.identity != selection.identity {
			continue
		}
		if !exactResourceName(use.name) {
			return selection, fmt.Errorf("resource name has shared, derived or ambiguous source")
		}
		exact = append(exact, use)
	}
	selection.occurrences = exact
	return selection, ctx.Err()
}

// PrepareResourceRename returns the full affected-file set for the LSP host's
// opened-buffer/version checks. Domain code never assumes client edit support.
func PrepareResourceRename(ctx context.Context, w workspace.Workspace, uri string, pos source.Position) (any, []source.URI, error) {
	selected, err := selectResourceRename(ctx, w, uri, pos)
	if err != nil {
		return nil, nil, err
	}
	if err := safeScopeChange(ctx, selected.input, selected.input, selected.identity.target); err != nil {
		return nil, nil, err
	}
	seen := map[source.URI]bool{}
	for _, use := range selected.occurrences {
		seen[selected.resources[use.resource].document.Snapshot.URI()] = true
	}
	files := make([]source.URI, 0, len(seen))
	for name := range seen {
		files = append(files, name)
	}
	sort.Slice(files, func(i, j int) bool { return files[i] < files[j] })
	return map[string]any{"range": selected.selection.Range, "placeholder": selected.identity.name}, files, nil
}

var resourceNamePattern = regexp.MustCompile(`^[a-z0-9](?:[-a-z0-9]*[a-z0-9])?(?:\.[a-z0-9](?:[-a-z0-9]*[a-z0-9])?)*$`)
var resourceNamespacePattern = regexp.MustCompile(`^[a-z0-9](?:[-a-z0-9]*[a-z0-9])?$`)

func staticResourceNamespace(namespace string) bool {
	return len(namespace) <= 63 && resourceNamespacePattern.MatchString(namespace)
}

func resourceBindings(resources []resource, uses []resourceNameUse) map[string]string {
	declarations := map[externalIdentity][]int{}
	ids := make([]string, len(resources))
	ordinals := map[source.URI]int{}
	for i, r := range resources {
		uri := r.document.Snapshot.URI()
		ids[i] = fmt.Sprintf("%s\n%d", uri, ordinals[uri])
		ordinals[uri]++
		declarations[resourceIdentity(r)] = append(declarations[resourceIdentity(r)], i)
	}
	bindings := map[string]string{}
	for _, use := range uses {
		key := fmt.Sprintf("%s\n%d", ids[use.resource], use.call)
		bindings[key] = "unresolved"
		if candidates := declarations[use.identity]; len(candidates) == 1 {
			bindings[key] = ids[candidates[0]]
		}
	}
	return bindings
}

// RenameResource applies all edits to temporary snapshots before proving the
// same call-to-declaration bindings and validating the complete changed scope.
func RenameResource(ctx context.Context, w workspace.Workspace, uri string, pos source.Position, newName string) (map[source.URI]edit.Plan, error) {
	selected, err := selectResourceRename(ctx, w, uri, pos)
	if err != nil {
		return nil, err
	}
	if len(newName) > 253 || !resourceNamePattern.MatchString(newName) || newName == selected.identity.name {
		return nil, fmt.Errorf("invalid or unchanged resource name")
	}
	nextIdentity := selected.identity
	nextIdentity.name = newName
	for _, r := range selected.resources {
		if resourceIdentity(r) == nextIdentity {
			return nil, fmt.Errorf("new resource name conflicts with a declaration")
		}
	}
	plans := map[source.URI]edit.Plan{}
	for _, use := range selected.occurrences {
		r := selected.resources[use.resource]
		part, ok := scalarPlan(r.document.Snapshot, use.name.node, newName)
		if !ok || use.name.node.Kind() != yamlview.Scalar {
			return nil, fmt.Errorf("resource name cannot be edited independently")
		}
		key := r.document.Snapshot.URI()
		plan := plans[key]
		plan.BaseRevision = part.BaseRevision
		plan.Replacements = append(plan.Replacements, part.Replacements...)
		plans[key] = plan
	}
	after := selected.input
	after.Documents = append([]workspace.Document(nil), selected.input.Documents...)
	for i, d := range after.Documents {
		plan, changed := plans[d.Snapshot.URI()]
		if !changed {
			continue
		}
		next, err := edit.Apply(d.Snapshot, d.Snapshot.Revision()+1, plan)
		if err != nil {
			return nil, err
		}
		after.Documents[i].Snapshot = next
	}
	resources, uses, err := resourceRenameInputs(WithLogicalResult(ctx), after)
	if err != nil {
		return nil, err
	}
	beforeBindings, afterBindings := resourceBindings(selected.resources, selected.uses), resourceBindings(resources, uses)
	if len(beforeBindings) != len(afterBindings) {
		return nil, fmt.Errorf("rename changed the external reference set")
	}
	for call, target := range beforeBindings {
		if afterBindings[call] != target {
			return nil, fmt.Errorf("rename changed an external binding")
		}
	}
	if err := safeScopeChange(ctx, selected.input, after, selected.identity.target); err != nil {
		return nil, err
	}
	return plans, ctx.Err()
}
