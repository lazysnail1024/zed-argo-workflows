# Incremental workspace validation

These observations were collected on Linux with Go 1.27.3 through the real native
stdio server. They are individual development runs, not a five-run cold-start
benchmark, a 100-sample p95 claim, or Zed GUI acceptance. The commands below retain
protocol traffic, stderr, result JSON and executable/test SHA-256 identities.
The native CI workflow runs the required consumers again on the submitted source.

## Source dependency

Both Argo plugins use toolkit source commit
`79c46c0625b94d61156e40994ac603db69e2438f` through Go pseudo-version
`v0.0.0-20261110063134-79c46c0625b9` and checked-in vendor sources. There is no
release-tag prerequisite, local replacement or sibling-checkout dependency.
The contract is `toolkit-design-2`. Native and adapter version/download pins are
independent of this source change.

## Closed-text cache removal

The first persistent catalog also kept a 128 MiB LRU of closed source text.
Inspection showed that acquisition still read the file to verify its content
before consulting that cache. It duplicated retention already possible in the
separately bounded 8 MiB analysis-facts cache, without avoiding that disk read.
The catalog now retains summaries and revision identities; closed source text
is released after acquisition/analysis. Open buffers remain authoritative.

The same strict consumer and generated input were run before and after that
removal. The input contains 50,000 WorkflowTemplate resources padded with 4 KiB
comments, plus the current Workflow and its related template: 214,539,337 input
bytes. The consumer continuously edits the open Workflow until initial Ready,
then checks unchanged events, transitive signature invalidation, observation of a
missed watch, discovery of an entirely new unwatched URI and a fully completed
second reconciliation pass. It reads the process's kernel `VmHWM`, rather than
using an application cache counter as a proxy for RSS.

| Observation | Candidate retaining closed text | Candidate releasing closed text |
| --- | ---: | ---: |
| First local diagnostic | 0.575 s | 0.601 s |
| First complete Ready | 23.282 s | 27.912 s |
| Open-document edits during discovery | 148 | 179 |
| Catalog retained text at Ready | 134,215,819 bytes | 286 bytes |
| Process peak RSS across two passes | 659,156,992 bytes (628.6 MiB) | 290,811,904 bytes (277.3 MiB) |
| Repeated unchanged events | 4 reads, 0 parses | 3 reads, 0 parses |
| New unwatched URI discovered | 36.759 s | 36.662 s |
| Complete second-pass Ready | Yes | Yes |
| Assertions passing | 17/18; RSS failed | 18/18 |

This is a candidate comparison with cache removal as the principal memory fix,
not a strictly single-variable performance experiment. The later candidate also
contains resource-name completion from summaries and a workflow-parameter
definition fix. This workload does not request those completions or parameter
definitions. Different counts of concurrent edits also prevent interpreting the
two Ready times as a controlled throughput result.

The accepted candidate did not raise the 512 MiB process-RSS assertion. It still
reads closed content when necessary to verify missed events, and unchanged
content reuses summaries rather than redoing workspace parsing.

Recorded identities for this comparison:

- Consumer: `tests/e2e/wf-persistent-workspace.py`, SHA-256
  `d27bad316c3208fc320921d6a01fd7103cd7385d7c10a1d3a80c935da7d089c7`.
- Before executable: `d1c2b40f2bf570a8a0cb560e6b82f9b78510497ed5ea1f6d0a84048a81b08427`.
- After executable: `2893ba59c06b648a25f5a4336c4796f484ed3f42137727b3ae104c803f54e773`.

```sh
python tests/e2e/wf-persistent-workspace.py build/argo-workflows-ls \
  artifacts/persistent-50000 --count 50000 --padding 4096 \
  --resource-every 1 --deadline 300 --require-slow
```

These are development-candidate identities, not release artifacts. Subsequent
query-proof and budget-boundary fixes are checked by the final native consumers.

### Shared request-source allowance

Resource acceptance found a separate boundary in dependency materialization.
A real stdio probe filled the open-buffer allowance with 25 buffers of 5 MiB and
a 3 MiB caller, then requested a repair whose actual templateRef dependency was
a 142-byte closed file. The original catalog counter started at zero for that
closure and offered the repair despite the already full 128 MiB reservation.

