# Automated acceptance

`.github/workflows/native.yml` runs on every pull request, pushes to `main`, and
manual dispatch. Its stable **Acceptance gate** check requires every native,
extended semantic, resource and mutation job to succeed. Failure, cancellation
or a skipped mandatory job cannot produce a successful aggregate check.
Configure **Acceptance gate** as a required status check in the repository's
branch rules to make it a merge requirement; adding a workflow does not modify
GitHub branch protection.

The workflow has read-only repository permissions, pinned action commits,
disabled persisted checkout credentials, bounded jobs and commands, and
per-PR/ref concurrency. It does not publish a release or a marketplace extension.
The existing manually dispatched Linux development-bundle workflow remains a
smaller packaging convenience, not the complete acceptance check.

## What is executed

| Job | Hosts | Evidence and acceptance |
| --- | --- | --- |
| Native | Linux amd64/arm64, macOS amd64/arm64, Windows amd64 | Build the real CGO executable from vendor; `go vet`; CLI and stdio LSP consumers; test installer lifecycle; compile source and developer-package Rust adapters to `wasm32-wasip2`; generate and verify each native development ZIP |
| Extended semantic | Linux amd64, three independent shards | Existing workflow/reference/inheritance/DAG/query/edit/lifecycle consumers, source asset integrity, snippets, pinned offline Argo and Helm integration |
| Schema contracts | Linux amd64, semantic shard 0 | Actual CRD boundaries and separately built synthetic schema query/branch/projection fixtures |
| Resource adversarial | Linux amd64 | Actual cgroup memory limits, oversized aggregate input, expression depth/width, buffer/snapshot limits, alias/merge budgets, subprocess output floods, timeout/cancellation and descendant reclamation |
| Resource load | Linux amd64 | Existing 10,000-file/50 MiB workload, cold and active diagnostics, query semantics/latency, 600 edits over 60 seconds, 10,000 open/edit/close operations, memory retention, explicit CLI cancellation |
| Mutation core | Linux amd64 | Passing baseline plus detection of eight specific semantic mutations |
| Mutation audit | Linux amd64 | Passing baseline plus ten targeted mutations for metadata Rename, missed-watcher recovery, configuration diagnostics, isolated-scope actions, completion acquisition and cache freshness |
| Mutation resources | Linux amd64 | Passing baseline plus output-limit and descendant-reclamation mutations under a 512 MiB cgroup |

The native matrix uses the same runner families and pinned toolchain/action
versions already used by `zed-argocd`, but every build command and consumer is
selected for this repository. Go is read from `go.mod`; Rust is pinned in
`rust-toolchain.toml`; Cargo builds use `--locked`. The compiler version, platform,
Python, Go environment, source commit/status, native identity and binary hash
are recorded. `.gitattributes` keeps hashed and embedded source bytes identical
on Windows and Unix.

Enabling the Windows native gate also required one product portability fix:
local file URI conversion is shared by CLI filters, disk snapshots, workspace
buffers and LSP configuration. Windows drive paths use `file:///C:/...`, with
the drive in the path, not the URI authority. Local absolute paths retain
percent-escaped characters correctly; unsupported remote/UNC/device URIs are
rejected. Test fixture writers preserve explicit UTF-8 LF/CRLF bytes instead of
letting the host silently change them.

`tests/ci/run.py` contains the explicit portable/extended suite inventories.
The orchestration invokes existing `tests/e2e/*.py` consumers; it does not
duplicate their workflow fixtures or product assertions. Extended family tests
exercise accepted 3.6.0 behavior and existing version-boundary transitions.
Coverage of a historical transition does not declare that historical target
accepted: the product's accepted delivery target remains exactly 3.6.0.

Linux-only FIFOs, `/proc`, process-group signals, cgroup accounting and CPU
affinity tests do not establish equivalent operating-system behavior on macOS
or Windows. The native matrix executes the portable CLI/LSP and package checks
on those hosts without cross-compilation or emulation.

## Semantic results, not just exit codes

The orchestration requires both a successful consumer process and nonempty
semantic result files. Explicit `pass`/`passed` fields and assertion values must
be boolean `true`; strings, missing reports, empty selections and exceptions
fail. Family consumers' parent exit records are insufficient: the gate checks
the exact expected worker set, every worker's semantic `result.json`, and its
per-case results. `--prepare` runs cannot satisfy acceptance.

