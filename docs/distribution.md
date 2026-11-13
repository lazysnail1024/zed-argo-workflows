# Distribution and installation

## Independent versions

| Component | Source version | Distribution |
| --- | --- | --- |
| Native Go LSP/CLI | [VERSION](../VERSION): `0.0.1-alpha.4` | Platform binaries, archives, provenance and `SHA256SUMS` under `v<VERSION>` |
| Zed adapter | [extension.toml](../editors/zed/extension.toml) and [Cargo.toml](../editors/zed/Cargo.toml): `0.0.1-alpha.5` | Precompiled ZIP, identity record and `SHA256SUMS` under `zed-v<VERSION>` |
| Adapter native server | [native-server-version](../editors/zed/native-server-version): `0.0.1-alpha.4` | Pinned native release, versioned independently of the adapter |

These are source configuration values, not proof that a release has been published.
Check [release assets](https://github.com/lazysnail1024/zed-argo-workflows/releases)
before installation. Use `version --json` to verify `sourceCommit`,
`toolkitVersion` and `catalogHash`. Published artifacts do not acquire later
source changes. Downloads use explicit versions and direct URLs; corrections to
published artifacts require a new version.

## Release automation

Merging a root `VERSION` change into main triggers
[release.yml](../.github/workflows/release.yml). Changes to
`editors/zed/extension.toml` trigger the independent
[zed-release.yml](../.github/workflows/zed-release.yml); its Cargo version must
match. Matching tag pushes and manual runs on main or the matching tag are also
supported. Each workflow completes acceptance, tags the exact commit and
publishes verified assets. Existing tags never move; existing releases or drafts
stop publication. A native release workflow change on main can retry publication,
but cannot overwrite existing assets.

Before adapter publication, the native pin must resolve to `SHA256SUMS` and all
five raw binaries: Linux amd64/arm64, macOS amd64/arm64 and Windows amd64. A 404 or
draft is retried every 30 seconds for up to 45 minutes. Permission errors, other
HTTP errors, mismatched tags and published releases with missing assets fail
immediately. After a timeout, rerun the publishing job once native assets are
ready. The adapter does not rebuild Go; its native release can come from another
commit. Native release descriptions use [native-release-notes.md](native-release-notes.md).
The adapter runtime acceptance also downloads the pinned native release, so
publish native before validating an updated adapter pin.

## Install, upgrade and roll back

See the [README](../README.md) for installation. Close Zed, run the platform
installer and restart Zed. Running it again upgrades the installation. Precompiled
ZIP installation needs neither Go nor Rust.

The installer downloads the selected `zed-v<version>` ZIP and `SHA256SUMS`,
verifies the checksum and extension identity, stages beside the current user's
`extensions/installed` directory and replaces the installed extension. Identical
files are unchanged. One previous version is retained at
`extensions/.alpha-installer/argo-workflows-lsp.previous`. Failures restore the
previous state without changing the adapter's native server cache.

After closing Zed, roll back on Linux/macOS with:

```sh
curl -fsSL https://raw.githubusercontent.com/lazysnail1024/zed-argo-workflows/main/editors/zed/install.sh | sh -s -- --rollback
```

On Windows, download `editors/zed/install.ps1`, then run
`powershell -ExecutionPolicy Bypass -File ./install.ps1 -Rollback` from its directory.
Another rollback swaps the two retained versions. If an interruption leaves only
the backup, the next installation first restores it.

For offline installation, place a release ZIP and its `SHA256SUMS` together, then
run `sh install.sh --assets-dir /path/to/assets` or
`./install.ps1 -AssetsDir C:/path/to/assets`. Use `--version VERSION` or
`-Version VERSION` to select a published adapter; the script pins its default.
Alternatively, close Zed and extract the ZIP's `argo-workflows-lsp/` directory into
`extensions/installed`, preserving the previous directory for recovery. Delete
that installed directory while Zed is closed to uninstall.

Development installation uses Zed's **Install Dev Extension** with `editors/zed`
and requires Rust. To run current native source, build it separately and set
`serverPath`; rebuilding only the adapter still uses its native pin. Manual alpha
installation is separate from extension-store distribution.

## Verify downloads

Obtain `SHA256SUMS` from the same release. On Linux, place assets beside it and run
`sha256sum --check --ignore-missing SHA256SUMS`; require `OK` for the desired
archive. On macOS use `shasum -a 256 <archive>`; on Windows use
`Get-FileHash <archive> -Algorithm SHA256`. Compare the complete digest.
Checksums from the same HTTPS source detect corruption and version mismatches;
they are not independent publisher signatures. Retain the archive's dependency,
schema and runtime license texts and provenance.

## Server selection and caching

The adapter selects, in order: absolute
`lsp.argo-workflows-ls.settings.serverPath`, `argo-workflows-ls` on the worktree
shell PATH, then the pinned native release. Invalid explicit selections fail.
Managed downloads verify SHA-256 before an identity probe checks the product,
contract, readiness and release identity.

New cache generations use unique `servers/v<version>-<platform>.gen2-<nonce>/`
directories. After validating the executable at its permanent path, the installer
atomically creates an empty `.complete/` directory. Unmarked new generations are
unusable. Legacy fixed-version and `.gen-` caches are validated independently;
an unmarked new directory cannot masquerade as a legacy cache. Offline reuse
still checks file type, size, manifest, hash and identity. Published generations
are never moved or deleted; concurrent installers may each publish a valid
generation. Failure cleanup only attempts to remove the current incomplete
generation and preserves the original error if cleanup also fails.
See the [installer implementation](../editors/zed/src/distribution.rs).

For a first offline launch, extract a native archive onto PATH or configure:

```json
{
  "lsp": {
    "argo-workflows-ls": {
      "settings": { "serverPath": "/absolute/path/to/argo-workflows-ls" }
    }
  }
}
```

Windows requires an absolute `.exe` path. On POSIX, restore the executable bit
with `chmod +x argo-workflows-ls` if extraction removed it. Existing explicit and
PATH selections retain priority when the adapter is upgraded.

## Troubleshooting and editor acceptance

Open `zed: open log`, verify the selected binary with `version --json`, and check
the pinned release and platform assets. Fix the reported configuration, network
or permission problem, then restart the language server. Keep the generic YAML
server enabled. Helm templates render only through explicit `render-check` or
the saved-chart code action.

A previous Windows activation failure occurred when moving a directory after
executing its EXE. This was unrelated to YAML validation. A separately verified
EXE selected with `serverPath` provides a workaround. See
[lessons learned](lessons-learned.md) for the cause and runtime evidence limits.

See [CI](CI.md) for automated acceptance layers. The filesystem installer workflow
uses a published release fixture; it does not prove current-candidate GUI startup.
WASI installer tests exercise real native probes and controlled Windows handles,
but do not constitute a full Zed session.

For real-editor acceptance, record Zed, OS, architecture, adapter/native hashes,
source identity, settings and logs. Observe:

- First launch in a clean profile, pinned downloads and offline cached restart.
- Diagnostics, applied completion, cross-file navigation, allowed and refused
  Rename, Quick Fix and undo.
- Target-version settings, visible errors and recovery, and YAML/Helm coexistence.
- Interrupted or concurrent installation and corrupted-cache recovery while
  preserving existing valid paths.

Keep evidence separate for each version, platform and artifact. A local pass does
not validate other platforms or unpublished candidates.
