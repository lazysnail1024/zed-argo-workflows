# Incremental editor workspace

The stdio server keeps one catalog for the session. Request and diagnostic workers
may still be cancelled or reach their five-second deadline; that does not discard
discovery progress or the file index. The CLI retains its one-shot `workspace.Load`
contract and existing analysis deadline. It shares configuration/target selection,
file membership and the stable bounded single-file reader with the editor; only
its collection and selected-scope behavior remain separate.

The implementation consumes `k8s-crd-toolkit` source commit
`b1e411d4b3abe5c1ad70518be161c1daff717d61` through the pinned Go pseudo-version and
reproducible vendor tree. It does not require a sibling checkout, local `replace`,
or a new toolkit release. See [the source dependency lock](../contracts/toolkit.lock.json),
[the shared contract](../contracts/toolkit-design-3.md), and
[measured validation](incremental-index-validation.md).

## Responsibilities

| Component | Persistent state and purpose |
| --- | --- |
| Toolkit `Documents` | The single authoritative open-buffer store, monotonic revisions, global open-text accounting and read tickets. The LSP adapter retains editor versions and borrows the accepted immutable snapshots. |
| Toolkit `Scanner`, `Membership` and `WorkQueue` | Resumable discovery, pass-scoped observations, and bounded current/related/background work. The queue owns overflow, running reservations and coalesced follow-up notifications; workers wait for its wake signal. |
| Toolkit `Index` | Atomic per-URI detached symbols, references, missing-key dependencies and semantic digests. Pending inputs hide old locations. The index does not assert workspace completeness. |
| `analysis/index.go` | Workflow resource identity and detached summaries. Namespace, resource kind, reference policy, template signatures and semantic origins remain product decisions. |
| `lsp/catalog*.go` | Registered-root ownership, configuration metadata, file admission, scope completeness, dependency traversal and publication scheduling. |
| Existing analysis/query/edit engine | YAML/schema/local rules, external template inheritance, DAG/steps semantics, ambiguous references, exact origins, Quick Fix checks and versioned edits. |

A symbol key encodes resource kind, effective namespace and resource name; the
index scope is the existing configured target identity. `WorkflowTemplate` and
`ClusterWorkflowTemplate` therefore cannot collide. A file may contain several
resources. Static references with exact scalar locations enter the navigation
index. Derived/shared aliases keep semantic dependencies without inventing a
navigation span. Missing targets also have dependency keys, so later creation
can wake callers that never had a successful binding.

A file digest includes the semantic resource specification and effective target
configuration. Changing inputs, outputs or inherited defaults invalidates callers
without requiring a name change. Moving a comment updates spans and source
identity without reporting a semantic signature change. Dependency publication
walks exported keys transitively, with key and URI deduplication, so an unchanged
middle template in `A -> B -> C` cannot hide a changed interface in `C`.

## Foreground behavior and completeness

Opening a real Workflow first runs its own YAML, schema and local analysis.
Those diagnostics do not wait for unrelated directories. Current files and their
directories receive priority while discovery continues. Foreign YAML and foreign
Argo resources retain the existing diagnostic-ownership behavior; Helm source
boundaries and mixed YAML streams remain governed by the existing product rules.

A direct request for a closed document waits only for its root configuration and
target metadata to be described, within the request context. It does not wait
for directory discovery. The resulting local query carries the root epoch and
known input stamp to response delivery; pending metadata is not treated as an
invalid target version.

Known positive external definitions and resource-name completion candidates can
be used during discovery. Missing-target diagnostics, unique reference binding,
Rename and safe Quick Fix proofs require complete inputs for the selected scope.
An error attributable to scope A blocks A; it does not invalidate a completed
scope B. Failed directory enumeration or an input whose scope cannot be determined
blocks proof for the affected root. Partial enumeration never deletes known files
merely because the failed walk did not see them.