The reused checks verify diagnostic codes/severity/ranges, JSON report envelopes,
document versions, exact edit bytes, recovery after errors, and explicit JSON-RPC
error codes. Changes in this PR remove several old weak checks:

- A stale document update is checked with a subsequent query over the current
  buffer, instead of inferring correctness from a short absence of publication.
- Restored settings and undo must produce the expected edits against the current
  document version; a nonempty response or literal `True` is insufficient.
- Cancellation must return `-32800` exactly once, with no result, and subsequent
  requests must still work. Shutdown must return null and exit cleanly.
- Workspace removal must produce a new clearing publication after the removal;
  an earlier empty diagnostic cannot satisfy it.
- Invalid configuration must visibly publish `WF_ANALYSIS_UNAVAILABLE` on affected workflows and `WF_CONFIGURATION_ERROR` on the configuration buffer. Valid configuration and restored workflow diagnostic inventories must recover exactly.
- Unknown target output is checked as a semantic mismatch, rather than crashing
  the Python test while indexing a nonexistent schema.
- Historical exit expectations now match the current accepted-target policy:
  complete valid/error analyses exit 0/1; experimental/incomplete analyses exit 2.
- The valid expression-boundary fixtures supply their required parameter.
  Their success requires complete analysis with no diagnostics; refusal cases
  require the expression-specific incomplete diagnostic and exit 2. A deadline
  or an unrelated missing-argument error cannot satisfy either case.

The shared LSP client enforces absolute response deadlines even under unrelated
message traffic and saves traffic/stderr on initialization or shutdown failure.
CLI timeouts retain partial stdout/stderr and command receipts.

The re-audit consumers also exercise metadata literals beside real tagged and
raw references, applied Quick Fix bytes and version transitions for excluded or
internal-directory buffers, and both creation and deletion of an external
dependency without watcher notifications. The reconciliation consumer waits for
the real 30-second timer, compares queried clients with no-query controls, and
requires an unchanged idle Quick Fix to remain valid. Renaming a configured
scope must retire its old publication dependencies instead of triggering work
on every later maintenance tick. A missing publication is
a semantic failure; a dead server or an incomplete run is an infrastructure
failure. The Linux checksum consumer executes the production workflow command,
then the README verification command against delivered and relocated ZIPs. It
also requires tampering and the old path layout to fail verification.

`wf-reaudit-query-io.py` runs in the Linux extended suite and counts actual file
open events on its private fixtures with inotify. Local completion and isolated
configuration validation must open zero resource files. Each external completion
must acquire its selected dependency once and never read the unrelated scope.
Every request must still return the correct nonempty candidates. Elapsed times
are descriptive evidence, not the acceptance predicate; the existing constrained
load and latency gates remain in place.

## Fault-injection gate

`tests/e2e/ablate-ci.py` runs selected historical and re-audit recipes through
`mutation_support.py`. It builds its own baseline from the same isolated source
snapshot as the mutants; it cannot silently test a stale prebuilt executable.

| Mutation | Existing consumer / required observable change |
| --- | --- |
| Post-output deadline recheck | A completed report keeps its 0/1 exit status while stdout is blocked past five seconds |
| Unknown target admission | Delivery unknown-target report envelope |
| Document version guard | LSP stale-version buffer semantics |
| DAG cycle detection | Local workflow diagnostic codes |
| Stale code action identity | Delivery stale action error |
| Nested workspace ownership | Exactly one nested-root symbol |
| Request cancellation | Explicit cancellation response |
| YAML scalar edit encoding | Exact completion/edit bytes for five cases |
| Metadata raw binding | Literal annotations remain untouched and plain prose cannot block Rename |
| Foreground reconciliation baseline | New correct diagnostics after both creating and deleting a dependency, even after a foreground query |
| Obsolete publication scope | Rebinding a configured scope does not cause periodic invalidation of unchanged input |
| Editor-only scope acquisition | Exact immediate Quick Fix edits for excluded and internal-directory buffers |
| Isolated action configuration identity | Policy and default-namespace changes invalidate a previously offered edit |
| Configuration diagnostic path | A valid configuration buffer has an empty diagnostic inventory |
| Local completion acquisition | Correct local candidates with no scope resource reads |
| External completion acquisition | Correct selected-scope candidates with no unrelated scope reads |
| Query cache input identity | A disk change without a watcher cannot reuse stale completion candidates |
| Query cache effective configuration | Changing the default namespace changes isolated-document completion candidates |
| Tool output budget | Bounded combined stdout/stderr |
| Process tree cleanup | No surviving descendants after tool exit |

