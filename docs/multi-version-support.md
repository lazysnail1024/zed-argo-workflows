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

## Source update procedure

1. Run `python3 scripts/collect-catalog.py` to record upstream release metadata,
   the Git tag inventory, commit identities and content-addressed CRD sources.
2. Run `python3 scripts/normalize-schemas.py` to generate exact schemas and the
   compact automatic alternatives, reject unsupported constraints, and remove
   obsolete generated schema blobs.
3. Rebuild the native server and run the version acceptance and ablation suites.

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

Run these consumers with a built native binary and a new output directory:

```sh
python3 tests/e2e/wf-multi-version.py /path/to/argo-workflows-ls /path/to/acceptance
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
