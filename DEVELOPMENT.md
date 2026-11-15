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

Treat an upgrade as an explicit consumer decision. Keep the existing pin until a
contract diff and a fixed candidate commit demonstrate a benefit in Workflows
workloads. In particular, adopting design-6 requires parser scheduling measurements
with representative interactive workloads; matching another product's pin is not
an upgrade reason.

1. Record the old consumer commit, toolkit lock, contract digest and toolchain.
   Rebuild that combination independently before replacing it, using the check
   below. Keep the old checkout and evidence available for comparison.
2. Review the candidate contract against the locked snapshot, then fix the full
   provider SHA. Run the provider's compatibility checks against fixed consumer
   commits. Record both revisions and any candidate vendor substitution; this
   proves compatibility with those consumers, not that they adopted the candidate.
3. In an isolated consumer candidate, update `go.mod`, `go.sum`, the contract
   snapshot and all lock identities together. Regenerate vendor with
   `GOWORK=off python3 scripts/vendor-native.py`. The script runs `go mod vendor`,
   restores native parser sources, and verifies and reapplies the Expr formatter
   and JSON Schema compiler patches. Plain `go mod vendor` is insufficient.
4. Compare every vendored toolkit file with the pinned provider's corresponding
   runtime file, including C headers, and check that the expected vendor inventory
   was not truncated. Verify the contract SHA-256 against `contractFile` in the
   lock and the provider contract. Reproduce `sourceSHA256` using the runtime
   snapshot procedure at the pinned provider commit. For the current
   `go-module-commit` adoption, `zipSHA256` is the SHA-256 of the Go module archive
   returned as `Zip` by `go mod download -json`, not the local snapshot ZIP. Check
   `Origin.Hash` against `implementationCommit`; separately compare `Sum` and
   `GoModSum` with the module and `/go.mod` entries in `go.sum`. These Go `h1:`
   checksums are not raw archive SHA-256 values.
5. Build the actual consumer with `GOWORK=off` and no `replace`, check the linked
   dependency identity, then run the applicable real CLI/LSP consumers from
   [CI](docs/CI.md): diagnostics, strict projection, cancellation/lifecycle,
   editing refusal and final bytes, and memory/interactive workloads. Retain
   source and binary hashes, commands, tool versions, raw output and assertions.
   Commit module, vendor, contract and provenance changes together only when
   authorized. Consumer acceptance, adoption, native publication and real Zed
   acceptance remain separate evidence stages.

To retrieve the exact module archive into a fresh cache for the identity check,
run from the consumer root with the pinned Go toolchain installed:

```sh
toolkit_module=$(python3 -c 'import json; print(json.load(open("contracts/toolkit.lock.json"))["module"])')
toolkit_version=$(python3 -c 'import json; print(json.load(open("contracts/toolkit.lock.json"))["implementationVersion"])')
toolkit_cache=$(mktemp -d)
GOWORK=off GOMODCACHE="$toolkit_cache" go mod download -json "$toolkit_module@$toolkit_version"
```

Retain the returned JSON and hash the exact `Zip` path it reports. This fetch
requires network access and verifies archive provenance; it is separate from the
vendored offline build below. Preserve the archive or its verification evidence
before removing the temporary cache.

For an independent rebuild, extract a fixed consumer commit into an otherwise
empty directory outside a parent workspace (or make a clean standalone checkout).
From that extracted root, with the Go/C toolchain available:

```sh
GOWORK=off go mod edit -json
CGO_ENABLED=1 GOWORK=off GOPROXY=off GOSUMDB=off go build -mod=vendor -buildvcs=false -trimpath -o build/argo-workflows-ls ./cmd/argo-workflows-ls
go version -m build/argo-workflows-ls
./build/argo-workflows-ls version --json
```

Require an empty `Replace` list, the module version in `go.mod` and
`vendor/modules.txt` equal to the lock's `implementationVersion`, and the same
version in both binary metadata and `toolkitVersion`. Also compare the contract
file's SHA-256 with the lock. `GOPROXY=off` checks that the build uses committed
vendor; install the pinned Go toolchain beforehand because toolchain acquisition
is separate. This development build deliberately makes no release source-commit
claim. Build success and an identity probe do not establish behavioral acceptance.

The existing [build identity helper](tests/ci/build_identity.py) records input and
vendor digests in CI producer receipts; [CI](docs/CI.md) describes
its stricter release import checks. It does not reconstruct the provider runtime
snapshot or download and hash the pinned Go module ZIP. Perform those checks at
upgrade time rather than treating matching receipt inputs as dependency adoption
proof. Patch provenance and verification requirements remain in
[VENDORED_PATCHES.md](docs/VENDORED_PATCHES.md).

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