For each mutation, the baseline must pass, every source selector must match
exactly once, the mutant must compile, and the expected assertion must change
from true to false. Baseline and mutant case/assertion inventories must match.
Compilation errors, a missing Go toolchain, timeout, signal exit, malformed
results, empty cases, Python exceptions and a nonclean LSP exit are **errors**,
not successful mutation detections. A surviving mutant fails the gate. There is
no automatic retry or `continue-on-error` that turns a failure green.

The remaining historical `ablate-*` scripts are not claimed as CI gates. Some
still encode old local toolchain paths or historic expectations. The curated
entry point states exactly which mutations it executes and preserves their
source diffs, before/after hashes, builds, binaries and consumer evidence.

## Real resource limits

`tests/ci/resources.py` uses transient systemd services on the Ubuntu runner.
It verifies `memory.max`, `memory.swap.max`, `cpu.max`, and four-CPU affinity
inside each service before executing the consumer. Native builds occur outside
these measured cgroups. The worker interpreter receives setup-python's library
path before launch; the tests' own resource guards remain unchanged.

- Adversarial consumers run with 512 MiB and no swap.
- The legitimate exact 128 MiB snapshot input case uses its existing separate
  1 GiB process limit; overflow/refusal cases retain the 512 MiB limit.
- Fixed-load consumers run with at most four CPUs, 8 GiB and no swap.
- Services have a runtime bound and process-tree cleanup; failure to establish
  the requested cgroup fails acceptance.

The fixed workload intentionally alternates 50 accepted 3.6 scopes and 50
experimental 3.5 scopes. Its CLI result must therefore be exit 2 with exactly
the documented experimental reasons, all 10,000 file/resource counts, correct
scope identities and no unrelated errors. The existing five-second analysis
bound is still required. An unexpected deadline is not accepted as an
experimental-target result. Load tests run sequentially on their dedicated job;
they are not multiplied concurrently inside the same runner.

These are explicit workload/consumer bounds, not universal process-memory or
controller performance guarantees. An isolated Runner failure is evidence to
investigate; the workflow does not weaken a threshold to make it pass.

Identical document bytes may share one immutable YAML view within the current
analysis. URI, revision, target, projection and logical expansion admission keep
their own identities; the existing 50 MiB view-cache budget is unchanged. This
removes repeated parsing in the legitimate 128 MiB snapshot case without
increasing its deadline or input/memory limits.

The CLI also preparses small files in ordered batches of at most four, respecting
`GOMAXPROCS`, with at most 64 KiB of new text per batch. Workers only produce
immutable syntax views. The caller admits successful views in input order under
the same cache budget, then performs schema checks, diagnostics and global
alias/merge admission sequentially. Large files use the ordinary path. Errors
are not cached, and cancellation joins the current parser workers before the CLI
returns. Analysis still runs inside its original five-second deadline.

The CLI freezes cancellation/deadline state before publishing the completed
report. Top-level and target completeness, reasons and the exit code use that
same terminal state; a slow stdout reader cannot retroactively change a complete
result into exit 3. `wf-report-deadline.py` runs on every native host and blocks
output for more than five seconds after the first byte, checking complete JSON
success/error reports and human output against exact diagnostic inventories.
The core mutation restores the post-output live-clock check and must invalidate
all three exit assertions. The separate `wf-bounded-external-counts.py` consumer
continues to require a genuinely interrupted analysis with incomplete counts,
explicit external-analysis/deadline reasons and exit 3 within its original bound.

A small, documented [vendored JSON Schema queue patch](VENDORED_PATCHES.md)
removes repeated linear lookup during cold CRD compilation while preserving
schema identity and compilation order. The dependency remains pinned at v6.0.3;
the local patch and its vendor-regeneration requirements are explicit.

## Development package integrity

