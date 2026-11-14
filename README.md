# Argo Workflows language server

Local YAML diagnostics, completion, hover, navigation and safe edits for Argo Workflows.
The Go language server works independently; the Zed extension is a thin adapter.

**Alpha:** default support covers **3.0.0–4.1.6**, including the stable patch releases
in that window. All eight official CRDs are covered. No project configuration or
target version is needed.

## Install in Zed

Close Zed, then run one command:

Linux / macOS:

```sh
curl -fsSL https://raw.githubusercontent.com/lazysnail1024/zed-argo-workflows/main/editors/zed/install.sh | sh
```

Windows (PowerShell):

```powershell
irm https://raw.githubusercontent.com/lazysnail1024/zed-argo-workflows/main/editors/zed/install.ps1 | iex
```

Restart Zed and open a project containing Argo Workflows YAML. **Run the same
command to upgrade.** The installer verifies the release ZIP against
`SHA256SUMS`, preserves one previous installation for rollback, and leaves
Zed's native-server cache alone. If you prefer to inspect a script before
running it, download it from [`editors/zed/`](editors/zed/) first.

This precompiled package needs **no Rust or Go installation**. The native download
supports Linux amd64/arm64, macOS amd64/arm64 and Windows amd64. This is a manual
alpha installation; the extension is not yet in the Zed extension store.

[Rollback, manual/offline installation and troubleshooting](docs/distribution.md).

## Try it

Save this as `example.yaml`:

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

Hover a field or request completion inside `spec`. Change
`entrypoint: main` to
`entrypoint: missing` to check diagnostics, then restore it.

The default compatibility mode accepts content supported by a version in the
window. It does not guess your cluster version. An existing explicit `targetVersion`
still requests strict checking; remove that key to restore automatic compatibility.
[Version behavior and advanced configuration](docs/configuration.md).

## Navigation and safe edits

The current source supports definition/references, workspace symbols, local
template/name edits and static WorkflowTemplate/ClusterWorkflowTemplate resource
renames. Cross-file resource renames require a complete scope with `closed`
reference policy, a known namespace for namespaced resources, a unique definition,
and every affected file open with a client version. Dynamic, shared or ambiguous
source references cannot be renamed safely.

Current-file diagnostics and proven local fixes can respond while the workspace
index is still being built. Hover, completion and document symbols have a separate
bounded request lane. See [current capabilities and boundaries](docs/architecture.md).
Installed releases use their recorded source commit; development capabilities do
not imply that an older released binary contains them.

## Helm charts

Install Zed's Helm extension for Helm syntax and Helm language-server features.
If Zed classifies your charts as YAML, add one-time **global** settings with globs
adapted to your actual chart directories:

```json
{
  "file_types": {"Helm": ["**/charts/*/templates/**/*.yaml", "**/charts/*/templates/**/*.yml"]},
  "languages": {"Helm": {"language_servers": ["helm", "argo-workflows-ls", "..."]}}
}
```

Do not use `**/templates/**`: ordinary repositories can have unrelated template
folders. If both Argo extensions are installed, add `argocd-ls` to that list too.
These editor language associations are separate from product configuration.

Ordinary YAML in `templates/` keeps normal Workflows diagnostics, hover and navigation.
Actual Helm source offers limited **Go to Definition** for literal template names,
local templates, `templateRef` targets in the same chart and literal escaped Argo
tags such as ``{{ `{{workflow.parameters.message}}` }}``. Positions refer to
the original source, including UTF-16 and CRLF. Simple scalar `.Values`, `.Release`
and `.Chart` expressions (optionally `| quote`) are treated as unknown values.

This does not evaluate Helm: `if`, `range`, `include`, other helpers, multiline
actions and non-ASCII action bodies decline source navigation. Dynamic resource
names do not bind. Helm source does not receive schema success, completion,
references or rename from this server; unsupported queries return empty results.

To validate rendered output, save your chart buffers, run **editor: toggle code
actions** from the command palette, then choose
**Argo Workflows: render and check saved chart** on a Workflow template. It runs
only when selected, uses Helm from `PATH`, opens generated YAML when the editor
supports it, and reports the check exit code. No `.zed/tasks.json` or product configuration is needed. It never
runs on save. Helm 3 and 4 are accepted; chart `values.yaml` is loaded by Helm.

The same explicit operation is available in a terminal:

```sh
argo-workflows-ls render-check --file charts/example/templates/workflow.yaml
# Or run inside a chart (including its templates directory):
argo-workflows-ls render-check
```

The nearest ancestor `Chart.yaml` is selected. `--chart`, `--helm-binary`, `--values`,
`--output`, release/namespace and capability flags remain available. Each default
output gets a fresh directory under the user cache, outside the chart, with rendered
YAML, Helm stdout/stderr and provenance. Diagnostics point to generated YAML;
source files are unchanged. Delete obsolete cache runs when no longer needed.

Open a common parent folder to navigate across multiple Git repositories in one
Workflows index. In Zed 1.28.1, folders added as separate visible roots remain
separate indexes; adding folders to the same window does not merge their scopes.

## Standalone CLI / other editors

Download your platform archive from the [LSP alpha release](https://github.com/lazysnail1024/zed-argo-workflows/releases/tag/v0.0.1-alpha.4),
extract it, and run:

```sh
argo-workflows-ls check --root . --format json
argo-workflows-ls serve --stdio
```

Analysis is local: it does not access a cluster, execute controllers or render Helm
implicitly. The alpha includes automated CLI/LSP and package checks; real Zed GUI
acceptance is recorded separately. [Development and tests](DEVELOPMENT.md) ·
[Documentation](docs/README.md) · [Architecture](docs/architecture.md) · [MIT license](LICENSE)

<!-- Temporary documentation-only CI verification. -->