The corrected reader uses the existing request snapshot budget, and the duplicate
catalog counter is removed. The same request now refuses the additional closed
input; closing one 5 MiB buffer makes the exact repair available again. These
controls use a real dependency, not an unrelated large file that a summary query
has no reason to load. Separate alias-graph controls preserve four valid repairs
at 100,000 materialized nodes, refuse them at 100,001, and restore them after the
input returns to the boundary.

These local protocol probes had no cgroup resource controller. They establish
the semantic refusal and recovery only; the default resource consumers retain
their required cgroup checks in GitHub CI. Their explicit local-only mode records
`resourceAcceptance: false` and does not stand in for constrained RSS acceptance.

### Cold-schema compilation ablation

The linked-repair buffer fixture exposed a second issue after its raw-source
budget was corrected: foreground field fixes and background diagnostics could
miss the same compiled-schema cache concurrently. A diagnostic-only heap/profile
overlay recorded both compilations of the same embedded file overlapping. The
cache retained only one result, but both compilation object graphs existed
during construction.

Two independent changes and a clean unfixed control were tested against the same snapshot consumer
(`b535e73a0f2f10234f19fc800d317860d4192f3bf7bb3dc84b8ca27c30d388d2`)
and its unchanged 448 MiB local process guard:

| Single change | Observation | Decision |
| --- | --- | --- |
| No compile admission change; corrected source budget only | Process reached 477,724,672 bytes (455.6 MiB) and was killed during the linked dependency refusal | The source-budget correction alone does not bound simultaneous compiler construction. |
| One cancellable permit for cold schema compilation | All six semantic cases passed; process peak 426,102,784 bytes (406.4 MiB) | Keep. Warm cache hits bypass the permit; a second cache check after admission prevents duplicate work. |
| Reduce the transport input channel from 16 messages to 1 | Process reached 475,250,688 bytes (453.2 MiB) and was killed during recovery | Reject this change; it did not fix the demonstrated cause. |

The first permit-only pass did not establish a stable memory bound. An independent
run of the landed permit candidate
`d469f577e9cf6bbeaf3fdaf37d8cf5641fb8e2907b1234d9a9f859b0a54202c9`
passed the first five semantic cases but reached 472,662,016 bytes (450.8 MiB)
during recovery and was killed by the same guard. That failed run is retained;
duplicate-compilation prevention alone is not reported as complete resource
acceptance.

The compile permit adds no second schema cache and retains no failure or document
source. Waiting callers can cancel, and every acquired permit is released on
success or error. Existing in-progress compiler work is not made cancellable by
this change. A completed successful compilation is cached as immutable embedded
data, while a cancelled initiating caller still receives cancellation. The clean
control executable was `3fd5c93a9c9b7c38c6345e748af89b5c05bffc7645f931b5af2f5ac5f4397f95`
and ran without GC tracing or profiling instrumentation. The permit
candidate executable was `a31e8b8eab0361a89b3230475ea87cc77ad8a248ec6865c8700a933311ffb3d9`;
the transport-only candidate was
`ac1ed55d18d81dc2326c5e3fd6cc6a1d8d1f45bd36310d9bc0d29246ea882c40`.
These are local development observations, not whole-cgroup acceptance: the
required CI consumer still measures its complete enforced process group.

### Runtime headroom and completed-work retention

The same landed `d469f577...` binary and frozen `b535e73a...` consumer were then
run without GC tracing, changing only the `GOMEMLIMIT` environment setting:

| Go soft target | Semantic cases | Kernel process peak RSS |
| --- | ---: | ---: |
| 288 MiB | 6/6 | 390,307,840 bytes (372.2 MiB) |
| 320 MiB | 6/6 | 413,577,216 bytes (394.4 MiB) |

Both kept the original 448 MiB process guard and the exact 128 MiB open-text
allowance. These single observations demonstrate a meaningful headroom effect;
they are not repeated-run percentiles or whole-cgroup acceptance. The native
LSP now applies the 288 MiB soft target, preserves a lower caller limit and
restores the previous setting at shutdown. C/native allocations and mappings
remain outside this Go limit, so the independent process/cgroup gates remain
necessary. The CLI's policy is unchanged.

Several unnecessary lifetimes and work paths were removed as well:

- Completed worker results no longer carry parsing contexts or borrowed
  workspace/buffer maps that the owner never reads.
