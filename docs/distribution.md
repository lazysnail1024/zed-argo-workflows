# Distribution and installation

## Two independent releases

| Component | Tag | Download |
| --- | --- | --- |
| Native LSP / CLI | `v0.0.1-alpha.1` | Platform executable and archive, source/build records, `SHA256SUMS` |
| Zed adapter | `zed-v0.0.1-alpha.1` | `zed-argo-workflows-0.0.1-alpha.1.zip`, adapter record, `SHA256SUMS` |

Both are GitHub **prereleases** before the first `0.0.1`. The adapter uses its
explicit `native-server-version` pin and direct release URL; it never relies on
GitHub's latest-stable-release endpoint. Published versions are not overwritten.
Corrections receive a new alpha number.

The Windows native-cache activation repair is prepared for adapter
`0.0.1-alpha.2`, retaining native LSP `0.0.1-alpha.1`. A source change or passing
PR check is not a published upgrade: the new `zed-v` release must contain its
verified ZIP and `SHA256SUMS` before installation. The published `alpha.1`
adapter retains the older cache activation behavior described in
[the incident lessons](lessons-learned.md).

## Release automation

Merging a change to root `VERSION` into `main` starts the native release workflow.
Merging a change to `editors/zed/extension.toml` starts the independent Zed release
workflow; its version must also match `editors/zed/Cargo.toml`. Each workflow runs
its complete acceptance gate, then creates its version tag at the exact verified
commit and publishes the checked artifacts. Matching version-tag pushes and
manual runs on `main` or that exact tag are also supported. Existing tags are
never moved, and an existing release or draft stops publication.

A change to `.github/workflows/release.yml` on `main` also retries the native
pipeline, so a publication-workflow repair can complete an unpublished version.
It still runs every gate and refuses an existing release, draft, or tag pointing
at another commit; this retry path never replaces already published artifacts.

The Zed publication step checks its committed `native-server-version` pin before
creating a tag or publishing a ZIP. The native release must already be published
and contain `SHA256SUMS` plus raw executables for Linux amd64/arm64, macOS
amd64/arm64 and Windows amd64. Initial native and adapter releases can start
together: a missing native release (HTTP 404) or a draft is polled every 30 seconds
for at most 45 minutes. Permission errors, other HTTP errors, a mismatched tag or
a published release with missing or incomplete required assets fail immediately.
The adapter does not rebuild the server or require it to come from the adapter's
own source commit. If the wait expires, retry the failed publication job after
the pinned native release becomes available.

## Precompiled Zed installation

The quickest route is the one-command Linux/macOS or Windows installer in the
root README. Close Zed first and restart it afterwards. Re-running the installer
upgrades the adapter without touching Zed's downloaded native LSP cache.

The installer selects Zed's documented `extensions/installed` directory for the
current user, downloads the exact `zed-v<version>` release's ZIP and
`SHA256SUMS`, verifies SHA-256, checks the ZIP's extension identity and stages
the files beside the active directory before renaming. Reinstalling identical
files does nothing. The previous version is retained once under
`extensions/.alpha-installer/argo-workflows-lsp.previous` (outside the
`installed` scan directory); a failed replacement restores the previous state,
including an older backup when present.

To undo the most recent install or upgrade, close Zed and use:

```sh
curl -fsSL https://raw.githubusercontent.com/lazysnail1024/zed-argo-workflows/main/editors/zed/install.sh | sh -s -- --rollback
```

On Windows, download `editors/zed/install.ps1` and run
`powershell -ExecutionPolicy Bypass -File ./install.ps1 -Rollback` from its
download directory. A second rollback swaps the two versions back.

For an offline install, download the exact release ZIP and `SHA256SUMS` into
one directory, then run `sh install.sh --assets-dir /path/to/assets` on
Linux/macOS or `./install.ps1 -AssetsDir C:/path/to/assets` in Windows
PowerShell. `--version VERSION` / `-Version VERSION` selects an older published
adapter Release; defaults follow the current alpha release in the script.
If an interrupted upgrade left no active directory but a previous backup exists,
the next run restores it first.

The automated E2E uses isolated HOME/XDG/LOCALAPPDATA and actual public Release
bytes on Linux, macOS and Windows. It tests installation, idempotence, replacement,
checksum/ZIP rejection, rollback and injected activation failure, **not** a real
Zed GUI session. GUI activation remains a separate manual acceptance step.
CI explicitly selects a published fixture with `--version` and records
`sourceAdapterVersion` separately from `exercisedReleaseVersion`; `zed.yml`
independently verifies the new candidate's WASM and package before publication.

