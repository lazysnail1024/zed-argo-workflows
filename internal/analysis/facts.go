package analysis

import (
	"context"
	"encoding/json"

	"github.com/lazysnail1024/k8s-crd-toolkit/yamlview"
	"github.com/lazysnail1024/zed-argo-workflows/internal/workspace"
)

// Facts is one detached immutable handoff of existing parse/projection data.
// It contains no context, domain result, cancellation outcome or schema document.
// The owner accepts it only from a completed current-epoch worker. Older facts
// may be retained across changes, but restoration requires freshly acquired
// byte-identical snapshots and target versions. Each new
// invocation copies the outer maps; all consumers read the underlying values.
type Facts struct {
	boundary    string
	views       map[snapshotIdentity]yamlview.View
	projections map[projectionIdentity]projectedDocument
	costs       map[snapshotIdentity]factBudget
}

// CaptureDocumentFacts is the catalog's completed single-document handoff.
// Parse/projection values carry no workspace policy; avoid retaining or
// serializing a full multi-scope header for each independently indexed file.
func CaptureDocumentFacts(ctx context.Context, document workspace.Document) *Facts {
	return CaptureFacts(ctx, workspace.Workspace{Documents: []workspace.Document{document}})
}

// These optional-retention limits do not reduce the workspace input allowance.
// Text includes source/URI/boundary bytes and expanded JSON strings/keys/numbers;
// nodes include CST nodes/documents and JSON containers, keys and scalar values.
const maxFactText = 8 * 1024 * 1024
const maxFactNodes = 512000

// FactBoundary admits one complete acquired scope and binds all current options
// and targets. Disk acquisition and buffer authority must precede this check.
func FactBoundary(w workspace.Workspace) string {
	if len(w.Documents) == 0 || len(w.NotEvaluated) != 0 {
		return ""
	}
	for _, incomplete := range w.IncompleteScopes {
		if incomplete {
			return ""
		}
	}
	target := w.Documents[0].Target.ID
	for _, d := range w.Documents {
		if d.Target.ID != target {
			return ""
		}
	}
	data, err := json.Marshal(struct {
		Root       string
		Config     workspace.Config
		Targets    []workspace.Target
		Scope      string
		Incomplete map[string]bool
	}{w.Root, w.Config, w.Targets, target, w.IncompleteScopes})
	if err != nil {
		return ""
	}
	return string(data)
}

func factInputs(w workspace.Workspace) map[snapshotIdentity]string {
	inputs := make(map[snapshotIdentity]string, len(w.Documents))
	for _, d := range w.Documents {
		inputs[snapshotKey(d.Snapshot)] = d.Target.Version
	}
	return inputs
}

// RestoreFacts copies only facts for freshly acquired, byte-identical inputs.
// No previous request context or map ownership crosses this boundary.
func RestoreFacts(ctx context.Context, facts *Facts, w workspace.Workspace) {
	state, _ := ctx.Value(invocationKey{}).(*invocation)
	if state == nil || facts == nil || ctx.Err() != nil {
		return
	}
	inputs := factInputs(w)
	for key, view := range facts.views {
		if ctx.Err() != nil {
			return
		}
		if _, ok := inputs[key]; !ok {
			continue
		}
		if _, ok := state.views[key]; !ok && state.bytes+len(key.text) <= 50*1024*1024 {
			state.views[key] = view
			state.sharedViews[key.text] = view
			state.bytes += len(key.text)
		}
	}
	for key, value := range facts.projections {
		if inputs[key.snapshotIdentity] != key.version {
			continue
		}
		if _, ok := state.views[key.snapshotIdentity]; ok {
			state.projections[key] = value
			state.sharedProjections[key.content()] = value
		}
	}
	state.imported = facts
}

type factBudget struct{ text, nodes int }

