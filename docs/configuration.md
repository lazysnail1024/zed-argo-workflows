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
| `schemaVersion` | `1` | Only configuration schema `1` is accepted. |
| `targetVersion` | `auto` | Accepts the bundled automatic mode or an exact supported release, such as `3.6.0`; no nearest-version fallback. |
| `referencePolicy` | `open` | `open` leaves missing external resources as Information; `closed` can report Error only after the selected scope is complete and the resource identity is known. |
| `defaultNamespace` | Empty | Used when a namespaced resource omits `metadata.namespace`; an empty value leaves the namespace unknown. ClusterWorkflowTemplate remains cluster-scoped. |
| `exclude` | Empty list | Root-relative glob list for disk membership. Explicitly opened excluded files remain available in isolated `opened:<URI>` scopes. |
| `scopes` | Empty list | Without entries, files use `root`. With entries, each file belongs to one matching scope or `unassigned`; multiple matches are an error. |
| `scopes[].name` | Required | Nonempty and unique; `unassigned` is reserved. |
| `scopes[].include` | Required | Nonempty list of root-relative globs; controls membership and cross-file reference boundaries. |
| `scopes[].targetVersion` | Inherits root | Overrides the root target for that scope. |

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

## Boundaries

Static validation does not execute controllers, Kubernetes admission or dynamic
expressions. Missing or ambiguous sources do not receive guessed edits. A closed
scope alone does not authorize Rename: current input, complete scope, unique
binding, precise source locations and affected open-file versions must also be
proven. Input, index and analysis-budget failures remain visible as incomplete
analysis. See [architecture](architecture.md) and [troubleshooting](distribution.md#troubleshooting-and-editor-acceptance).
