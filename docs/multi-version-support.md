# Version coverage and automatic compatibility

The native language server defaults to `auto`. It embeds official Argo Workflows
schemas for **146 exact versions from 3.0.0 through 4.1.6**, across the two most
recent major versions in the source snapshot, whose cutoff is
`2026-11-05T13:59:59Z` in [catalog.lock.json](../compat/catalog.lock.json).
This is a bundled offline inventory, not a live claim about the newest upstream release.
Analysis requires no version setting or network schema access.

## Coverage

All eight official CRD kinds present in the supported releases use the same
schema diagnostics, field completion and hover implementation.

| Resource | Available releases in this snapshot | Additional editing behavior |
| --- | --- | --- |
| Workflow | 3.0.0–4.1.6 (146 versions) | Local references, parameters, DAG/steps and supported cross-file references |
| WorkflowTemplate | 3.0.0–4.1.6 (146 versions) | Supported template and cross-file queries |
| ClusterWorkflowTemplate | 3.0.0–4.1.6 (146 versions) | Supported template and cross-file queries |
| CronWorkflow | 3.0.0–4.1.6 (146 versions) | Old `schedule` and newer `schedules` forms; embedded workflow specification |
| WorkflowEventBinding | 3.0.0–4.1.6 (146 versions) | Schema only; event expressions and submission are not executed |
| WorkflowTaskSet | 3.2.0–4.1.6 (119 versions) | Schema only; executor task payloads do not create local workflow template scopes |
| WorkflowTaskResult | 3.3.0–4.1.6 (107 versions) | Schema only; output values do not create local workflow template scopes |
| WorkflowArtifactGCTask | 3.4.0–4.1.6 (96 versions) | Schema only; artifact cleanup is not executed |

The catalog is sparse where a release did not yet contain a resource kind. It
records successful CRD sources and both immutable upstream paths checked for an
absent kind. Only HTTP 404 establishes absence; a network or server failure aborts
collection. An exact pin before a kind's introduction produces `SCHEMA_UNAVAILABLE`
at the resource kind, with a completed analysis and no fallback to another release.
Completion and hover return no schema result for that unavailable kind. Automatic
hover source ranges reflect the releases that actually include the kind.

Schema coverage does not simulate a controller, Kubernetes admission, permissions,
available plugins, remote artifacts, expression execution or a live cluster.
Upstream schemas sometimes intentionally allow arbitrary objects; the server
preserves those declared open maps.

## Selecting versions

Automatic compatibility accepts a document's structure when at least one complete
supported release schema accepts it. It does not combine unrelated fields into a
fictional schema. For example, `podPriority` from older 3.x and `podResources` from
4.1 can each be edited without configuration, while a document containing both is
reported because no supported release defines that combination.

The same branch selection limits completion and hover according to fields already
present in the document. Missing fields and unrelated value errors do not erase
useful completion while a document is being written. If no branch matches an
unfinished document, queries retain the full catalog to help repair it.

Schema checks cover value types, required properties and supported upstream
constraints. Ten distinct upstream CEL validation expressions are translated into
JSON Schema at generation time. New CEL expressions and unknown normalization
keywords stop generation until reviewed; there is no general CEL interpreter or
runtime schema download.

Optional exact pins remain available for checking a known deployment. Every
catalog version accepts a pin; unknown versions produce an actionable configuration
error. Exact pins preserve their original upstream unknown-property behavior,
including warning diagnostics for fields absent from that release. The compatibility
mode closes known structured objects so an old schema cannot silently admit a
new field with an invalid type. It leaves open maps open.

Local workflow analysis covers the supported static editing rules. In automatic
mode, a version-specific static rule permits forms supported somewhere in the
catalog. It does not prove that every controller execution detail in a document
belongs to the same release; use an exact pin and upstream validation for a
particular deployment.

## Explaining modeled candidate versions

Run `argo-workflows-ls explain-config --root . --file example.yaml` for saved bytes,
or select **Argo Workflows: explain current analysis** for the current editor buffer.
The explicit LSP request `argo-workflows/explainAnalysis` returns the same structured
data without a message. The outer input identity records exact bytes, source,
configuration, root and scope; the compatibility projection includes the catalog
hash and the independent [modeled-rule provenance](../compat/modeled-rules.json) hash.

