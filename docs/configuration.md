# Configuration

Configuration is optional. The defaults are automatic compatibility, an open
reference policy, no assumed namespace, no user exclusions and one root scope.
The server reads `.argo-workflows-ls.yaml` at each workspace root; it does not
search every nested Git repository for another product configuration.

## Project file

This example separates two inventories and assigns their known controller versions:

```yaml
schemaVersion: 1
targetVersion: auto
referencePolicy: closed
defaultNamespace: workflows
exclude:
  - "generated/**"
scopes:
  - name: production
    include: ["production/**"]
    targetVersion: "3.6.0"
  - name: development
    include: ["development/**"]
    targetVersion: "4.1.6"
```

Choose `closed` only when each scope contains the resources intended to resolve
there. Remove it or use `open` for partial checkouts. Only set `defaultNamespace`
when it describes the resources in this workspace; it is an analysis assumption,
not a namespace discovered from Kubernetes.

| Key | Default | Current behavior |
| --- | --- | --- |
| `schemaVersion` | `1` | Schemas `1` and `2` are accepted. Named render profiles require `2`. |
| `targetVersion` | `auto` | Accepts the bundled automatic mode or an exact supported release, such as `3.6.0`; no nearest-version fallback. |
| `referencePolicy` | `open` | `open` leaves missing external resources as Information; `closed` can report Error only after the selected scope is complete and the resource identity is known. |
| `defaultNamespace` | Empty | Used when a namespaced resource omits `metadata.namespace`; an empty value leaves the namespace unknown. ClusterWorkflowTemplate remains cluster-scoped. |
| `exclude` | Empty list | Root-relative glob list for disk membership. Explicitly opened excluded files remain available in isolated `opened:<URI>` scopes. |
| `scopes` | Empty list | Without entries, files use `root`. With entries, each file belongs to one matching scope or `unassigned`; multiple matches are an error. |
| `scopes[].name` | Required | Nonempty and unique; `unassigned` is reserved. |
| `scopes[].include` | Required | Nonempty list of root-relative globs; controls membership and cross-file reference boundaries. |
| `scopes[].targetVersion` | Inherits root | Overrides the root target for that scope. |
| `renderProfiles` | Empty | Schema `2` only: named saved-chart render settings; see below. Schema `1` rejects this key even when empty or null. |

Globs use `/` on every platform. `**` must occupy a complete segment. Absolute
patterns, backslashes, empty path segments and `..` are rejected. The parser is
strict: unknown fields and duplicate keys fail configuration rather than being
silently ignored. Configuration text is limited to 1 MiB.

Disk discovery accepts `.yaml` and `.yml`, excludes the product configuration
itself and directories named `.git`, `vendor`, `node_modules` or `.argo-ls`, and
does not follow child symlinks. A nested workspace root owns its own files.
These rules are separate from a file's GVK and from Helm action recognition.
See [config.go](../internal/workspace/config.go) and
[stream.go](../internal/workspace/stream.go) for the executable rules.

## Version selection and editor settings

The effective target order is:

1. CLI `--target-version`, or the LSP client's `targetVersion` setting.
2. The matching scope's explicit `targetVersion`.
3. The workspace file's `targetVersion`.
4. `auto`.

The CLI does not read Zed settings. The LSP accepts `initializationOptions.targetVersion`
and updates through `workspace/didChangeConfiguration` with `settings.targetVersion`.
The Zed adapter forwards its product settings. For example:

```json
{
  "lsp": {
    "argo-workflows-ls": {
      "settings": {
        "targetVersion": "3.6.0",
        "serverPath": "/absolute/path/to/argo-workflows-ls"
      }
    }
  }
}
```

Both settings are optional. `serverPath` is an adapter selection setting, not a
key in `.argo-workflows-ls.yaml`; Windows uses an absolute `.exe` path. Without
it, the adapter tries the worktree shell PATH, then its exact native release pin.
Remove the editor target override to let project scopes choose their versions;
set it to `auto` only when intentionally overriding every scope to automatic mode.
Removing all explicit target settings restores the default.

The bundled catalog covers **146 exact versions from 3.0.0 through 4.1.6**.
Automatic mode requires one complete supported schema to accept a document;
it does not combine fields that never coexist in a release. The inventory is
embedded and offline. Its source cutoff, all eight CRDs, introduction boundaries,
query behavior and update procedure are documented in
[multi-version-support.md](multi-version-support.md).

