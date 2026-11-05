# Zed Argo Workflows

Offline YAML support for Argo Workflows `Workflow`, `CronWorkflow`, `WorkflowTemplate`, and `ClusterWorkflowTemplate`: diagnostics, completion, hover, navigation, references, and restricted safe edits. It does not connect to a cluster or execute workflows.

**Store publication is pending.** The adapter now supports verified downloads of a native server pinned to extension version `0.1.0`, but no matching public Release or Zed registry entry has been published as part of this change. Until that release exists, use a native build or a verified developer package with `settings.serverPath`. The default and accepted local-editing target is **3.6.0**. Other known versions remain experimental; their checks are incomplete and semantic edits are disabled.

## Install

The adapter resolves its server in this order:

1. An absolute `lsp.argo-workflows-ls.settings.serverPath` override.
2. An `argo-workflows-ls` executable found in the project environment's PATH.
3. The matching native asset from the pinned GitHub Release `v0.1.0`.

An explicit or discovered executable must pass the Workflows identity/readiness handshake. Invalid selections fail visibly; remove or correct them to use managed installation. Managed downloads use Zed's Rust Extension API. The adapter downloads `SHA256SUMS` and the platform executable, verifies SHA-256 **before executing it**, then validates its release identity and activates it in a version/platform cache. A valid cached server starts without network access and is rehashed on each startup. The first managed installation and the first startup after an upgrade require network access and the matching published release. See [Distribution and store readiness](docs/DISTRIBUTION.md) for asset names, recovery and the manual editor checklist.

### Developer installation before the first release

You need Zed with YAML support enabled and Rust through **rustup**. The adapter pins Rust **1.99.1** and `wasm32-wasip2`; initial Rust toolchain and dependency downloads require network access. A verified developer package includes a separate native server, so it needs no Go or C compiler on the user's machine.

1. Obtain the platform-specific `zed-argo-workflows-dev-<os>-<arch>.zip` and its checksum from the same identified Actions run or maintainer delivery; verify the checksum before extraction.
2. Extract it into a permanent directory. Use its `settings.example.json` as a template and set `lsp.argo-workflows-ls.settings.serverPath` to the absolute path of `server/argo-workflows-ls` (`.exe` on Windows).
3. In Zed choose **Install Dev Extension** and select the extracted **`extension/`** directory, which contains `extension.toml` and `LICENSE`.
4. Open a project folder and a Workflows YAML file. On POSIX hosts, run `chmod +x server/argo-workflows-ls` if the unzip tool did not preserve execute permissions.

The server stays outside the extension source and WASM. To upgrade a developer package, verify and extract the new ZIP into a new directory, update `serverPath`, install its `extension/` directory and restart the server. Keep the previous directory for an explicit rollback. Linux amd64, Linux arm64, macOS amd64, macOS arm64 and Windows amd64 have native CI jobs; no platform's new installer has yet been accepted in a real Zed session.

For Rename in a Workflow project, optionally add this to the project's `.zed/settings.json` so Zed routes the operation to Workflows while retaining other YAML servers:

```json
{
  "languages": {
    "YAML": {
      "language_servers": ["argo-workflows-ls", "..."]
    }
  }
}
```

