package analysis

import (
	"context"
	"crypto/sha256"
	"fmt"

	"github.com/lazysnail1024/k8s-crd-toolkit/source"
	"github.com/lazysnail1024/zed-argo-workflows/internal/workspace"
)

// Refusal is produced by the guard which withholds an operation. Codes are
// independent of human wording; callers must not infer them from error strings.
type Refusal struct {
	ReasonCode       string     `json:"reasonCode"`
	Message          string     `json:"message"`
	RelatedLocations []Location `json:"relatedLocations"`
	NextStep         string     `json:"nextStep"`
}

func (r *Refusal) Error() string { return r.Message }

func Refuse(code, message string, locations ...Location) *Refusal {
	next := "Review the selected input and its diagnostics, then request the operation again."
	switch code {
	case "INDEX_INCOMPLETE":
		next = "Wait for the selected scope to finish indexing; fix unavailable inputs if it remains incomplete."
	case "AMBIGUOUS_BINDING":
		next = "Resolve duplicate declarations or shared source locations so the selected name has one binding."
	case "TARGET_UNKNOWN":
		next = "Resolve the target resource and namespace in this scope; provide an explicit namespace or reviewed defaultNamespace and check its target version."
	case "AFFECTED_FILE_NOT_OPEN":
		next = "Open every listed affected file in the editor, then request the operation again."
	case "DYNAMIC_REFERENCE":
		next = "Use a statically resolvable reference or inspect and edit the dynamic references manually."
	case "STALE_INPUT":
		next = "Request a new action or operation for the current buffer and configuration."
	case "REFERENCE_POLICY_OPEN":
		next = "Review whether this scope contains all references; resource rename requires a deliberately configured closed reference policy."
	case "CLIENT_CAPABILITY_MISSING":
		next = "Use a client that supports versioned documentChanges; read-only analysis remains available."
	case "UNMODELED_BINDING":
		next = "Inspect these references manually; the static model cannot prove this edit safe."
	case "NO_RENAMABLE_SYMBOL":
		next = "Select a supported, uniquely bound symbol name and request the operation again."
	case "ANALYSIS_UNAVAILABLE":
		next = "Correct the unavailable input or configuration, then request an explanation again."
	case "CANCELLED":
		next = "Request the operation again when needed."
	}
	if locations == nil {
		locations = []Location{}
	}
	return &Refusal{ReasonCode: code, Message: message, RelatedLocations: locations, NextStep: next}
}

type OperationAvailability struct {
	Operation string `json:"operation"`
	Allowed   bool   `json:"allowed"`
	*Refusal
}

type InputIdentity struct {
	Source            string `json:"source"`
	URI               string `json:"uri"`
	Version           *int32 `json:"version,omitempty"`
	Revision          uint64 `json:"revision"`
	SHA256            string `json:"sha256"`
	ConfigurationHash string `json:"configurationHash"`
}

type Completeness struct {
	Document  bool `json:"document"`
	Workspace bool `json:"workspace"`
}

type ReferenceStatus struct {
	Count    int  `json:"count"`
	Complete bool `json:"complete"`
	*Refusal
}

func ExplainInput(snapshot source.Snapshot, inputSource string, version *int32, configHash string) InputIdentity {
	return InputIdentity{Source: inputSource, URI: string(snapshot.URI()), Version: version,
		Revision: uint64(snapshot.Revision()), SHA256: fmt.Sprintf("%x", sha256.Sum256([]byte(snapshot.Text()))), ConfigurationHash: configHash}
}

// AnalysisExplanation is a detached view of a particular input. It is never
// retained as a second lifecycle state or used as authorization to perform edits.
type AnalysisExplanation struct {
	Root          string                           `json:"root"`
	Roots         []string                         `json:"roots"`
	Scope         string                           `json:"scope"`
	TargetVersion string                           `json:"targetVersion"`
	InputIdentity InputIdentity                    `json:"inputIdentity"`
	Phase         string                           `json:"phase"`
	Completeness  Completeness                     `json:"completeness"`
	Reasons       []*Refusal                       `json:"reasons"`
	Operations    map[string]OperationAvailability `json:"operations"`
	References    *ReferenceStatus                 `json:"references,omitempty"`
	Compatibility *CompatibilityExplanation        `json:"compatibility,omitempty"`
}

func ExplainDocument(ctx context.Context, document workspace.Document, inputSource string, version *int32, complete bool) AnalysisExplanation {
	result := AnalyzeSchema(WithLogicalResult(ctx), document.Snapshot, document.Target.Version)
	status := AnalysisExplanation{
		Root: document.Target.Root, Roots: []string{document.Target.Root}, Scope: document.Target.Scope,
		TargetVersion: document.Target.Version, Phase: "ready", Reasons: []*Refusal{},
		Operations:    map[string]OperationAvailability{},
		Completeness:  Completeness{Document: len(result.NotEvaluated) == 0, Workspace: complete},
		InputIdentity: ExplainInput(document.Snapshot, inputSource, version, document.Target.ConfigHash),
	}
	for _, reason := range result.NotEvaluated {
		status.Reasons = append(status.Reasons, Refuse("ANALYSIS_UNAVAILABLE", reason))
	}
	if !complete {
		status.Phase = "indexing"
		status.Reasons = append(status.Reasons, Refuse("INDEX_INCOMPLETE", "The current document and the selected workspace scope have separate completeness."))
	}
	if !status.Completeness.Document {
		status.Phase = "incomplete"
	}
	return status
}

// Disk acquisition can finish while a sibling YAML resource is unevaluable.
// Reuse the resolver's completed-scope fact instead of treating an enumerated
// file set as a proof that every modeled external binding is known.
func (status *AnalysisExplanation) ExplainCompatibility(ctx context.Context, document workspace.Document, w workspace.Workspace) {
	status.Compatibility = ExplainCompatibility(ctx, document, w)
	if status.Completeness.Workspace && !status.Compatibility.ScopeComplete {
		status.Completeness.Workspace = false
		status.Phase = "incomplete"
		status.Reasons = append(status.Reasons, Refuse("INDEX_INCOMPLETE", "The selected scope contains unavailable or unevaluable external-reference inputs."))
	}
}