## CLI commands and exit codes

```sh
argo-workflows-ls version --json
argo-workflows-ls explain-config --root .
argo-workflows-ls explain-config --root . --file example.yaml
argo-workflows-ls check --root . --format json
argo-workflows-ls check --root . --scope production --target-version 3.6.0 --format json
argo-workflows-ls serve --stdio
```

`explain-config` reports root, configuration, targets and unevaluated inputs; it
uses the CLI workspace loader, so it also performs disk collection.
With `--file`, it adds an `analysis` explanation for that saved file. Relative file
paths are resolved from `--root`. `inputIdentity` records `source: disk`, the URI,
SHA-256 and effective configuration hash; CLI never reads editor overlays.

The editor's **Argo Workflows: explain current analysis** source action uses the
current buffer instead. It displays the owning root and scope, the roots actually
passed to this server, document and selected-scope completeness, and an actionable
Rename refusal at the cursor. It is available without versioned-edit capability.
Clients may also request `argo-workflows/explainAnalysis` with `textDocument.uri`
and an optional LSP `position` to receive the same structured projection without
a message. `workspace/executeCommand` uses `argo-workflows.explainAnalysis` and the
arguments returned by the source action; changed buffer/configuration/root inputs
invalidate that action. Explanations are requested explicitly and do not generate
background message notifications.

At a static `templateRef.name` or `workflowTemplateRef.name` whose caller has no
known namespace, the explanation also includes `referenceCandidates`. These are
same-name `WorkflowTemplate` resources from the selected scope, explicitly
labeled **binding unproven**. Each item reports `kind`, `name`, `namespace`, an
exact source `location`, and the `TARGET_UNKNOWN` reason. An empty candidate
namespace stays unknown; the service does not invent a default namespace.
Existing explicit namespaces and a configured `defaultNamespace` retain their
ordinary binding rules. Cluster-scoped and dynamic calls do not use this view.

`referenceCandidates.complete` describes candidate enumeration only;
`scopeComplete` separately reports the selected scope's readiness. Known fresh
candidates can be shown while that scope is incomplete, with both flags false.
`truncated` remains true if either the index's 1000-entry substring search or
the 20-item display limit is reached. Exact-name filtering does not erase
truncation. Duplicate declarations remain separate items, including resources
in different documents of one file. Inexact declarations are not offered as
precise locations and prevent an enumeration-complete claim. None of these
flags establishes a binding, reference completeness, or permission to edit.

The **Argo Workflows: open candidate … (binding unproven)** source actions use
kind `source.openReferenceCandidate` and command
`argo-workflows.openReferenceCandidate`. They remain available without
versioned-edit support and never contain a WorkspaceEdit. Resolve and execute
revalidate the source buffer, configuration, selected scope, target bytes and
location; changed inputs return `STALE_INPUT` and require a new action. With
`window.showDocument.support`, execution requests the exact source selection.
Otherwise it returns `{location, message}` and displays instructions for opening
that location manually. A successful command does not claim that the client has
already opened a document. Clients should execute the arguments returned by the
action instead of constructing or retaining resource locations themselves.

Both file explanation paths include `analysis.compatibility` (or `compatibility`
in the custom LSP result). It contains catalog and modeled-rule provenance hashes
and separate records for each owned resource in the YAML stream. Each record has
`schemaCandidates`, `semanticCandidates`, their `candidates` intersection,
versioned `exclusions`, and `unknowns`. `evaluated: false` leaves candidate arrays
null; it does not claim a proven empty set. `modeledComplete: false` means some
modeled context or explicit coverage area remains unknown even if evaluation
returned possible candidates. The message labels these **candidates under modeled
static constraints** and summarizes up to four resources; the structured result
retains all resource records. See [the full contract](multi-version-support.md).

Compatibility work runs only for explicit explanation requests. Normal diagnostics,
completion, hover and opening files keep their existing paths. An incomplete scope
can still have complete local candidate information, but partial external findings
cannot eliminate versions. The source action retains its existing input and scope
publication proofs; a candidate list never authorizes a later edit.