The result is named **candidates under modeled static constraints**. Every owned
YAML resource has its own record, including separate resources in one file. The
server does not intersect an entire workspace or assume its resources deploy to
one cluster. The fields are:

| Field | Meaning |
| --- | --- |
| `schemaCandidates` | Exact catalog releases whose complete, structurally closed automatic schema branch accepts this resource. |
| `semanticCandidates` | Exact releases with no counterexample from the existing local/reference rules and the modeled external caller rules for available, proven scope facts. |
| `candidates` | Intersection of those two sets, in catalog order. |
| `exclusions` | Actual schema/rule failures, source locations, responsible rule IDs and the affected exact releases. A schema branch reports a concrete failing constraint rather than every duplicate error. |
| `evaluated` | Whether the bounded calculation finished. False leaves arrays null, distinguishing unavailable evaluation from a proven empty set. |
| `modeledComplete` and `unknowns` | Whether relevant modeled context was known, plus dynamic, ambiguous, unavailable and explicitly unmodeled areas. A nonempty possible set does not turn unknown context into proof. |
| `selectedTarget` | For an exact pin, its membership in each set. Exact diagnostic behavior remains a separate contract. |
| `work` | Schema branch and finite semantic-signature evaluations performed for this resource; these counters are not performance benchmarks. |

The two sets can each be nonempty while their intersection is empty. A
CronWorkflow with `schedules` and an unused top argument using `valueFrom.path`
has 61 schema candidates (3.6.0–4.1.6), while the modeled argument-form rule permits
45 older releases; no collected release satisfies both. The ordinary `auto`
checker retains its existing successful result for that example. An explanation
adds information without introducing a new warning or changing automatic pass/fail.
The independent scenario is `wf-compatibility/joint-empty`.

Candidate schema membership uses structural closure, including for a selected
exact pin. The existing exact checker still reports unsupported properties as
warnings where the original upstream schema permits them. An exact check can
therefore complete without an Error while that release is absent from the stricter
schema candidate set; the explanation does not reinterpret the historical warning.

Dynamic references and unresolved or ambiguous external bindings remain unknown.
An incomplete scope cannot supply external exclusion evidence. Resolved referenced
resources inform the caller's existing static rules; their own schema and execution
constraints are not silently retargeted or intersected. This limitation is reported
at the reference. The four schema-only kinds explicitly report unmodeled semantics.
Input-supply checks cover local and supported external calls, but parameter
value/valueFrom form checks at step, DAG task and hook call sites are not currently
modeled and are reported as unknown when such arguments are present.

Controller execution, live ConfigMaps and other cluster objects, arbitrary expression
evaluation, admission, plugins and artifact availability remain outside the model.
The result neither executes upstream validators nor proves deployment compatibility.

### Modeled release boundaries

The same product predicates serve ordinary diagnostics and the explanation. The
current finite signature covers these independently sourced changes:

| Rule ID | Catalog feature boundary |
| --- | --- |
| `argument-value-from` | `valueFrom` can replace a missing literal argument from 3.2.0. |
| `template-empty-arguments` | Independent template validation permits empty top arguments from 3.2.4. |
| `argument-configmap` | A ConfigMap source can satisfy an input from 3.2.5. |
| `dag-omitted` | DAG `Omitted` task result from 3.3.9. |
| `workflow-parameters-json` | JSON aggregate and independent template parameter allowance from 3.4.2. |
| `external-exit` | Holder exit context and expanded inherited-template traversal from 3.4.6. |
| `argument-overwrites-default` | A supplied argument without a literal value clears the input fallback from 3.4.10. |
| `argument-value-from-restriction` | Restricted argument forms from 3.5.5, separately backported to 3.4.17. |
| `absent-output-marker` | Optional-output marker handling from 3.7.16 and separately 4.0.7. |

The marker predicate intentionally corrects the former exact-pin behavior for
4.0.0–4.0.6, whose pinned `ProcessArgs` source lacks the later marker branch. The
ordinary `auto` behavior is unchanged. Other existing exact-boundary outputs remain
covered by the before/after regression consumers. Source commits, function and
caller hashes, conditions, branch ranges and behavioral scenario IDs are in the
provenance manifest. It explicitly records **boundary samples**, not a full audit
of every controller source in all 146 releases.

## Source update procedure

