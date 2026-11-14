# Development

## Native language server

Use the Go version in `go.mod` and a C compiler for the vendored parser. Dependencies
are vendored; no Rust toolchain or editor checkout is needed.

```sh
go build -mod=vendor -o build/argo-workflows-ls ./cmd/argo-workflows-ls
go vet -mod=vendor ./...
./build/argo-workflows-ls version --json
./build/argo-workflows-ls check --root /path/to/yaml --format json
```

Root `VERSION` controls release-mode builds. From a clean commit, run:

```sh
python scripts/release.py version --source-commit "$(git rev-parse HEAD)"
```

The native workflow runs the existing CLI, stdio LSP, performance, resource,
mutation and archive consumers on their designated hosts. Release staging and
collection require exact clean source identity, matching native metadata, complete
licenses and all five platforms. See `.github/workflows/native.yml` for executable
commands and retained evidence. Merging a changed `VERSION` into `main` starts
`release.yml`; after all gates pass it creates `v<VERSION>` at the verified commit
and publishes the native assets. An exact version-tag push or a manual run on
`main` or that tag can also start the same workflow.

## Shared toolkit source

The toolkit dependency is pinned to the source commit in `contracts/toolkit.lock.json`,
represented by the Go pseudo-version in `go.mod`. It does not require a toolkit release tag.
The vendored `source`, `yamlview`, `yamlvalue`, `schemaquery`, `edit`, `index`, and `workspace` packages match
that commit; builds need no sibling checkout or local `replace` directive.
`contracts/toolkit-design-5.md` is the byte-identical API contract, with its
implementation and content identities in `contracts/toolkit.lock.json`.
The vendor script also restores the existing Expr formatter and JSON Schema
compiler patches, verifying their input and output hashes against
`contracts/expr-patch.json` and `contracts/jsonschema-patch.json`.

When advancing the source pin, update the Go dependency, contract snapshot and
lock identities together. Run `python3 scripts/vendor-native.py` to regenerate
vendor, restore the native parser source trees and reapply the recorded Expr
formatter and JSON Schema compiler patches. Keep `go.sum`, vendored sources and provenance records in the
same change. The executable's `version --json` reports the linked toolkit
dependency from Go build metadata.

`yamlvalue` supplies strict YAML values, bounded alias/merge expansion and source
sharing; `schemaquery` reads the same compiled schema graph as the validator.
The plugin owns resource identity, release selection, explicit-tag policy,
logical-result budgets, reference semantics and edit safety. Do not add a second
complete YAML decoder or schema walker as a fallback after a shared query fails.
Budget, cancellation and unsupported-query errors remain explicit.

## Zed adapter

All adapter code, Rust configuration and assets are under `editors/zed`.

```sh
cd editors/zed
cargo test --locked
cargo build --locked --release --target wasm32-wasip2
```

Select this directory when using Zed's Install Dev Extension. The independent
`native-server-version` file chooses a released LSP; the adapter's own version can
change independently. Building the adapter does not build or select the current
Go source. To test local native changes in Zed, build the native executable and
point `settings.serverPath` to that absolute path. The adapter package command is also in this directory.
`zed.yml` owns adapter CI. Bump the matching Cargo and extension versions and merge
the changed `editors/zed/extension.toml` into `main` to start `zed-release.yml`.
It also accepts matching `zed-v*` tags and manual runs. After its own gates pass,
it waits up to 45 minutes for the exact native pin to be published with all five
platform executables and `SHA256SUMS`, then creates its tag and publishes the ZIP.
The native release can precede the adapter release and use a different commit;
no Go build is part of the adapter workflow. Existing releases and drafts are
never replaced. See [release automation](docs/distribution.md)
for waiting and failure behavior.

The precompiled ZIP is the ordinary alpha installation path and does not need a
Rust toolchain on the user's machine. See [distribution](docs/distribution.md).

Installer changes also require the WASI cache runtime acceptance in `zed.yml`:
the production installer runs as `wasm32-wasip2`, probes the pinned released
native executable through a host bridge, and exercises retained Windows handles
that forbid moving/deleting the executable. Native Rust tests alone do not cover
that runtime boundary. This is not a Zed GUI test. The
[Windows activation lessons](docs/lessons-learned.md) record the incident,
publication invariants, acceptance layers and prevention checklist.

From `editors/zed`, on Linux amd64 or Windows amd64 with the pinned Rust
toolchain/target and Python installed, use a fresh evidence directory:

```sh
python tests/cache_runtime_e2e.py --output ../../artifacts/zed-cache-runtime
python tests/ablate_distribution.py --output ../../artifacts/zed-distribution-ablation
```

The runtime consumer verifies the pinned Wasmtime download and the native
release asset before use. It needs network access for those downloads; a
download or runtime setup failure does not count as a passing cache test.

