> Historical pre-alpha evidence. Current architecture experiments are in [the alpha ablation report](../ablation.md).

# Design ablation and Actions repair — 2026-11-05

Baseline: `742cdf409f48af9b32af2ba80d39ba20375b7208` (`main`).
The experiments use the pinned Go 1.27.3 and Rust 1.99.1 toolchains,
vendored Go dependencies, and real CLI/stdio LSP consumers.

## Method and decisions

Each removal was built and checked against the same observable contract as its
baseline. Compilation alone is not evidence that a design is unnecessary.
Passing existing behavior justifies removal of unused plumbing; a required
behavior or useful measured performance benefit justifies retaining a layer.
Negative controls must compile and fail the intended assertion, rather than
crash or time out in the harness. No new general ablation framework is added.

| Candidate | Decision | Evidence / remaining responsibility |
| --- | --- | --- |
| `session.factsEpoch` | Remove | Written when accepting facts but never read. Worker epoch validation and snapshot/target identities continue to own freshness. |
| `workspace.LoadWithConfig` | Remove | No production or test callers. The actual CLI loader and editor overlay loader remain. |
| Parent-node return from `markerPath` | Remove | Its only caller discarded it. Keep the marker path, field name, exact-offset check and cancellation checks. |
| Separate `PreparseBatch` API and workspace-symbol batch loop | Remove | Workspace symbols use the existing bounded document visitor also used by CLI analysis. Parsing remains bounded and joined; semantic work stays ordered on the owner. |
| `directory_with_create` generic callback and directory preflight | Remove | Direct atomic `create_dir`, with type validation on `AlreadyExists`, passes the same 25 Rust cases as the old production code. |
| Argo CD dispatch in Workflows candidate staging | Remove | This repository has one extension ID and two required WASM variants: source and developer package. Original release/candidate assertion maps remain unchanged. |
| Alternate `package.binarySHA256` checksum field | Remove | The native release producer emits `binarySha256`. The fallback accepted a record missing that required field; the fixed consumer rejects it atomically. |
| Existing-cache directory type validation | Retain | Removing it compiles, but real regular-file and symbolic-link cases fail their intended assertions. |
| LSP final-response memoization | Retain | On the dense external-candidate fixture, deleting reuse preserves answers but makes repeated identical queries substantially slower; details below. |

The retained installer `Host` boundary, immutable published cache generations,
checksum and native-identity verification, cancellation, input limits, schema
projection and source identities continue to serve concrete contracts.

### Go removal comparisons

The same existing consumers pass before and after each isolated removal:
23 fact-handoff cases for the unused epoch; 14 target/configuration cases for
the unused loader; 75 marker, projected-context and schema-query cases for the
discarded parent return. The shared visitor is checked by projection/expansion
budget, lifecycle/shutdown, fact-handoff and query consumers. Its caller still
rejects cancellation or expansion failure before publishing any symbol list.

On a separate 2,000-file fixture, three independent baseline and visitor runs
return the exact same 4,000-symbol response hash. Baseline queries take
1.040–1.133 seconds; the shared visitor takes 0.761–0.920 seconds in that local
experiment. These descriptive samples do not replace the constrained Actions
gate. Local 10,000-file trials include deadline failures in both variants; they
return explicit protocol errors rather than partial success and are not counted
as passing timing evidence.

## Actions failure: report and exit code disagreed