The portable ZIP contains `argo-workflows-lsp/extension.toml`, `extension.wasm`, snippets and
licenses. With Zed closed, place that folder below its `extensions/installed`
directory as shown in the root README. This matches Zed's installed extension
layout. Preserve an older folder before replacement; remove this folder while
Zed is closed to uninstall the manual alpha.

The downloaded component is already compiled. A source developer can instead
select `editors/zed` with Zed's **Install Dev Extension** command, which requires
Rust. The extension store is a separate distribution channel and has not been
published by this release.

## Verify downloads

Download `SHA256SUMS` from the same release. On Linux, place the requested assets
alongside it and run `sha256sum --check --ignore-missing SHA256SUMS`. At least the
chosen archive must report `OK`. On macOS, compute `shasum -a 256 <archive>` and
compare the full digest with that archive's exact line in `SHA256SUMS`. On Windows,
use `Get-FileHash <archive> -Algorithm SHA256` and compare the full digest.

A checksum supplied by the same HTTPS release detects corruption and version
mismatch; it is not a separate publisher signature. Archives retain their
original dependency, runtime and schema licenses and exact source identity.

## Server selection and offline use

The adapter chooses:

1. An explicit absolute `lsp.argo-workflows-ls.settings.serverPath`.
2. `argo-workflows-ls` found through Zed's worktree shell PATH.
3. The platform asset from the exact native release pin.

An invalid explicit selection is reported. For managed downloads, SHA-256 is
checked before execution, then the native product, contract, readiness and release
identity are verified. The repaired installer reserves a unique
`servers/v<version>-<platform>.gen2-<nonce>/` directory, verifies the EXE at that
unchanging path, then atomically creates its `.complete/` directory. Unmarked
new generations are ignored, including after interruption. Activation does not
rename a directory after executing its EXE.

Existing fixed-version and `.gen-` caches remain supported without fabricated
markers. Every reused cache is rechecked without network access. Previously
published generations are never moved or deleted during installation or repair;
two installers may safely retain two complete generations. Cleanup of the
current failed attempt is best effort and cannot replace its original error.

For offline first use, extract the native platform archive and put its executable
on PATH, or configure its absolute path in Zed settings:

```json
{
  "lsp": {
    "argo-workflows-ls": {
      "settings": { "serverPath": "/absolute/path/to/argo-workflows-ls" }
    }
  }
}
```

Use a Windows absolute path and `.exe` suffix on Windows. Restore execute bits
with `chmod +x argo-workflows-ls` after ZIP extraction on POSIX systems when necessary.
Existing custom `serverPath` or PATH installations retain priority on upgrades.

## Troubleshooting and acceptance boundary

Use `zed: open log`, check the selected executable with `version --json`, confirm
the exact pinned release and platform asset exist, and restart the language server
after correcting permissions or network access. Keep the general YAML language
server enabled. Ordinary Argo YAML receives semantic analysis; raw Helm template
sources are not silently rendered. Use explicit `render-check` for rendered input.

On Windows, the `alpha.1` pair `Cannot activate verified server` and
`Cannot clean temporary cache servers/.download-*` points to native-cache
activation, not Workflow validation. Its `os error 2` is reported through WASI;
do not treat that number alone as Win32 "file not found". Use the adapter
containing the repair once it is published, or temporarily select an
independently verified EXE with `serverPath` as above. Do not run Zed as
administrator, disable antivirus or purge all extension caches merely to work
around this error. See [cause, evidence limits and prevention](lessons-learned.md).

Native CI executes five actual operating-system/architecture hosts. Rust tests
exercise the cache protocol with injected transport and process boundaries.
The separate WASI runtime acceptance executes the production installer with
real native identity probes and controlled Windows file-sharing restrictions.
Package tests inspect the compiled WASM and archive, and native tests execute
extracted servers. These checks do not claim a full real Zed GUI session on
every platform. Manual GUI acceptance remains separately tracked for this
alpha; the release is not a Zed extension-store approval.

Upstream installation behavior is defined by Zed's
[extension host](https://github.com/zed-industries/zed/blob/main/crates/extension_host/src/extension_host.rs)
and [platform paths](https://github.com/zed-industries/zed/blob/main/crates/paths/src/paths.rs).