## Changes and tests

Use the existing real-process E2E consumers for changed behavior. Keep raw failure
output and reject timeouts, missing reports and unexpected successful mutants.
Foreign-platform synthetic archives test aggregation rules only; they do not
replace native execution. Keep actual upstream CRD tests separate from synthetic
schema query fixtures.

For cache protocol changes, run the installer tests, the WASI runtime consumer
and `editors/zed/tests/ablate_distribution.py`. Require a passing baseline and
the intended assertion failure after each isolated mutation; compilation errors,
unrelated failures and zero matching tests must not count as regression detection.
Record exact-commit evidence separately from any manual Zed acceptance.

Shared YAML/query changes use `wf-shared-yaml-projection.py`,
`wf-projected-query-context.py`, `wf-semantic-origins.py`, and the separate
`tests/ci/run.py schema` gate. `ablate-shared-yaml-projection.py` must compile each
variant and trigger its named source-validation or tag-policy failure.
When moving code into a dependency, migrate mutation selectors to the executing
code path; a selector that no longer matches is a test failure.

Version collection and normalization preserve upstream commit IDs and SHA-256
source identities. The default auto window is derived from the latest two stable
majors. A new catalog must pass version compatibility consumers before release.
See [configuration](docs/configuration.md), [architecture](docs/architecture.md),
[CI](docs/CI.md), and [lessons learned](docs/lessons-learned.md) for current boundaries and regression requirements.

Helm source navigation and identity controls run in the portable native suite via
`wf-helm-navigation.py` and `wf-helm-identity.py`. The Linux extended suite runs
`wf-render-discovery.py` against checksum-pinned Helm 3 and Helm 4 binaries and
`wf-render-cancel.py` against a real LSP process and its descendants. Install the
tools with `python3 tests/ci/install-tools.py --output artifacts/tools`, then run
`python3 tests/ci/run.py suite --suite extended --binary build/argo-workflows-ls
--tools artifacts/tools --output artifacts/extended` (join the command lines).
The adapter WASI acceptance downloads its pinned native release; publish and
verify that native release before validating a new adapter pin.

### CI identity, reuse and evidence

The native workflow builds the ordinary Linux release once and imports its exact
bytes into the Linux native, three semantic and four resource jobs. The producer
manifest includes the source tree, Go/C environment, native headers, dependencies,
linker flags, product version, runner image, run ID and attempt. Consumers verify
the manifest digest supplied by the producer job before executing the absolute
binary path. Partial reruns that inherit a producer from an earlier attempt are
rejected; rerun the complete producer and consumer group. Synthetic schema, slow
I/O, mutation, race, offline distribution and other platform builds remain
independent. Only the ordinary producer uses a compiler/input-scoped build cache.

`Acceptance gate`, `Zed acceptance gate`, and `Installer acceptance gate` are
independent required-check candidates. Each compares its complete plan to the
same workflow's job results and retained report bytes. A missing, cancelled,
skipped or duplicate selected lane fails. Branch rules must require the exact
hosted check names and GitHub Actions source after their PR events are verified;
local control results do not establish branch protection.

Useful local commands (use a new output directory for each execution):

```sh
python tests/ci/control.py baseline --output artifacts/baseline.json
python tests/ci/control_e2e.py --output artifacts/control-e2e
python tests/e2e/session_e2e.py --output artifacts/session-e2e
python tests/e2e/wf-equivalence.py /absolute/server artifacts/equivalence
python tests/ci/trends.py --inputs artifacts/ci --output artifacts/timing.json
```

The baseline exporter reads frozen Git objects, including the original resource
consumer source and exact assertion inventory. Timing summaries keep first
failures and original command hashes; they do not replace product thresholds.
A performance comparison needs five warm and three cold complete runs. An
insufficient sample set is reported explicitly. The change classifier is staged
in observation mode: every active plan remains full until hosted gate and reuse
acceptance is established. Unknown paths, snippets, tests, dependencies, rename,
delete and unavailable diffs always select full.

The `stdio-buffer-lifecycle` fixture owns the migrated schema/navigation case.
The session helper only handles framing, deadlines, bounded transcripts and
process cleanup. Equivalence consumers retain final overlays, targets, versions,
ranges, diagnostic severity and root identity. Repeated-query agreement does not
claim a cache hit. The existing saturation consumer also compares the state after
an observed cancellation with a fresh server.

The historical alpha.4 file installer remains a separate regression fixture.
`Published Zed adapter installation` verifies a successful same-repository adapter
release, exact source tag/version, asset inventory and installed source/native pin.
It uses read-only credentials and cannot publish. Neither file installation nor
stdio acceptance proves activation in Zed.
