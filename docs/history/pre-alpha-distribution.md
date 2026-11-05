# Historical delivery design

This describes the pre-alpha implementation. Current instructions are in ../distribution.md.

# Native distribution and Zed store readiness

The extension ID is `argo-workflows-lsp`; the language server ID is `argo-workflows-ls`.
The extension has no `bundled-server` feature, `include_bytes!` payload or native executable
in its WASM. This follows [Zed's publishing prerequisites](https://github.com/zed-industries/zed/blob/main/docs/src/extensions/publishing/prerequisites.md):
fetch a language server with the Rust Extension API or find it in the user's environment.

## Release contract

The extension's Cargo version pins the native version. Version `0.1.0` requests the exact
GitHub Release tag `v0.1.0` in `lazysnail1024/zed-argo-workflows`. It does not query the
latest release at startup, follow prereleases, or fall back to an older server.

| Host | Raw executable asset |
| --- | --- |
| Linux x86_64 | `argo-workflows-ls-0.1.0-linux-amd64` |
| Linux aarch64 | `argo-workflows-ls-0.1.0-linux-arm64` |
| macOS x86_64 | `argo-workflows-ls-0.1.0-darwin-amd64` |
| macOS aarch64 | `argo-workflows-ls-0.1.0-darwin-arm64` |
| Windows x86_64 | `argo-workflows-ls-0.1.0-windows-amd64.exe` |

The same release must contain a UTF-8 `SHA256SUMS` file with a lowercase 64-digit digest,
two spaces and the exact basename per entry. An empty file, malformed entry, missing
selected asset or duplicate selected entry is rejected. The selected executable must
be at most 128 MiB; the checksum file must be at most 64 KiB. Zed's `download_file` API
has no streaming-size-limit parameter, so these bounds are checked after the download
and before reading/hashing/executing it. Both files go through `download_file` with
`DownloadedFileType::Uncompressed`, including when a user restricts download capabilities.
The extension does not bypass a denied download with a direct HTTP client.

Each native build must report the Workflows product, adapter contract 1, analysis
readiness, config/report schema 1, an exact server version, `buildMode: release`,
`dirty: false` and a 40-digit source commit from `version --json`. Build metadata,
source archive, catalog hash, legal notices and the release package must refer to
one tested commit. Release version, `Cargo.toml` and `extension.toml` must agree.
Use the existing native build/package machinery to produce the five binaries;
do not rebuild an already tested binary during release aggregation. Publish legal
archives and provenance with the executable assets. Never replace assets in a
published tag: publish a new version and update the extension pin.

SHA-256 here detects changed, truncated or corrupted bytes. The checksum and binary
come from the same GitHub release over HTTPS. This is not an independent signature
or proof that a compromised publisher account supplied trustworthy code. The normal
trust boundary remains the extension source and its release repository.

## Resolution, cache and failure recovery

1. An explicitly configured absolute `lsp.argo-workflows-ls.settings.serverPath` wins.
   The adapter probes it through Zed's process API without reading outside its WASI
   filesystem. Development identities are accepted if product, contract, schema and
   readiness match. An invalid override is reported and stops resolution.
2. Otherwise the adapter asks the worktree environment for `argo-workflows-ls` in PATH
   and validates it in the same way. An incompatible PATH executable is reported;
   correct or remove it before managed installation can be used.
3. Otherwise it selects the published platform asset for the pinned version.

New managed installations live under the extension work directory in unique
`servers/v<version>-<platform>.gen-<attempt>/` directories. A valid legacy
`servers/v<version>-<platform>/` directory is still preferred and retained in place.
Before every execution, the cache must contain regular files with an acceptable
checksum manifest, a matching binary SHA-256 and a matching release identity. Valid
cache use performs no download. If the legacy slot is missing or byte-corrupt, the
installer checks generation directories for that exact version and platform; incomplete
or byte-corrupt generations are skipped. It never searches another release as a fallback.
The identity probe and final server command use
absolute native paths because Zed's host process does not inherit the WASI work directory.

On a cache miss or byte corruption, the installer uses a unique `.download-*` directory.
It downloads and validates the manifest, downloads and hashes the binary, asks Zed to
set executable permissions, and probes `version --json`. Only then does it rename the
complete directory into a new generation. Hash failures never reach chmod or process execution.
A failed attempt deletes its own staging files; a retry creates a fresh staging directory.
A process killed between operations may leave an old staging directory, but it is never
selected or executed and cannot block a new attempt.

Before activation, the installer checks whether another Zed process completed the same
version/platform installation during its downloads. It verifies that cache's checksum
and release identity again and reuses a valid winner in place. This last check is only
an optimization: a second process can still publish after the check. Every published
directory keeps its pathname and file bytes; the installer never moves or deletes it,
even when a later checksum detects corruption. Executable permissions may still be
repaired before a verified cache is used.

Concurrent processes can safely retain two verified generations for the same pin. If
their generation names collide, the directory rename cannot overwrite a nonempty
published destination on Unix or Windows. The installer revalidates and reuses a valid
collision winner, or chooses a fresh generation when the occupied slot is incomplete or
byte-corrupt. It never renames the winner aside. A corrupt-cache repair likewise installs
into a new generation while preserving every previous path, including files held open
by a Windows server. There is no lock directory that a crashed process can leave behind.

An unused staging directory is removed only while the attempt still owns it. Successful
publication relinquishes that pathname, so cleanup cannot remove a different process's
later reuse of the old staging name. If cleanup is blocked after a verified server has
been selected, the leftover is reported and cleanup is deferred instead of preventing
startup. A failed rename with no destination remains a visible installation failure.
Unexpected symlinks and nonregular cache entries fail safely. Checksum-correct binaries
that cannot execute or report an incompatible identity fail visibly; no older version
is selected to conceal the error.

To recover from filesystem permissions, a locked executable or an unexpected cache
entry, stop the language server, inspect the reported path and remove only that affected
version or generation directory. Retry with network access, or point `serverPath` to an independently
verified local executable. Published generations are not automatically pruned: another
Zed process may still be using any one of them. After stopping all affected Zed/server
processes, obsolete generations and abandoned `.download-*` directories may be removed
to reclaim disk space, along with any `.previous-*` directories from older adapters.
Do not delete another running installation's files. For offline deployment before any cache exists, use
`serverPath` or PATH with a validated local server; do not fabricate a cache manifest.

Upgrades install a new version/platform directory. Existing versions remain intact if
the new download or handshake fails. An explicit rollback uses the prior extension
version with its corresponding cached release, or a compatible `serverPath` selection.
The installer never silently launches a different release. Cached execution still needs
normal native execution permissions; offline support does not bypass OS restrictions.

## CI distribution candidates without publication

Pull requests, pushes to `main`, and manual runs of `native.yml` resolve the stable
version from both `Cargo.toml` and `extension.toml` and require the exact clean
`github.sha`. For a pull request this is GitHub's tested merge commit. The existing
five native runners now build that version in release mode and execute the same
required consumers. There is no second candidate native matrix. A reusable formal
release caller can supply `release_version`; a mismatch is rejected.

After each native host passes, the workflow reuses `scripts/release.py stage` to
retain its raw executable, legal archive, and source/identity record. It also uses
`scripts/candidate.py adapter` to retain the existing compiled `wasm32-wasip2`
component outputs with an `adapter.json` manifest: extension/version, exact commit,
build host, locked Rust toolchain, hashes of adapter sources and resource inputs,
and each component's size/SHA-256. The Argo CD job retains its developer-package
compile; the Workflows job retains both its ordinary-source and developer-package
compiles. These WASM outputs are portable components; the recorded host identifies
where the component was built, not a different WASM runtime target.

The final candidate job runs only after all required native and acceptance gates
succeed. It downloads assets from that same workflow run and calls the existing
strict five-platform release collector. That collector rechecks native SHA-256,
identity, provenance, legal archives and platform completeness. The candidate
collector also requires adapter records from all five matching hosts, exact source
and native-build identities, and a strict adapter-only file inventory. It verifies
component headers/section boundaries, hashes and sizes and rejects embedded byte
signatures of any of the five verified native executables. It does not introduce a
second native or ZIP packager. Corruption, missing/duplicate hosts, mixed source,
extra native files, and native bytes embedded in an otherwise correctly hashed
adapter refuse the complete candidate output.

The retained Actions artifact is named
`workflows-distribution-candidate-<version>-<commit>`. It contains:

- `native/`: five existing verified raw assets, their legal archives and platform
  records, plus the native `SHA256SUMS` required by the managed downloader.
- `adapters/<build-host>/`: compiled WASM evidence and its identity/hash manifest.
- `candidate.json`: exact candidate identity, complete file hashes and explicit
  `not-published`, `installationChannel: none`, and editor `not-executed` status.
- Root `SHA256SUMS`: every delivered file, including both manifests and the native
  checksum inventory, excluding only its own checksum.

Artifacts expire after 30 days. This is a review/acceptance channel, not a formal
precompiled extension installation channel or a public managed-server endpoint.
The workflow has only `contents: read`, creates no tag or GitHub Release, and never
calls the publishing job. Passing candidate checks does not make the pinned
`releases/download/v<version>/...` URLs available. A maintainer must still publish
the separately gated complete native Release, record real Zed acceptance on all
five platforms, and obtain official registry review. The existing formal tag,
version, clean-source and immutable-Release gates are unchanged.

The existing release E2E now receives the actual compiled WASM from the native job
and exercises candidate assembly and rejection using its existing native artifacts.
Its foreign-platform fixtures remain explicitly synthetic controls. Only a fully
successful remote five-host candidate job establishes an actual five-platform
candidate set for that workflow's exact commit; neither local fixtures nor an
older successful CI run establish that result.

## Building and publishing a native release

`.github/workflows/release.yml` runs on a `v*` tag, or when manually dispatched on that
tag. It validates the stable tag against both extension manifests, then calls the
existing full acceptance workflow with `release_version`. Each native runner builds
the actual release in a disposable worktree of the exact clean commit. The version,
source commit and release mode are stamped into CLI identity, JSON reports and LSP
`serverInfo`; ordinary unstamped contributor builds remain development builds.

The release workflow preserves all five native runners and the extended semantic,
resource and mutation gates. Every native runner also executes
`tests/e2e/release-distribution.py`. The test builds and runs its current-host release;
the other-architecture aggregation fixtures are explicitly synthetic parser controls.

The following commands exercise the local Linux amd64 release path from a clean
committed checkout. Set `RELEASE_COMMIT` to that checkout's full commit SHA:

```sh
python tests/ci/run.py build --platform linux/amd64 --release-version 0.1.0 --output artifacts/release-build
python scripts/release.py stage --build artifacts/release-build/build.json --version 0.1.0 --source-commit "$RELEASE_COMMIT" --output artifacts/release-linux-amd64
```

`stage` reuses `scripts/package-dev.py` and the exact Git source archive. It checks
the actual native executable, then emits the raw asset, a ZIP preserving all original
notices/provenance, and a platform release record. The ZIP retains the developer kit's
internal directory layout; its release filename is
`argo-workflows-ls-0.1.0-<os>-<arch>.zip`. The adapter downloads only the raw executable
and `SHA256SUMS`; the complete ZIP and provenance remain available alongside them.

Windows builds reuse the Argo CD builder's static external-link configuration and
the same MinGW-Builds `15.2.0-rt_v13-rev1` x86_64 POSIX/SEH/UCRT distribution.
[`scripts/windows-runtime.json`](../scripts/windows-runtime.json) records its original
archive URL/SHA-256 and the exact runtime/notice file hashes. The checked-in notice
copies are unchanged bytes from that distribution. The build verifies the compiler runtime
inputs and original legal notices before and after linking, and retains the linker
map with its SHA-256. The legal ZIP carries the complete GCC runtime exception,
MinGW and winpthreads notices; packaging and collection verify their committed hashes.
These notices are also retained in the other platform ZIPs as part of the shared
source distribution; their presence does not mean those platforms use MinGW.

Windows staging runs `objdump -p` and rejects unreviewed non-system DLL dependencies,
including `libwinpthread-1.dll`, so a compiler installed on the CI host cannot hide
an extra runtime requirement. The Windows release test also launches the raw asset
from an otherwise empty directory with only Windows System32 in PATH. Both the
import check and this actual execution must pass; a successful build alone is
insufficient evidence of a standalone download.
Linux `ldd` and macOS `otool -L` output are recorded as native dependency evidence.
The advertised native baselines are the CI hosts in the matrix below; those results
do not establish compatibility with musl Linux or older system C libraries.

After downloading the five platform artifacts into separate directories, CI runs:

```sh
python scripts/release.py collect --inputs artifacts/release-inputs --version 0.1.0 --source-commit "$RELEASE_COMMIT" --output artifacts/release
```

Collection requires all five platforms from that exact commit. It rechecks raw/ZIP
agreement, original source and notice bytes, metadata, runtime dependency records and
all checksums. The final directory has five raw executables, five legal ZIPs, five
release records and `SHA256SUMS`. Collection does not rebuild any executable.

Once the full reusable acceptance workflow succeeds, CI creates a draft native Release
with all assets, then makes it public. There is no separate approval step in this
workflow. Existing Releases are not overwritten. A failed upload may leave a draft;
inspect and remove an incomplete draft before retrying. After a version has been
public, publish a new version instead of replacing its assets. Bump both manifests
and the Rust lockfile package version when preparing that new pin.

The first native Release is needed to test the actual managed-download path in Zed.
Perform the real-editor checks below against those public assets and the final
extension commit before submitting the separate extension-registry PR.

## Developer packages

`scripts/package-dev.py` still accepts a native executable and its exact source archive;
it does not compile another server. It verifies the supplied digests, native identity,
Go build metadata, source commit and catalog bytes, then produces a deterministic ZIP.
The `extension/` subtree contains the unmodified Rust adapter inputs and license. The
`server/` subtree contains the native executable; `settings.example.json` shows the
absolute `serverPath` to configure. Legal notices and `native/manifest.json` remain
outside the extension subtree. The JSON result reports `folder`, `extension`, `binary`,
`zip`, `zipSha256` and `binarySha256` for CI and release aggregation.

Installing this package in Zed requires selecting **only `extension/`**. The native server
is not automatically embedded or copied into that extension. A package handshake or
WASM build does not exercise Zed's actual host download permissions or process environment.

## Automated acceptance

`cargo test --locked` executes the production installer/resolver with a fake network and
process boundary and real temporary directories. It verifies manifest rejection, hash
mismatch refusal, repair after corrupted binary/manifest, interrupted download recovery,
chmod/probe failure recovery, warm offline reuse, independent version/platform caches,
failed offline upgrade without fallback, an installer that publishes after another's
final cache lookup, simultaneous corrupt-cache repairs, verified and corrupt destination
collisions, offline generation reuse, staging ownership after rename, concurrent cache-root
creation, deferred cleanup failures, symlink refusal, absolute Windows paths, and
explicit override/PATH precedence. These are installer behaviors, not source-text checks.

`tests/e2e/dev-bundle.py` keeps the existing process-level checks: deterministic ZIP bytes,
exact manifest/file inventory, source/provenance identity, legal notices, native execute
permissions, extracted server identity, and adversarial input refusal. It now checks the
separate server path and adapter subtree. Native CI runs these consumers on all five
platforms, runs the Rust installer tests, and compiles both the repository adapter and
the actual packaged adapter to `wasm32-wasip2`.

All existing semantic, mutation and resource acceptance gates remain in force for
release publication. A platform compile or installer unit test cannot waive a failed
gate. The pre-change main run reported a resource/adversarial process kill and a cold
index result above its 5-second threshold; those require a successful exact-commit
acceptance run before a release can be considered eligible. The thresholds have not
been relaxed by the distribution change.

## Required real Zed acceptance

No real editor run for this new managed installer is claimed. Record results for the
exact submitted submodule commit and release assets, not an earlier developer bundle.

| Platform | Native CI runner | New managed installer in real Zed |
| --- | --- | --- |
| Linux amd64 | `ubuntu-24.04` | Pending |
| Linux arm64 | `ubuntu-24.04-arm` | Pending |
| macOS amd64 | `macos-15-intel` | Pending |
| macOS arm64 | `macos-15` | Pending |
| Windows amd64 | `windows-2025` | Pending |

For each platform, keep Zed version, OS/architecture, extension commit, release tag,
asset/manifest digests, relevant settings and redacted Zed logs with observed results:

- Install the exact adapter commit in a fresh Zed profile with no override/PATH server.
  Verify first download, correct native asset, schema diagnostic, hover, completion,
  definition/navigation, valid/refused rename, Quick Fix/undo and YAML/Helm coexistence.
- Restart with network disabled and prove warm-cache execution and normal editing.
  Delete the managed cache while offline and verify a visible, useful installation error.
- Truncate the cached binary or checksum file, then reconnect and prove repair before
  normal analysis. Interrupt a first download and retry. A failed check must never start
  an unverified executable. Exercise denied download and native execution permissions.
- Open two Zed processes against an initially empty cache, then repeat after corrupting
  a cached binary. Verify that both selected server paths remain usable and that a
  subsequent offline restart finds a verified generation. On Windows, retain the first
  server process during the second installation to exercise native file sharing behavior.
- Upgrade to a new compatible release; fail its download and verify that the previous
  cache remains intact without silent fallback. Test an explicit rollback. On Windows,
  include restart/upgrade while the old native process may still hold its file open.
- Set a valid development `serverPath`, then an invalid absolute path, a wrong-product
  binary and a nonready binary. Verify visible failures and no managed fallback until
  the override is corrected/removed. Test PATH discovery and settings forwarding.

## Store submission status

This change prepares compliant distribution; it does not publish a Release or install
anything in the Zed store. The first public `v0.1.0` assets, all exact-commit automated
gates, recorded manual Zed acceptance and maintainer review are still prerequisites.
Also explain the extension's offline Workflows semantic analysis and constrained edits
relative to existing YAML/Kubernetes CRD extensions, as the store reviews overlap.

After those gates pass, follow the [publishing guide](https://github.com/zed-industries/zed/blob/main/docs/src/extensions/publishing/publishing-guide.md):
add the public HTTPS repository as a Git submodule of `zed-industries/extensions`, pin
the tested commit that exists on a branch, keep the registry version equal to
`extension.toml`, run `pnpm sort-extensions` and submit one extension per registry PR.
This repository PR is preparation for that separate review. Only the registry maintainers'
approval and publication establishes store availability. The extension license must satisfy
[Zed's license requirements](https://github.com/zed-industries/zed/blob/main/docs/src/extensions/publishing/license-requirements.md).
