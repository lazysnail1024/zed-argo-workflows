# Vendored dependency patches

## JSON Schema compilation queue

The repository vendors `github.com/santhosh-tekuri/jsonschema/v6` at **v6.0.3**,
as pinned by `go.mod`, `go.sum`, and `vendor/modules.txt`. The upstream source is
[`compiler.go` at v6.0.3](https://github.com/santhosh-tekuri/jsonschema/blob/v6.0.3/compiler.go).
Its existing Apache-2.0 license and notices remain in the vendored directory.
This local patch changes one file and does not upgrade the dependency:

`vendor/github.com/santhosh-tekuri/jsonschema/v6/compiler.go`

The compiler's pending-schema queue originally searched its entire slice for
every reference enqueue. Large Workflows schemas incur repeated linear scans
during cold compilation, contributing to the existing 10,000-file CLI check
exceeding its unchanged five-second deadline. The patch adds a `map[urlPtr]*Schema`
for pending-schema lookup while keeping an ordered slice for compilation and
final cache insertion. Lookup becomes independent of the number of pending
schemas on average; the queue retains the same schema pointers and order.

`Compiler.enqueue` remains the sole caller of `queue.append`: it checks both the
compiled cache and the pending queue before allocating a schema. Consequently,
each `urlPtr` enters the queue once, and the index has the same duplicate and
reference-resolution behavior as the former linear search. Compilation still
uses the ordered slice, so map iteration does not affect results or error order.
The patch adds no shared state or concurrent compiler operations.

### Regenerating vendor

`go mod vendor` replaces this file with the upstream v6.0.3 implementation and
removes the local patch. When refreshing vendored sources, preserve or reapply
the ordered-slice-plus-index change and inspect the resulting diff. If changing
the dependency version, first check whether upstream already addresses the
lookup cost, then review queue identity and ordering before carrying the patch
forward. Do not silently lose it or reapply it to a changed queue algorithm.

Validate the resulting source with `go vet ./...` and the existing CI suites.
In particular, `wf-bounded-full.py` must still analyze all 10,000 files within the
original five-second limit, with the exact mixed-version target and completion
semantics; schema, reference, edit, and mutation gates must also pass. A local
profile or affinity-constrained timing run does not replace the workflow's
enforced resource tests or real Zed editor acceptance.