The original [main run 41992515786](https://github.com/lazysnail1024/zed-argo-workflows/actions/runs/41992515786)
failed `Linux semantic / shard 2`. The original
[`wf-bounded-external-counts` artifact](https://github.com/lazysnail1024/zed-argo-workflows/actions/runs/41992515786/artifacts/13398543293)
contains this 4,000-file outcome:

| Observable | Original result |
| --- | --- |
| Report `analysisComplete` | `true` |
| Files / resources analyzed | `4000 / 4000` |
| Diagnostics | 60,000 complete entries |
| `notEvaluated` / `configurationErrors` | Both empty |
| Report elapsed time | 4,936 ms |
| Process elapsed time | 5.237680825 s |
| Exit code | `3` (deadline/cancellation) |

Analysis completed before the five-second deadline. Serializing and writing its
large report crossed the deadline. A second `ctx.Err()` check after output
changed the exit code of the already published report. The failing consumer was
correct to reject that contradiction.

The CLI now snapshots its terminal analysis error once, immediately after
analysis (or on workspace-load failure). Target completeness, report
completeness, reasons and exit status use that same snapshot. The five-second
analysis deadline is unchanged. The original bounded-external-counts consumer
and its assertions are unchanged.

### Deterministic real-process regression

`tests/e2e/wf-report-deadline.py` reads the first stdout byte, stops reading for
5.25 seconds, confirms the writer is still blocked, and then drains the actual
process. It verifies exact diagnostics, complete counts and the exit code.

| Case | Original CI binary | Fixed binary |
| --- | --- | --- |
| Complete JSON, informational diagnostics | Wrong exit `3` | Exit `0` |
| Complete JSON, error diagnostics | Wrong exit `3` | Exit `1` |
| Complete human-readable output | Wrong exit `3` | Exit `0` |

For all three original-binary cases, only the exit/outcome assertion fails.
Restoring the old post-output deadline check in a compiled mutation reproduces
all three failures. The fixed baseline passes, and the mutation is killed by
the exact `exitMatchesPublishedOutcome` assertions. This consumer runs in the
five-platform native inventory; the mutation is in the core gate.

The separate real-deadline control also passes: one local fixed-binary run
completed 1,844 of 4,000 files/resources, reported 2,156 external-incomplete
reasons plus the deadline reason, marked the target incomplete, and returned
`3` in 5.181 seconds. These counts are observations of that run, not fixed
expectations; the test requires honest counts and genuine incomplete analysis.

## Installer and distribution controls

Both the original and simplified directory implementations pass the same
expanded 25-case Rust suite. The suite uses real files for new/existing roots,
retained sentinel contents, occupied regular-file paths and symbolic links.
Removing the retained `AlreadyExists` type check produces compiled assertion
failures in `shared_root_creation_accepts_only_a_directory` and
`cache_symlinks_are_never_executed_or_overwritten`.

Linux amd64 release/candidate E2E passes 49/49 before the change and 52/52 after
it. The existing 49 assertion maps match. Three additional cases reject a
missing canonical hash, a wrong hash, and a record that supplies only the old
checksum alias. Every refusal checks the specific error and absence of partial
published output. The last case was incorrectly accepted by the baseline.

Source and independently extracted developer-package WASM builds are
byte-identical within each variant. The baseline component is 443,873 bytes;
the simplified component is 443,774 bytes. This is a size observation, not a
runtime performance claim. Foreign-platform records in this local aggregation
test are synthetic controls; native execution on all platforms belongs to the
Actions matrix.

## Why the response cache remains

A separate temporary source variant makes `cachedScopeQuery` return
`nil, false`, so each request computes its response from fresh acquired input.
It retains parse/projection facts and all semantic checks. Both binaries run
serially with four-CPU affinity and `GOMAXPROCS=4`, using the unchanged
`wf-external-candidate-density.py` consumer (100 files, 50 candidate resources
and 50 callers). All 11 queries and CLI behavior remain correct in both runs.

The second and third identical resource-definition queries take **3.74 / 3.47
ms** with the cache and **492.36 / 401.69 ms** without it. This is sufficient
evidence to keep reuse for repeated dense queries; it is not a claim that the
whole dense test meets its budget. Both local variants exceed that consumer's
100 ms budget on first/uncached queries. The canonical 10,000-file external
query experiment also lacks a passing local timing baseline (P95 143.94 ms
versus 124.50 ms after removal), so it cannot establish performance acceptance
for either design. No timing threshold was relaxed, and neither result is
reported as a passing constrained resource gate.

## Reproduction

Use fresh output directories and the pinned toolchains. Ordinary comparisons
use independent baseline and modified binaries, not a stale executable:

```sh
python3 tests/ci/run.py build --platform linux/amd64 --output artifacts/build
python3 tests/ci/run.py suite --suite native \
  --binary artifacts/build/argo-workflows-ls --output artifacts/native
python3 tests/e2e/wf-report-deadline.py \
  artifacts/build/argo-workflows-ls artifacts/report-deadline
python3 tests/e2e/wf-bounded-external-counts.py \
  artifacts/build/argo-workflows-ls artifacts/external-deadline
python3 tests/ci/run.py mutations --group core --output artifacts/mutations-core
cargo test --locked
```

The original full Linux native baseline passed **22 suites / 312 cases**. The
modified inventory adds the three slow-output cases. Actions additionally runs
the five actual native hosts, extended semantic shards, actual/synthetic schema
contracts, unchanged constrained-resource consumers, all mutation groups and
the distribution-candidate aggregate. The PR's latest run is authoritative for
its exact commit; local results do not substitute for that run or for real Zed
editor installation and interaction.