- Pending edit plans detach expected and replacement text instead of retaining
  whole superseded source files through short substrings. Tokens and stale-edit
  rejection behavior are preserved.
- An incomplete selected scope returns an empty action list before computing
  fixes that would all fail the existing scope-safety gate.
- A successful immutable embedded-schema compile is retained even if the
  initiating request was cancelled while construction was running; failures are
  still never cached.

A per-URI background invocation reset was also tested, but was not adopted:
it did not establish an additional stable resource bound and would discard
useful decoding reuse. Neither it nor the transport-channel reduction is part
of the final implementation. The focused Go tests cover cancellable cold waits,
unblocked warm hits, recovery after cancellation and restoration of a lower
runtime memory policy.

The integrated candidate
`18fb64e38f5f6d60e8c37cf5ac9b5539acee990a2e64367821bf0088dd89e7f7`
passed all six snapshot cases with the built-in policy and no `GOMEMLIMIT`,
`GOGC`, `GODEBUG` or `GOMAXPROCS` environment setting. Its observed process peak
was 389,853,184 bytes (371.8 MiB), below the unchanged 448 MiB guard. The updated
consumer records these environment inputs in its manifest; this local result
still explicitly has `resourceAcceptance: false`. Existing action lifecycle
checks passed 8/8, exact Quick Fix checks 31/31, and focused race tests passed.
The same integrated candidate passed all five preflight cases and all thirty
semantic-origin cases with the same clean environment. Default CI execution of
the resource consumers continues to require the original cgroup controllers and
limits; only these explicitly marked local runs use the semantic-only mode.

## Mixed ownership and source freshness

`wf-mixed-parent-index.py` drives actual initialize/open/change/watch/close/delete
and recreate messages. Eleven checks cover resource recognition, foreign YAML,
related definition lookup, buffer authority, fresh disk restoration, immediate
withdrawal of stale locations and clean shutdown. Both the original main binary
and the refactored binary passed the small 2,048-file fixture; these are preserved
semantics, not an invented baseline failure.

The refactored candidate also passed all eleven checks with 50,000 ordinary YAML
files padded to 4 KiB. Its current-file first publication was 0.716 s in that run.
This fixture proves prioritized interaction during discovery. The all-owned
50,000-resource run above separately proves full membership and bounded retention;
neither fixture is presented as the reference 90/9/1 ownership mix.

```sh
python tests/e2e/wf-mixed-parent-index.py build/argo-workflows-ls \
  artifacts/mixed-parent-50000 --ordinary-files 50000 --padding-bytes 4096
```

## Conditions that remain necessary

The refactor removes the repeated whole-root acquisition path and the redundant
closed-text LRU. It keeps the mechanisms for which real counterexamples exist:

- Revision and event checks at commit and response delivery prevent old positions
  or superseded disk reads from becoming authoritative again.
- Rejected open buffers remain authoritative refusals; falling back to the disk
  would resurrect content the editor has replaced.
- Scoped completeness is needed for absence, unique reference binding and edits.
  Known positive candidates can be used while discovery continues.
- Reconciliation compares actual content and completes a new pass before claiming
  that an unwatched creation or deletion has converged.
- A query that observes changed content must still cause the corresponding
  diagnostic publication; consuming a queue item cannot swallow that obligation.

The focused Go catalog/index tests, mixed-parent consumer, persistent consumer and
existing scope, edit, delivery and query consumers exercise those boundaries.
Timeouts, build failures and missing result files are failures, not successful
negative controls.


## Shared lifecycle contraction — 2026-11-10

This follow-up starts from consumer commit `0b18cd72edb5ced85ab794ec800d9b0e00cf409a` and uses the real
`k8s-crd-toolkit` source commit `b1e411d4b3abe5c1ad70518be161c1daff717d61`,
Go module version `v0.0.0-20261110114800-b1e411d4b3ab`, and
[contract 3](../contracts/toolkit-design-3.md). The earlier PR12 measurements and
source identities above remain historical evidence for that earlier change.

### Removed mechanisms and remaining policy

- `Documents` now owns accepted source snapshots and open-text accounting.
  The transport keeps LSP versions, refusal state and borrowed immutable
  snapshots. The catalog no longer copies accepted buffers through a second
  full-text update or tracks `observed`, `session.bufferBytes` or `catalog.openBytes`.
  Configuration and root-external buffers remain part of the same allowance.