Rename errors include `error.data.reasonCode`, `nextStep` and `relatedLocations`.
The originating safety guard supplies codes such as `INDEX_INCOMPLETE`,
`AMBIGUOUS_BINDING`, `TARGET_UNKNOWN`, `AFFECTED_FILE_NOT_OPEN`,
`DYNAMIC_REFERENCE`, `REFERENCE_POLICY_OPEN` and `STALE_INPUT`. These explain
existing requirements; no response authorizes a later edit without revalidation.
`references.complete: false` means a zero count cannot prove there are no references.
Reference certainty is deliberately conservative: it reuses the modeled closed
binding proof, independently of client edit capability. Some navigable but
non-renamable symbols therefore remain unknown; a false flag does not erase the
known locations or claim the symbol has no references.
An unavailable configuration or dynamic Helm source is reported as unavailable,
not as successfully checked empty input.

`check --file path/to/file.yaml` filters emitted diagnostics after the selected
scope is loaded and checked; it is not a current-file-only analysis shortcut.
`--warnings-as-errors` also fails on Warning diagnostics.

| `check` exit code | Meaning |
| --- | --- |
| `0` | Analysis completed with no Error diagnostics, and no Warning when `--warnings-as-errors` is selected. |
| `1` | Analysis completed and diagnostics meet the failure threshold. |
| `2` | Invalid command/configuration, incomplete non-timeout analysis, or JSON output write failure. |
| `3` | Analysis context was cancelled or reached its deadline. |

The JSON report's `analysisComplete`, `notEvaluated`, `configurationErrors`,
per-target completeness, counts and diagnostics must agree with the exit status.
The product's five-second analysis deadline includes workspace acquisition and
schema work. LSP discovery instead persists across independent request deadlines.
`check --help` prints the accepted flags; like other flag-parser errors it returns
`2`, so it is not a successful analysis probe.

`render-check --file charts/example/templates/workflow.yaml` explicitly renders a
saved chart with Helm 3 or 4 and checks generated YAML. `lint-official` explicitly
runs a supplied Argo CLI; neither command runs implicitly while editing. For
render settings and source-navigation limits, see the [README](../README.md).

## Saved render profiles and generated cache

Use a named profile when a chart needs repeatable values, namespace or capability
settings. The existing default render action and CLI defaults remain available.

```yaml
schemaVersion: 2
targetVersion: "3.6.0"
renderProfiles:
  dev:
    chart: charts/demo
    values: [env/common.yaml, env/dev.yaml]
    releaseName: acceptance
    namespace: testing
    kubeVersion: "1.31.0"
    apiVersions: [example.com/v1]
  prod:
    chart: charts/demo
    values: [env/common.yaml, env/prod.yaml]
    namespace: production
```

Profile names contain 1–64 ASCII letters, digits, dots, dashes or underscores and
start with a letter or digit. `chart` is required. Chart and values paths are relative to the owning workspace
root, use `/`, and cannot escape it through `..`, absolute paths or symlinks.
Values are applied in their declared order; repeated paths are retained. Omitted
release name, namespace and Kubernetes version use `argo-ls`, `default` and
`1.31.0`. `apiVersions` adds explicit capabilities. Unknown keys, duplicate profile
names, invalid path syntax and flag-shaped release names are rejected. This is a
fixed parameter set; profiles do not contain executable commands or arbitrary
Helm flags.

```sh
argo-workflows-ls render-check --root . --profile dev
argo-workflows-ls render-check --root . --file charts/demo/templates/workflow.yaml --profile prod
argo-workflows-ls render-cache
argo-workflows-ls render-cache --remove /absolute/path/from/the/cache/list
```

With `--file`, the selected source chooses scope and must belong to the configured
chart. Without it, a profile derives scope from its `Chart.yaml`. `--scope` must
agree with that membership; it cannot borrow another scope's target. Existing
target override precedence still applies. A profile cannot be combined with
`--chart`, `--values`, `--release-name`, `--namespace`, `--kube-version` or
`--api-version`, even if an explicit flag repeats the profile value. `--output`
and `--helm-binary` remain available. Outside profiles, CLI values paths retain
their current-working-directory interpretation.

When conservative source projection recognizes an Argo Workflows resource, the
editor offers the default action plus **render and check saved chart (dev)**
and other profiles for the selected owned chart. Helm validity alone does not
prove this source ownership: for example, an unquoted scalar concatenating two
actions with a dash may prevent recognition. Use the explicit CLI operation for
such charts, or a semantically equivalent whole-scalar Helm expression. Listing or resolving actions
never starts Helm. Execution uses the same resolved request as the CLI and
requires saved chart, configuration and chosen values buffers, including values
outside the chart and aliases of those files. It rechecks the selected buffer's
lifetime, root/scope, profile, ordered values paths and input bytes. A close/reopen
with the same document version also invalidates an old action. Save changes and
request a fresh action to recover. Accepted input changes or cancellation prevent
late messages and generated-document requests; a host's response to
`window/showDocument` leaves the language-server connection usable.
An explicit execution refused before Helm starts also shows the actual guard
reason in a message, so hosts that hide command error details still explain
which inputs need attention. Collecting or resolving actions remains silent.