The package command archives the exact checked-out commit with
`git archive --format=tar HEAD`. Every adapter source, lock, snippet, example,
document and dependency notice comes from that archive. A VCS-stamped native
binary must be clean and match the archive's commit; its embedded catalog hash
must match the archived catalog bytes. CI requires
`sourceIdentity.status == "verified-vcs"`. Unstamped local builds record
`unavailable` and cannot be delivered by this CI gate.

This source identity check compares native Go build metadata and archived inputs;
it is not a claim of reproducible bit-for-bit Go builds.

`dev-bundle.py` verifies deterministic ZIP bytes, CRCs, exact archive/folder/
manifest inventories, every file hash, native executable permissions, extracted
native startup and full identity, and all required source/provenance/license
files independently derived from the original archive. Invalid inputs must fail
for the expected reason. Wrong-product/contract/readiness/target fixtures are
real compiled executables, so Windows execution-format failures cannot falsely
satisfy identity checks.

The final delivered ZIP must have the same actual SHA-256 as the already
integrity-tested deterministic ZIP. The workflow also compiles the actual
standalone Cargo manifest from the package and retains that WASM output separately. Development ZIPs
contain an `extension/` directory for **Install Dev Extension** and a separate `server/` executable selected with `serverPath`;
they are not preinstalled editor sessions or marketplace releases.

## Evidence and reruns

Every job uploads `artifacts/ci/` with `if: always()` and 30-day retention,
including failures. This directory contains only generated test/build evidence;
hidden fixture configuration files are included intentionally. Per-command raw
stdout/stderr, execution/timeout receipts, result JSON, input hashes, protocol
traffic, source/native identity, cgroup limits, packages and WASM outputs remain
inspectable. A job killed before artifact upload cannot promise a complete
archive; GitHub's job log remains the record of that interruption.

Use fresh output directories. From a clean checkout with the pinned Go and Rust
toolchains, Python 3.14 and a C compiler installed:

```sh
python tests/ci/run.py build --output artifacts/ci/build
python tests/ci/run.py suite --suite native \
  --binary artifacts/ci/build/argo-workflows-ls --output artifacts/ci/native
python tests/ci/run.py wasm --output artifacts/ci/wasm-source
python tests/ci/run.py package \
  --binary artifacts/ci/build/argo-workflows-ls --output artifacts/ci/package
python tests/ci/run.py wasm --package artifacts/ci/package/package.json \
  --output artifacts/ci/wasm-bundle
python tests/ci/install-tools.py --output artifacts/ci/tools
python tests/ci/run.py suite --suite extended --tools artifacts/ci/tools \
  --binary artifacts/ci/build/argo-workflows-ls --output artifacts/ci/extended
python tests/ci/run.py schema \
  --binary artifacts/ci/build/argo-workflows-ls --output artifacts/ci/schema
python tests/ci/run.py mutations --group core --output artifacts/ci/mutations
python tests/ci/run.py mutations --group audit --output artifacts/ci/audit-mutations
```

On Windows use the `.exe` native filename. Extended integration/tool installation
and resources require Linux; the resource commands additionally require systemd,
cgroup v2, four CPUs and noninteractive sudo:

```sh
python tests/ci/resources.py --suite adversarial \
  --binary artifacts/ci/build/argo-workflows-ls --output artifacts/ci/adversarial
python tests/ci/resources.py --suite load \
  --binary artifacts/ci/build/argo-workflows-ls --output artifacts/ci/load
python tests/ci/run.py mutations --group resources --output artifacts/ci/resource-mutations
```

## Runner acceptance versus Zed editor acceptance

Successful Actions establishes only the checks listed above at the run's exact
commit, platform and toolchain. It does **not** launch a real Zed editor. The
workflow summary states `editorAcceptance: not-executed`.

Real editor acceptance remains a separate recorded procedure: Install Dev
Extension on a clean editor profile, no server override, verify pinned release download and warm-cache offline startup,
diagnostics, applied completion, definition/navigation, valid and refused rename,
Quick Fix plus undo, settings forwarding, error visibility/recovery, cache repair,
and coexistence with YAML/Helm servers. Record Zed version, OS/architecture,
extension/native hashes, settings, logs and observed outcomes. Use the explicit per-platform checklist in [Distribution and store readiness](DISTRIBUTION.md). A previous local
Zed result must not be attributed to a different CI artifact or another platform.