The `"..."` entry retains the other registered servers, including general YAML support. See [Zed's language-server ordering](https://zed.dev/docs/configuring-languages#choosing-language-servers). Source checkout builds and explicit executable overrides are documented in [Development](DEVELOPMENT.md).

## First workflow

Open a project folder and save this as `workflow.yaml`:

```yaml
apiVersion: argoproj.io/v1alpha1
kind: Workflow
metadata:
  name: hello
spec:
  entrypoint: main
  templates:
    - name: main
      container:
        image: alpine:3.20
        command: [echo]
        args: [hello]
```

Hover `entrypoint`, request completion inside `spec`, or go to the `main` definition. Temporarily change `entrypoint` to `missing` to see a local-template diagnostic, then restore it. No image is downloaded and no workflow is submitted.

## Configuration

No version pin is required for 3.6.0. To make the choice explicit, add `.argo-workflows-ls.yaml` at the project root:

```yaml
schemaVersion: 1
targetVersion: "3.6.0"
referencePolicy: open
defaultNamespace: argo
```

`open` reports unresolved external resources as information. `closed` permits an error when the local scope is complete. Add named `scopes` with `include` globs and optional `targetVersion` overrides to isolate unrelated workflows. Cross-root and cross-scope references do not bind.

An explicit `lsp.argo-workflows-ls.settings.targetVersion` overrides file pins; removing it restores the file pin or default. CLI commands do not read Zed settings. Unknown versions are rejected without fallback. Known versions other than 3.6.0 produce an experimental warning in the editor and incomplete CLI reports. They do not receive Rename or Quick Fix edits. An experimental scope does not disable edits in an independent 3.6.0 scope.

## CLI

From the extracted bundle folder (on POSIX systems, run `chmod +x server/argo-workflows-ls` if your unzip tool did not preserve executable permissions):

```sh
./server/argo-workflows-ls explain-config --root /path/to/project
./server/argo-workflows-ls check --root /path/to/project --format json
./server/argo-workflows-ls check --root /path/to/project --warnings-as-errors
```

Exit codes are **0** for complete analysis without errors, **1** for errors (or warnings with `--warnings-as-errors`), **2** for configuration/input/analysis incompleteness, and **3** for cancellation or deadline. The warnings flag changes the exit decision, not diagnostic severity. `--scope` selects an analysis scope; `--file` only filters diagnostic output and retains scope dependencies. `--target-version` overrides the target explicitly.

`analysisComplete` means the input and scope finished the **local static editing contract**. It does not mean controller execution, official lint equivalence, or dynamic value evaluation. An informational unresolved reference under `open` does not alone make a check incomplete. Actual input/projection refusals, incomplete indexes, budget exhaustion and experimental targets do.

Explicit `render-check` and `lint-official` commands use user-selected local Helm or Argo executables. They never run on save and do not fetch dependencies automatically. Their own incomplete/tool-result status is separate from the core check.

## Explicit Helm render task

Copy the entry in [examples/zed-tasks.json](examples/zed-tasks.json) into your project's `.zed/tasks.json` (merge with existing tasks). This POSIX/Linux example requires a shell and `mktemp`. Replace both absolute executable paths, chart and values paths before running it with **task: spawn**. The task explicitly selects Helm 3, chart, values and target 3.6.0 and creates a new evidence directory on each invocation. It never runs on save or downloads chart dependencies.

The terminal shows the check report and evidence directory. Inspect `render-report.json` for the command, Helm version, exact input SHA-256 values and target; `helm.stdout`/`helm.stderr` preserve render output. A Helm failure is a tool error; successful rendering followed by Argo errors returns the check's diagnostic report. `rendered.yaml` is the source of diagnostic locations: no Helm-source mapping is guessed. Inputs changed during execution cause an explicit refusal; rerun after editing. Cancel the task in the terminal to stop it; commands also have a 30-second budget. Missing executables, wrong Helm major versions and incomplete checks remain visible failures.

Chart symlinks and input sets exceeding 128 MiB or 20,000 chart files cannot provide complete provenance and are refused. Ordinary `templates/` directories without `Chart.yaml` and plain Argo expressions continue to use normal YAML analysis.

## Troubleshooting and limits

- If startup fails, check that the bundle matches your OS and architecture. Remove stale `serverPath` overrides. The adapter refuses invalid overrides and damaged metadata; reinstall the bundle if its files were modified.
- Use `zed: open log` for adapter build and startup errors. Ensure rustup and the pinned toolchain are available to Zed.
- Confirm the file uses YAML mode and the language server is enabled. Keep general YAML support enabled; existing schema associations may produce duplicate diagnostics.
- Hover a supported field to see the effective target, accepted/experimental status, scope and selection source. Use `explain-config` to inspect effective targets. Remove stale Zed target overrides before troubleshooting file pins.
- `WF_ANALYSIS_UNAVAILABLE` means acquisition or configuration failed; `WF_ANALYSIS_INCOMPLETE` means only part of the input could be checked. Fix the reported cause to recheck. An empty successful diagnostic list is not used for these failures.
- The configuration file itself reports `WF_CONFIGURATION_ERROR` for invalid syntax or effective target settings. Correcting it clears that error and rechecks affected workflows; a valid configuration does not receive workflow reference-analysis warnings.
- Analysis is bounded and local. Unsupported dynamic or nested inheritance behavior is not executed or guessed. External ConfigMap values are not read. Derived, encoded or shared origins do not acquire invented navigation/edit spans.
- Rename requires a proven closed local identity. Inherited, cross-file, ambiguous or alias-dependent edits remain refused even when static analysis completes. A private Workflow or CronWorkflow can rename a proven local template name beside an unrelated static `templateRef`; the external name, arguments and public template remain unchanged. Other symbol kinds beside external calls still require a cross-resource proof and are refused. Dynamic external references, `workflowTemplateRef` inheritance, inline templates and shared aliases remain conservative refusals. Quick Fixes preserve exact locations and current document versions.

Detailed implementation boundaries and contributor checks are in [Development](DEVELOPMENT.md). Native builds, stdio behavior and real Zed installation are separate verification layers; no five-platform or registry-release claim is implied.

## License and acknowledgements


Original code and documentation are [MIT licensed](LICENSE), copyright 2026 snail (lazysnail1024). Third-party source, schema data, and dependencies retain their original licenses. Redistribution must include the applicable license and notice files; the project license does not replace them.

- [k8s-crd-toolkit](https://github.com/lazysnail1024/k8s-crd-toolkit): MIT; immutable text, YAML source views, and checked edits. Exact snapshot: `contracts/toolkit.lock.json`.
- [zed_extension_api](https://github.com/zed-industries/zed): Apache-2.0; pinned to 0.7.0 in `Cargo.lock`. [serde_json](https://github.com/serde-rs/json): MIT or Apache-2.0, pinned to 1.0.145.
- [Expr](https://github.com/expr-lang/expr): MIT; v1.16.9 syntax parsing for restricted Rename. Its license retains upstream attribution. A [recorded local patch](contracts/expr-patch.json) makes error caret formatting linear without changing parser semantics or error text.
- [glsp](https://github.com/tliron/glsp): Apache-2.0; [jsonschema/v6](https://github.com/santhosh-tekuri/jsonschema): Apache-2.0; [sigs.k8s.io/yaml](https://github.com/kubernetes-sigs/yaml): MIT, BSD, and Apache-2.0 notices in vendor. Exact versions: `go.mod` and `go.sum`.
- Tree-sitter runtime/grammar and go-pointer retain their MIT notices. Tree-sitter Unicode data has a separate license. `golang.org/x/text` retains its BSD-style license; CJK string data is escaped without changing values, and one explanatory comment is rendered in English.
- [Argo Workflows](https://github.com/argoproj/argo-workflows): Apache-2.0. `compat/catalog.lock.json` fixes upstream commits, CRD hashes, schema mappings, and source attribution. Raw CRDs preserve upstream content; embedded schemas are derived by converting nullable, exclusive numeric bounds, and Kubernetes int-or-string constraints. The upstream license is in `schemas/LICENSE`.
- [PyYAML](https://github.com/yaml/pyyaml): MIT; build-time schema conversion only, not part of the running server.
- Extension configuration conventions were informed by [helm.zed](https://github.com/cabrinha/helm.zed) and [zed-ansible](https://github.com/kartikvashistha/zed-ansible), both MIT licensed. Their implementation code was not copied.