Every default output uses a unique directory under
`UserCacheDir/argo-workflows-ls/render`. The rendered YAML, raw Helm stdout/stderr
and `render-report.json` stay outside the source chart. Reports record profile,
scope/target, resolved arguments, ordered values with hashes, executable path/hash
and version, input hashes, request identity and input-change status. Helm failure,
cancellation and stale input retain their available raw streams and report; a
failed render does not claim generated YAML exists. Inputs are bounded to 20,000
files and 128 MiB, and combined subprocess output to 10 MiB. Chart symlinks are
refused because they prevent complete local provenance.

`render-cache` lists immediate generated directories with an `owned` flag and
refusal reason. Removal is explicit and verifies the product marker, native
directory identity and exact file inventory, content and modes through held
directory handles. Changed files, extra user files, symlinks, copied markers and
foreign directories are refused. It never recursively sweeps a directory.
Normal concurrent changes can cause a refusal, possibly after some verified
artifacts were already removed; cleanup is not a transactional filesystem
operation. Failed/cancelled inventories are removable when their integrity is
intact. Unsealed or changed directories remain available for manual inspection.
An explicit `--output` directory is never cleanup-owned, including one placed
under the normal cache parent. Both custom and managed output locations must
resolve outside the chart. Custom output must start empty. Its directory handle
and placement identity remain bound across Helm execution, and fixed artifacts
are created exclusively through that handle. Replacing the output pathname
refuses further writes and publication; the CLI failure response retains raw
Helm streams even when the displaced directory cannot safely receive a report.
There is no automatic cleanup or eviction.

## Optional work tracing

Set `ARGO_WORKFLOWS_LS_TRACE=1` before starting the native LSP to emit structured
`phase:work` records on stderr. It is disabled by default. Records identify the
request ID, method, per-session sequence and work origin (`request`, `input`,
`index`, `header` or `publication`); they contain counts and timings, without
document text or a telemetry endpoint. Zero counters are omitted.

The records count actual source/configuration/render-input acquisition, YAML
parser and projection calls, embedded Schema loads and compilation, product
Schema query calls, domain work and fact/result reuse. A cache hit can still
perform current disk reads and synthetic or recovery parsing. Directory scans,
generated-cache integrity I/O and the external tool's own work are outside these
input-read counters. Existing `phase:index` summaries retain their original
meaning and are not a count of every parser invocation.

Foreground `start` and `finish` records distinguish queue time, worker service,
terminal reply and worker-slot release. Cancellation records retain the work
already performed and separate `cancelToReplyNanoseconds` from
`cancelToReleaseNanoseconds`. A `delivered` outcome means the existing owner
accepted and wrote the response, which can itself contain a refusal. Trace data
does not grant input authority or relax an edit guard. Full definitions and
validation limits are in [the architecture](architecture.md#实际工作观测).

## Boundaries

Static validation does not execute controllers, Kubernetes admission or dynamic
expressions. Missing or ambiguous sources do not receive guessed edits. A closed
scope alone does not authorize Rename: current input, complete scope, unique
binding, precise source locations and affected open-file versions must also be
proven. Input, index and analysis-budget failures remain visible as incomplete
analysis. See [architecture](architecture.md) and [troubleshooting](distribution.md#troubleshooting-and-editor-acceptance).

Before publishing a Rename or resolved Quick Fix `WorkspaceEdit`, the session
owner reacquires the effective root configuration and compares it with the
configuration used to authorize that edit. An accepted configuration buffer
remains authoritative; otherwise this uses a fresh bounded disk read. Changed,
invalid or unavailable configuration refuses the edit with `STALE_INPUT`, even
without a watched-file notification. Existing input and catalog proofs are
checked again after that read. This is a delivery-time check, not an atomic
filesystem snapshot or permission to reuse an earlier Prepare Rename result.
Read-only explanations still describe the configuration observed by the catalog;
they do not themselves authorize an edit. The extra read/decode is attributed to
the edit request's opt-in work trace, after its worker service and before reply.