Queries materialize their known dependency closure and release its source texts
with the worker. References and edits also traverse relevant reverse dependencies.
For a resource-name Quick Fix, the existing safe evaluator receives at most 1,000
known alternatives of the same resource kind and namespace. Name completion is
similarly bounded to 1,000 indexed candidates. These are bounded candidate lists;
they do not claim an exhaustive result when truncated. Workspace symbol search
returns at most 20,000 known fresh domain symbols in stable URI/traversal order.
A cold workspace-symbol request may therefore return a partial inventory promptly;
the terminal Ready state proves discovery has finished before an exhaustive test
of a fixture below that result cap.

Results carry file stamps, root epochs and the generations of scopes whose
completeness was used. Delivery rejects obsolete results even if a background
index commit occurred without a foreground LSP event. A watched modification
immediately hides old locations. While replacement facts are pending, a query can
return no candidate or standard `ContentModified`; it cannot return a withdrawn
location. Code-action tokens additionally include existing scope generations so
new files outside the materialized closure cannot evade a previous edit proof.
Editor-only isolated scopes retain their independent effective configuration.

A rejected oversized open buffer remains authoritative as unavailable input;
it must not fall through to the old disk file. Closing a buffer restores disk
authority and schedules a fresh read. Adding or removing a registered workspace
root starts a new discovery pass because longest-root ownership changed, even
when all file bytes and configuration contents are unchanged.

## File events and reconciliation

Normal open/change/save events reuse configuration metadata. They do not read
all root configuration files on the transport owner. Config overlays, explicit
configuration events, override changes and new roots refresh metadata through
`workspace.Describe`; resource discovery remains separate. Header refreshes use
an epoch check so an old read cannot overwrite a newer configuration event.

A watcher or explicit save schedules only relevant file work. Open buffers win
over disk watcher events. Unchanged closed bytes reuse committed summaries;
repeated unchanged watcher events do not reparse the workspace. The current
publication may still read its own known dependency closure.

A bounded reconciliation pass starts roughly 30 seconds after the previous pass
finishes. It enumerates and reads closed candidates to detect missed events,
including same-size/same-mtime content changes; it is not an I/O-free timer.
Unchanged summaries are reused. A known dependency is also freshly read when a
query needs its text. A completely unknown URI created without a watcher is
found by reconciliation, rather than by a new whole-root scan on each query.
The 60-second/300-second convergence gates include this maintenance and scan work.

Only accepted diagnostic publications acknowledge external diagnostic work.
Foreground queries cannot advance that acknowledgement. If an open file receives
only local diagnostics while discovery is incomplete, the catalog remembers that
it still needs external diagnostics and republishes after its scope completes.
An unchanged Ready-to-Ready reconciliation does not republish every open file.
This preserves both missed-watcher recovery and stable idle Quick Fix tokens.

Input events update `Documents` and invalidate index facts in the same catalog
critical section. The adapter does not replay accepted text into a second document
store or maintain a second open-text counter. Configuration buffers and documents
outside registered roots still count toward the shared 128 MiB allowance; removing
a root removes its index membership while keeping editor buffers authoritative.
A refused buffer remains unavailable until full replacement or close/reopen, and
revokes file proofs even if a worker has not yet obtained an index ticket.

Every accepted open, close and watcher input records an observation in the current
scan pass. Sweep permission requires the same registered root, its current
configuration epoch, and a successful complete scanner result. Scanner batches
are admitted before `Done` becomes visible. The queue's bounded overflow replaces
the catalog's second batch backlog and dirty/running refill ledger; there is no
10 ms scan of all files to refill worker slots. Discovery still advances in
bounded timed steps and performs the existing 30-second reconciliation.

The server writes bounded per-root progress records to stderr with `phase: index`
and Discovering, Indexing, Ready, Limited or Failed state. Terminal records include
the pass, file count, reads, bytes, parses and retained-input counters. These logs
are observable test evidence, not a custom LSP request or a promise of complete
results from the shared index alone.

## Budgets and source lifetime

