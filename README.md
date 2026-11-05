# Argo Workflows language server

Local YAML diagnostics, completion, hover, navigation and safe edits for Argo Workflows.
The Go language server works independently; the Zed extension is a thin adapter.

**Alpha:** default support covers **3.0.0–4.1.6**, including the stable patch releases
in that window. All eight official CRDs are covered. No project configuration or
target version is needed.

## Install in Zed

1. Download **[zed-argo-workflows-0.0.1-alpha.1.zip](https://github.com/lazysnail1024/zed-argo-workflows/releases/download/zed-v0.0.1-alpha.1/zed-argo-workflows-0.0.1-alpha.1.zip)**
   from the [Zed alpha release](https://github.com/lazysnail1024/zed-argo-workflows/releases/tag/zed-v0.0.1-alpha.1).
2. Close Zed, extract the ZIP, and copy its **`argo-workflows-lsp`** folder into:

   | OS | Installation directory |
   | --- | --- |
   | Linux | `$XDG_DATA_HOME/zed/extensions/installed/` (default `~/.local/share/zed/extensions/installed/`) |
   | macOS | `~/Library/Application Support/Zed/extensions/installed/` |
   | Windows | `%LOCALAPPDATA%\Zed\extensions\installed\` |

3. Start Zed and open a project folder containing Argo Workflows YAML. The adapter downloads
   the matching native server and checks its SHA-256 and identity. Subsequent starts
   work offline. Keep Zed's general YAML support enabled.

This precompiled package needs **no Rust or Go installation**. The native download
supports Linux amd64/arm64, macOS amd64/arm64 and Windows amd64. This is a manual
alpha installation; the extension is not yet in the Zed extension store.

For an upgrade, keep a copy of the previous extension folder, close Zed, and replace
it with the new release's folder. [Checksums, offline setup and troubleshooting](docs/distribution.md).

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

Download your platform archive from the [LSP alpha release](https://github.com/lazysnail1024/zed-argo-workflows/releases/tag/v0.0.1-alpha.1),
extract it, and run:

```sh
argo-workflows-ls check --root . --format json
argo-workflows-ls serve --stdio
```

Analysis is local: it does not access a cluster, execute controllers or render Helm
implicitly. The alpha includes automated CLI/LSP and package checks; real Zed GUI
acceptance is recorded separately. [Development and tests](DEVELOPMENT.md) ·
[Architecture](docs/architecture.md) · [MIT license](LICENSE)
