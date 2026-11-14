# Development

## Native language server

Use the Go version in [go.mod](go.mod), Python 3 and a C compiler for the vendored
Tree-sitter parser. CI pins Python and the compiler in
[the native workflow](.github/workflows/native.yml). Native dependencies are
vendored; the Go service needs neither Rust nor an editor checkout.

From the repository root:

```sh
go build -mod=vendor -o build/argo-workflows-ls ./cmd/argo-workflows-ls
go vet -mod=vendor ./...
./build/argo-workflows-ls version --json
./build/argo-workflows-ls check --root tests/fixtures/schema --format json
```

The schema fixture directory deliberately includes invalid inputs, so the last
command can return diagnostics and a nonzero exit code. For a clean smoke check,
use the minimal Workflow from the [README](README.md) in its own directory.
A direct `go build` is a development build. Release builds additionally require a
clean exact source commit and the root [VERSION](VERSION):

```sh
python3 scripts/release.py version --source-commit "$(git rev-parse HEAD)"
```

The source version, adapter version and adapter native pin are independent.
Current values, installation, release triggers and publication requirements live
in [distribution.md](docs/distribution.md). Building current source does not
replace an already published binary with the same version.

## Shared toolkit and vendored patches

[contracts/toolkit.lock.json](contracts/toolkit.lock.json) pins the toolkit source
commit; [go.mod](go.mod) contains its Go pseudo-version. The vendored `source`,
`yamlview`, `yamlvalue`, `schemaquery`, `edit`, `index` and `workspace` packages
match that pin. No sibling checkout, local `replace` or toolkit release tag is
required. The hash-locked [API contract](contracts/toolkit-design-5.md) is a build
and identity input; its filename does not make it an obsolete design document.

When advancing the pin, update the module dependency, contract snapshot and lock
identities together, then regenerate vendor:

```sh
python3 scripts/vendor-native.py
```

The script restores native parser sources and verifies and reapplies the Expr
formatter and JSON Schema compiler patches. Commit `go.mod`, `go.sum`, vendor,
contract and provenance changes together. The exact patch records and verification
requirements are in [VENDORED_PATCHES.md](docs/VENDORED_PATCHES.md). Plain
`go mod vendor` is insufficient for this repository's native inputs.

The toolkit provides strict YAML values, bounded alias/merge expansion, source
coordinates, schema graph queries and indexing primitives. Workflows owns GVKs,
versions, namespaces, references, rule messages, logical-result budgets and edit
safety. A failed shared query must not fall back to another complete decoder or
schema walker. See [module and dependency boundaries](docs/architecture.md).

## Zed adapter

The independent Rust crate, extension manifest, installers, snippets and package
scripts live in [editors/zed](editors/zed). With Rust installed through rustup,
run from that directory:

```sh
cargo test --locked
cargo build --locked --release --target wasm32-wasip2
```

The directory's `rust-toolchain.toml` pins the toolchain and target. Select
`editors/zed` in Zed's **Install Dev Extension** action. That builds the adapter;
its `native-server-version` still selects a released Go server. To exercise local
Go changes, build the native executable and configure an absolute
`lsp.argo-workflows-ls.settings.serverPath` as shown in the
[configuration reference](docs/configuration.md).

Installer changes require the production WASI runtime boundary as well as Rust
Host tests. From `editors/zed`, on Linux amd64 or Windows amd64, use new output
directories:

```sh
python3 tests/cache_runtime_e2e.py --output ../../artifacts/zed-cache-runtime
python3 tests/ablate_distribution.py --output ../../artifacts/zed-distribution-ablation
```

These consumers verify downloaded Wasmtime and native release assets, execute
real native identity probes, and exercise controlled Windows file handles. They
need network access to their pinned assets and do not start a Zed GUI. Publish
and verify a native release before validating a new adapter pin against it.

## Choosing validation

Use the existing real-process consumers for the behavior being changed. The
[CI reference](docs/CI.md) is the single inventory of workflows, suite commands,
resource constraints, source-bound receipts, mutation rules and evidence limits.
The machine-readable routing inventory is
[tests/ci/inventory.json](tests/ci/inventory.json); update it when adding, deleting
or renaming a test entry point.

- Domain or editing changes: run the matching CLI/stdio consumer and verify exact
  diagnostics, ranges and final edited bytes, including refusal cases.
- YAML/schema changes: keep real upstream CRD checks separate from synthetic
  schema queries; run the applicable shared-projection and schema consumers.
- Index/lifecycle changes: check stale input rejection, open-buffer authority,
  cancellation, complete scope recovery and the relevant resource suite.
- Installer/package changes: run Rust tests, WASI/cache controls and package or
  filesystem installation acceptance at their respective boundaries.
- Catalog updates: follow [the catalog update procedure](docs/multi-version-support.md)
  and retain upstream commit IDs, content hashes and the complete version matrix.

Every consumer uses a fresh output directory. Mutations need a passing baseline,
a successful mutant build, the full expected case/assertion set and the named
behavioral failure; compilation errors, crashes, missing reports and timeouts do
not prove detection. Preserve source and binary identities with failure output.

## Maintaining documentation

Keep [docs/README.md](docs/README.md) as the navigation entry. Document implemented
behavior in usage, architecture, CI or distribution pages; record durable
problem–cause–fix–regression lessons in
[lessons-learned.md](docs/lessons-learned.md). Remove superseded plans, audit
snapshots and progress reports after their useful facts enter those pages.
Do not duplicate current versions or full test inventories in several documents.

The toolkit contract and [native release body](docs/native-release-notes.md) are
machine-consumed inputs and retain their paths. A contract change requires its
identity update; a release body belongs to the corresponding release version.
Documentation-only CI selection is deliberately narrow: only modifications to
`README.md` and/or `DEVELOPMENT.md` qualify. Changes under `docs/`, deletions,
renames or mixed changes run the full configured plan. See [CI](docs/CI.md) for
selection and required-gate semantics.