func (b *factBudget) add(text int) bool {
	if text > maxFactText-b.text || b.nodes >= maxFactNodes {
		return false
	}
	b.text += text
	b.nodes++
	return true
}
func (b *factBudget) node(ctx context.Context, n yamlview.Node, depth int) bool {
	if depth > 256 || ctx.Err() != nil || !b.add(0) {
		return false
	}
	for _, m := range n.Members() {
		if !b.node(ctx, m.Key, depth+1) || !b.node(ctx, m.Value, depth+1) {
			return false
		}
	}
	for _, e := range n.Elements() {
		if !b.node(ctx, e, depth+1) {
			return false
		}
	}
	return true
}
func (b *factBudget) value(ctx context.Context, value any, depth int) bool {
	if depth > 256 || ctx.Err() != nil || !b.add(0) {
		return false
	}
	switch value := value.(type) {
	case map[string]any:
		for k, v := range value {
			if !b.add(len(k)) || !b.value(ctx, v, depth+1) {
				return false
			}
		}
	case []any:
		for _, v := range value {
			if !b.value(ctx, v, depth+1) {
				return false
			}
		}
	case string:
		if len(value) > maxFactText-b.text {
			return false
		}
		b.text += len(value)
	case json.Number:
		if len(value) > maxFactText-b.text {
			return false
		}
		b.text += len(value)
	case nil, bool, float64:
	default:
		return false
	}
	return true
}

// CaptureFacts drops excess retention instead of refusing current analysis.
// Temporary edit/query snapshots and earlier scopes never enter the handoff.
func CaptureFacts(ctx context.Context, w workspace.Workspace) *Facts {
	state, _ := ctx.Value(invocationKey{}).(*invocation)
	boundary := FactBoundary(w)
	if state == nil || boundary == "" || ctx.Err() != nil {
		return nil
	}
	inputs := factInputs(w)
	facts := &Facts{boundary: boundary, views: map[snapshotIdentity]yamlview.View{}, projections: map[projectionIdentity]projectedDocument{}, costs: map[snapshotIdentity]factBudget{}}
	for key, view := range state.views {
		if _, ok := inputs[key]; ok {
			facts.views[key] = view
		}
	}
	for key, value := range state.projections {
		if inputs[key.snapshotIdentity] == key.version {
			facts.projections[key] = value
		}
	}
	if len(facts.views) == 0 {
		return nil
	}
	// Identical immutable facts were already measured; avoid a second tree walk
	// on unchanged queries. Newly created maps remain invocation-owned.
	if old := state.imported; old != nil && old.boundary == boundary && len(old.views) == len(facts.views) && len(old.projections) == len(facts.projections) {
		same := true
		for key := range facts.views {
			if _, ok := old.views[key]; !ok {
				same = false
				break
			}
		}
		if same {
			for key := range facts.projections {
				if _, ok := old.projections[key]; !ok {
					same = false
					break
				}
			}
		}
		if same && ctx.Err() == nil {
			return old
		}
	}
	budget := factBudget{}
	if !budget.add(len(boundary)) {
		return nil
	}
	for key, view := range facts.views {
		before := budget
		if !budget.add(len(key.text) + len(key.uri)) {
			return nil
		}
		for _, doc := range view.Documents() {
			if !doc.Complete() || !budget.add(0) || !budget.node(ctx, doc.Root(), 0) {
				return nil
			}
		}
		facts.costs[key] = factBudget{budget.text - before.text, budget.nodes - before.nodes}
	}
	for key, value := range facts.projections {
		before := budget
		for range value.shared {
			if !budget.add(0) {
				return nil
			}
		}
		if !budget.add(len(key.version)) || !budget.value(ctx, value.value, 0) {
			return nil
		}
		cost := facts.costs[key.snapshotIdentity]
		cost.text += budget.text - before.text
		cost.nodes += budget.nodes - before.nodes
		facts.costs[key.snapshotIdentity] = cost
	}
	if ctx.Err() != nil {
		return nil
	}
	return facts
}
