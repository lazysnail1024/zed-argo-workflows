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