1. Run `python3 scripts/collect-catalog.py` to record upstream release metadata,
   the Git tag inventory, commit identities and content-addressed CRD sources.
2. Review semantic source and caller changes independently of CRD changes. Retain
   exact upstream files at catalog-pinned commits, update
   `compat/modeled-rules.json`, the actual predicates and independent boundary
   scenarios where appropriate. Equal CRDs do not exempt this review: 3.4.16/17
   and 3.5.4/5 have equal CRD identities but different argument validation rules.
3. Run `python3 scripts/check-modeled-rules.py`. It verifies reviewed release/commit
   inventory, declared source identities, ranges, function metadata and scenario
   IDs. A changed pin set requires a reviewed provenance update. With
   `--source-dir /path/to/pinned-sources`, it also verifies exact files named
   `<sourceID>.go.txt`, Git blob hashes and AST-recorded function/caller byte slices.
   `--baseline /path/to/previous-modeled-rules.json` reports source and rule diffs
   separately from the declared CRD identity. The checker never fetches sources.
4. Run `python3 scripts/normalize-schemas.py` to generate exact schemas and the
   compact automatic alternatives, reject unsupported constraints, and remove
   obsolete generated schema blobs. It runs the semantic provenance checker before
   changing generated files. Generation also records the exact release set for
   each deduplicated whole branch; root-description normalization is included in
   that mapping, so it is not reconstructed from exact-file hashes.
5. Rebuild the native server and run the version and compatibility consumers and
   targeted ablation suites. These remain product-local rules and do not require
   a toolkit upgrade.

The inventory includes every official tag of the form `vMAJOR.MINOR.PATCH` in the
latest two major versions. Tags with `rc`, `alpha`, `beta` or other suffixes are
excluded. A GitHub Release record is not required, and a release record marked
prerelease does not remove an otherwise matching final-format tag. Release
metadata, tag provenance and raw source hashes remain in the catalog for auditing.

## Implementation and verification

Automatic schemas contain complete release alternatives and shared `$defs` for
identical schema subtrees. Root descriptions that only restate a resource kind
are omitted from automatic alternatives, so identical WorkflowTemplate and
ClusterWorkflowTemplate contracts share one file and compiled graph; field
descriptions and exact-version schemas remain intact. Only immutable embedded
schemas are compiled, lazily,
through the existing schema loader. There is no version provider registry,
per-editor validator, synthetic latest schema or separate version negotiation
service.

Explicit compatibility explanation validates each compiled automatic branch once,
reuses accepted parse/projection facts and evaluates ten distinct current semantic
signatures. Its internal release sets are bitsets. It does not perform 146 complete
document/workspace analyses, compile 146 exact schemas, or run compatibility work
on ordinary open/diagnostic/query traffic. Cancellation and the existing input and
scope publication proofs still apply.

Run these consumers with a built native binary and a new output directory:

```sh
python3 tests/e2e/wf-multi-version.py /path/to/argo-workflows-ls /path/to/acceptance
python3 tests/e2e/wf-compatibility.py /path/to/argo-workflows-ls /path/to/explanation
python3 tests/e2e/wf-multi-version-ablation.py /path/to/argo-workflows-ls /path/to/ablation
```

The acceptance suite checks provenance and all exact versions, zero-configuration
old/new fields, invalid types, missing required properties, impossible cross-version
combinations, all eight resource kinds and their 1,052 actual version/kind pairs,
introduction boundaries, schema-only semantic isolation, ten CEL translations,
exact 3.6 behavior, and hover/completion consistency. A single automatic LSP
session loads all eight kinds and retains the existing 128 MiB Linux memory
budget. The ablation suite creates isolated native builds
that remove whole-release alternatives, structural closure, query branch selection
and schema interning one at a time. It records observed failures or resource cost,
plus the source and binary hashes. Existing bounded tests continue to enforce
latency, lifecycle and memory behavior.

The compatibility consumer adds CLI and stdio cases for all nine semantic
boundaries, both backport lines, schema introduction/removal, an empty joint set,
auto/exact comparison, exact input identity, causal rule attribution, incomplete
external evidence, dynamic/unmodeled coverage, separate resources, and stale
actions. Three registered mutations remove the joint intersection, the 4.0 marker
boundary, or the complete-external-proof requirement and must fail the designated
behavior assertions. Real host usability remains separate acceptance evidence.