- `WorkQueue` owns ready jobs, overflow, running URI reservations and coalesced
  follow-up work. The catalog has no `dirty`/`running` flags, periodic full-file
  refill loop or second discovery-batch backlog. The ready limit is still 512;
  100,000 file entries plus the two fixed retiring worker reservations bound
  distinct outstanding work. Capacity failure remains explicit incomplete state.
- `Membership` supplies pass/observation tokens without a second file registry.
  Open/change/close and watcher observations protect current inputs. Sweep
  admission also checks the registered root identity, configuration epoch and
  the scanner's `Complete` result; `Done` alone is insufficient.
- `Index.Reuse` restores retained facts through a current ticket only after the
  consumer confirms identical bytes and effective configuration, under the same
  catalog lock as `Begin`. The `previous`, `factsCost`, `previousBytes` and
  `releasePrevious` copy ledger is gone.
- The CLI and editor share target/configuration selection, disk membership and
  stable bounded single-file reading. `LoadEditor`, its editor-only collection
  parameters and the duplicate reader/target construction path are removed.
  CLI selection still precedes validation of an unassigned file's default target;
  the one-shot collection, 50,000-file cap and diagnostic semantics remain.

For tracked non-vendor production Go under `internal/`, the change is
`+310/-517`: **207 net lines removed**. This figure excludes tests,
documentation, dependency locks and generated vendor sources. The three catalog
files are 1,422 lines combined, down from 1,449; the larger reduction comes from
removing duplicated workspace acquisition. This is not a claim that all remaining
catalog logic is Workflow semantics: root/configuration policy, completeness,
dependency materialization and LSP publication proofs still live there.

### Verification and source identities

The production package race command passed after the real module pin and vendor
regeneration:

```sh
go test -race -mod=vendor ./cmd/... ./internal/... ./compat/...
go build -mod=vendor -o /workspace/scratch/c09d266a9205/workflows-lifecycle-validation/argo-workflows-ls-pinned ./cmd/argo-workflows-ls
```

An initial literal `go test -race ./...` also traversed pre-existing ignored
`artifacts/` directories containing mixed-package diagnostic overlays, and failed
on those overlay packages. Those evidence files were preserved. The explicit
production package roots above cover all application packages without treating
saved fault-injection sources as standalone packages.

Two runtime stages are distinguished:

| Stage | Binary SHA-256 | Observed result |
| --- | --- | --- |
| Integrated mechanism candidate, before the final module pin and explicit document-entry refusal recovery | `250f2a101f0ef18dae4f99b0fe741fcc30836c007564fd5312184ee8ac453cf4` | 19 existing CLI/stdio runs; 84 cases and 454 explicit assertions passed. |
| Final pinned source, including document-capacity refusal/recovery | `4cf4db0c70449e3b64148be9343b7d03cd8cbe142d67abbe0761e606d13e2cd7` | Six targeted CLI/stdio reruns; 29 cases and 109 explicit assertions passed, plus production-package race tests. |

The 19-run stage covered `wf3-lsp`, `wf-default-target-provenance`,
`wf-remediation-targets`, `wf-remediation-lifecycle`, `wf-lifecycle-gaps`,
`wf-index-boundaries`, `wf-mixed-parent-index`, `wf-query-fact-handoff`,
`wf5-edits`, `wf-reaudit-query-io`, `wf-bounded-input-files`,
`wf-preflight-budget`, `wf-bounded-config`, both snapshot-budget modes,
`wf-persistent-workspace`, `wf-persistent-slowio`, `wf-index-publication` and
`wf-reaudit-reconciliation`. The final pinned reruns were `wf3-workspace`,
`wf-lifecycle-gaps`, `wf-index-boundaries`, `wf-remediation-lifecycle`, the
snapshot-budget refusal/recovery mode and `wf-persistent-workspace`.

Each runtime invocation used `python tests/e2e/<consumer>.py <binary> <output>`.
The snapshot refusal invocation additionally used `WF_BUDGET_CASES=refusal` and
`--semantic-only`; the exact-budget invocation used `WF_BUDGET_CASES=exact` with
the same option. The commands, exit codes and environments are retained in
`/workspace/scratch/c09d266a9205/workflows-lifecycle-validation/final/commands.json`
and `pinned/commands.json` under the same evidence directory. Per-run result JSON,
stdout and stderr remain alongside those manifests. The Go toolchain was 1.27.3;
module resolution used `-mod=vendor`, `GOTOOLCHAIN=local` and `GOWORK=off`.