| Retained or transient resource | Admission limit |
| --- | ---: |
| One source file | 5 MiB |
| Authoritative open source text | 128 MiB |
| File metadata / shared-index files | 100,000 |
| Resource symbols / references plus dependencies | 1,000,000 / 2,000,000 |
| Shared-index logical strings | 128 MiB |
| Domain-symbol summaries | 64 MiB and 1,000,000 symbols |
| Domain-symbol construction in one file | 8 MiB, checked before expensive concatenation |
| Ready jobs / active file workers | 512 / 2 |
| Outstanding work URI reservations, including overflow and running work | 100,000 file entries plus 2 retiring worker reservations |
| Current-directory hints per root | 64 |
| Request source allowance, including all open buffers and acquired closed dependencies | 128 MiB |
| Concurrent cold schema compilations | 1 |
| Go runtime soft memory target during an LSP session | 288 MiB, preserving any lower caller limit |

Closed raw source has no catalog LRU. The previous cache read the file before
looking up its cached snapshot, so it retained raw text without avoiding the I/O.
Removing it both simplifies ownership and prevents cumulative workspace input
from remaining alive through snapshots. File summaries clone retained names and
containers rather than keeping substrings backed by whole source strings.
For unchanged content and effective configuration, `Index.Reuse` restores the
already retained pending facts through the current ticket. The catalog keeps no
second previous-facts copy or separate copy-byte estimate. Pending navigation
remains hidden until that ticket is successfully reused or replaced.
The existing bounded optional analysis-facts handoff remains separate; no worker
`Workspace.Documents` or syntax tree is installed as permanent catalog state.
Completed workers drop their parsing context and borrowed workspace/buffer maps
before handing results to the owner. Stored code-action plans copy their edit
strings, so a short replacement does not retain a superseded multi-megabyte
document through a substring.

The existing request snapshot budget reserves every open buffer, including those
outside the selected dependency closure. Closed-file reads check that same
allowance before allocation and charge successful stable input. Open snapshots
are already reserved and are not charged again. Summary-only workspace symbol
search does not materialize those closed source files and can still succeed when
the raw-input allowance is full. Background discovery releases each bounded file
input instead of accumulating an entire workspace of source text.

Warm schema queries use the existing immutable compiled cache directly. A cold
miss takes one cancellable compilation permit and checks the cache again before
constructing a schema. This prevents foreground and background workers from
building duplicate compiler graphs at the same time. Cancelled waiters return
without waiting for the active compiler; failures are not cached. A completed
successful compile is reusable even when its initiating document changes,
because it depends only on immutable embedded schema bytes. That caller still
receives cancellation.

The LSP applies a 288 MiB Go runtime soft target and restores the caller's prior
setting when the session ends. A lower existing limit, including one established
by `GOMEMLIMIT`, is preserved. This target bounds Go's collection headroom; it
does not include C parser allocations or embedded/file mappings, and it is not a
hard RSS promise. The CLI keeps its separate one-shot policy. A known incomplete
selected scope returns no code actions before starting schema work that the edit
safety check would necessarily reject.

These logical budgets are not interchangeable with process RSS. The server still
needs temporary parsing/semantic objects and Go heap headroom. The independent
50,000-resource test therefore measures VmHWM as well as logical counters and
keeps the 512 MiB server-RSS threshold. Exact measurements, binary/script hashes,
and the distinction between local observations and future platform gates are in
[the validation record](incremental-index-validation.md).

## Reproducible checks

The native workflow runs focused Go tests for analysis summaries, catalog
lifecycle and workspace input helpers, plus a Linux race check. The standard
stdio consumers cover mixed ownership, stale watcher locations, overlay/close,
scoped failures, changing candidate sets and summary-driven completion caches.
The persistent consumer requires full first and second passes, exact final
locations, signature invalidation and real missed-watcher recovery. A separately
built overlay injects 15 ms at the real single-file read boundary while retaining
all production deadlines, proving discovery continues past five seconds during
continuous editor changes.

The Linux `index` resource job runs five fresh processes at each of 10,000 and
50,000 WorkflowTemplate files, with 4 KiB padding, four CPUs, 8 GiB and no swap.
It retains every sample and requires complete Ready counts, bounded convergence
and server RSS at or below 512 MiB. This dense-resource workload complements the
mixed-parent fixtures; it is not a claim that every editor, filesystem or complete
proposal workload has already been measured. See [CI acceptance](CI.md) for the
actual executable gates and retained evidence.
