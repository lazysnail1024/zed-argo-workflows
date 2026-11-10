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

## Standalone CLI / other editors

Download your platform archive from the [LSP alpha release](https://github.com/lazysnail1024/zed-argo-workflows/releases/tag/v0.0.1-alpha.2),
extract it, and run:

```sh
argo-workflows-ls check --root . --format json
argo-workflows-ls serve --stdio
```

Analysis is local: it does not access a cluster, execute controllers or render Helm
implicitly. The alpha includes automated CLI/LSP and package checks; real Zed GUI
acceptance is recorded separately. [Development and tests](DEVELOPMENT.md) ·
[Architecture](docs/architecture.md) · [MIT license](LICENSE)