For the final pinned persistent fixture, all 4,098 files reached Ready; first
current-document publication was 0.626 seconds and readiness was 0.673 seconds.
Repeated unchanged file notifications added four reads and zero parses. The
separate 15 ms-per-read control reached Ready in 8.102 seconds, while its first
current-document publication took 0.489 seconds. It retained the five-second
request deadline and proved discovery continues beyond one request lifetime.
These are individual development observations, not percentile measurements.
The 10,001-file natural reconciliation publication gate also passed without a
watcher shortcut or shortened 30-second timer.

Snapshot-budget runs used their existing `--semantic-only` mode because this
workspace does not expose the required cgroup memory files. Both exact 128 MiB
acceptance and refusal/recovery passed their unchanged process guards; these runs
do not claim whole-cgroup resource acceptance. No production limit, deadline or
semantic assertion was relaxed.

The historical `wf3-workspace` consumer expected exit 2 for its mixed 3.5.0/3.6.0
scope fixture. The unchanged base binary and the refactored binary both returned
exit 1 with complete analysis and supported targets. The consumer now expects
exit 1 only for that diagnostic-producing supported-target case and explicitly
checks `analysisComplete` and non-experimental targets. Invalid configuration
cases retain exit 2. The same revised consumer passed all ten cases against both
the base-source control and the pinned candidate.

### Regression controls

The added integration tests exercise sequential UTF-16 changes, atomic rejection
of partially invalid notifications, stale LSP versions, global source accounting,
configuration's 1 MiB limit, refused-buffer recovery, root removal while buffers
stay open, monotonic reopen revisions, entry-capacity recovery, obsolete root and
configuration scans, post-scan save/close observations, and canceled exhausted
scanner results.

One-mechanism source overlays were compiled separately; each had to fail its
specific behavioral assertion, not merely fail to build:

| Removed protection in the control | Required failure |
| --- | --- |
| Preserve editor authority when an index root is removed | Root removal closed an accepted editor snapshot. |
| Configuration's 1 MiB preflight | Oversized configuration became available. |
| Registered-root/configuration-epoch sweep fence | An obsolete scan deleted current-root membership. |
| Refusal advances the file input stamp | A captured file proof survived refused replacement. |
| Close observes the current scan pass | Sweep deleted a saved file before its fresh disk read. |
| Failed enumeration cannot authorize sweep | Incomplete enumeration deleted known membership. |
| Canceled `Done` does not imply `Complete` | A canceled exhausted scan established deletion/readiness. |

The fixed recipes are committed in
[`tests/e2e/ablate-shared-lifecycle.py`](../tests/e2e/ablate-shared-lifecycle.py).
It first compiles and runs the unmodified existing Go tests, requiring each
selected test to pass. Each overlay must then compile successfully and fail the
named original test at its specified `catalog_test.go` assertion. Compilation
errors, missing tests, timeouts, panics and unrelated failures are not detections.
The script only writes overlays and evidence outside the production source files;
it neither changes the CI matrix nor requires the earlier local artifacts.

```sh
# All seven controls; prints and preserves a new temporary evidence directory.
python tests/e2e/ablate-shared-lifecycle.py

# Optional empty output directory and a single control (repeat --variant to select more).
python tests/e2e/ablate-shared-lifecycle.py /tmp/wf-shared-lifecycle-controls --variant obsolete-root-sweep
```

The compiler comes from `GO`, then `GOROOT/bin/go`, then `PATH`; the existing
environment is inherited. No machine-specific toolchain or cache is hardcoded.
This round reran the positive tests and all seven controls with Go 1.27.3 using:

```sh
python tests/e2e/ablate-shared-lifecycle.py /workspace/scratch/c09d266a9205/workflows-lifecycle-validation/reproducible-controls
```

That directory retains `results.json`, every exact build/run command and exit
code, stdout/stderr, and each generated overlay. The script exits unsuccessfully
if the positive run fails or any selected control is not detected.

All seven controls were detected by the targeted tests. Production source was
never replaced in place by these controls. Runtime commands, results, stdout and
stderr were recorded separately for the integrated, pinned, base-control and
negative-control stages.
