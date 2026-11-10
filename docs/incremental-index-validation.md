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
