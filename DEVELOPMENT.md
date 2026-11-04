# Development

The accepted delivery target is 3.6.0. Other catalog versions are experimental, even where individual historical rule families have behavioral coverage. Native identity readiness is distinct from completion of an individual check. See [README](README.md) for installation and use.

## Build


Use Go 1.27.3 with a C compiler and the Rust toolchain pinned in `rust-toolchain.toml`. The native executable uses the checked-in vendor tree; the adapter uses `Cargo.lock`.

```sh
mkdir -p build
CGO_ENABLED=1 GOWORK=off go build -mod=vendor -buildvcs=false -trimpath \
  -o build/argo-workflows-ls ./cmd/argo-workflows-ls
cargo build --locked --offline --release --target wasm32-wasip2
./build/argo-workflows-ls version --json
```

The Rust target and locked packages must already be installed for an offline build. The adapter is written to `target/wasm32-wasip2/release/zed_argo_workflows.wasm`. On Windows, use equivalent environment assignments and an `.exe` suffix.

The toolkit dependency is pinned to the published Go pseudo-version `v0.0.0-20261103033718-ae52b82ec6c9`. Dependency availability does not establish product readiness. Its implementation identity and public API snapshot are fixed in [contracts/toolkit.lock.json](contracts/toolkit.lock.json) and [contracts/toolkit-design-1.md](contracts/toolkit-design-1.md). Independent builds use vendor and require no sibling repository. When explicitly updating dependencies, provide the pinned modules through your Go module cache or proxy, then run `python3 scripts/vendor-native.py` to regenerate vendor and restore the required Tree-sitter C headers and source directories. Third-party licenses remain in vendor.

## Source checkout installation

A plain source checkout has `bundled-server` disabled. Build the native server above, then set an absolute executable path in Zed:

```json
{"lsp":{"argo-workflows-ls":{"settings":{"serverPath":"/absolute/path/to/zed-argo-workflows/build/argo-workflows-ls"}}}}
```

On Windows use an absolute path such as `C:/src/zed-argo-workflows/build/argo-workflows-ls.exe`. Choose this checkout in **Install Dev Extension**. Zed builds the adapter. `settings.serverPath` retains the identity/readiness handshake; generic `binary.path` bypasses it. Invalid explicit overrides never fall back to a bundle. Rebuild the native executable and restart the language server after native changes; reinstall after adapter changes.

## Precompiled development bundles

The packaging tool consumes a prebuilt executable; it never rebuilds native code or accesses a sibling repository. Run it on the target host with Python 3 and the Go toolchain matching the executable (for build metadata and the actual Go license). Supply the exact native source archive and both independently verified SHA-256 values:

```sh
python3 scripts/package-dev.py --binary /path/to/prebuilt-server \
  --sha256 NATIVE_SHA256 --platform linux/amd64 \
  --source-archive /path/to/native-source.tar.gz --source-sha256 SOURCE_SHA256
```

`--output` defaults to `dist`; existing package paths are refused. Supported platform identifiers are `linux/amd64`, `linux/arm64`, `darwin/amd64`, `darwin/arm64`, and `windows/amd64`. Packaging requires a successful native-host `version --json` handshake, the Workflows product, readiness, and exactly the accepted/default 3.6.0 target. It does not establish editor acceptance on that platform.

The output contains a platform-specific development folder and deterministic ZIP. Its copied Cargo manifest enables `bundled-server`; `native/server.bin` and `native/manifest.json` become embedded adapter inputs. The manifest records platform, native size/hash/identity, exact native source archive hash, toolchain provenance, and hashes of packaged files. Notices are copied from the native source archive and matching Go toolchain. The folder contains only adapter sources, snippets, documentation, locks, native payload and provenance/notices; it excludes development history and evidence. Generated native files and `dist` are ignored. This is a local development delivery, not a registry or hosted release.

```sh
python3 tests/e2e/dev-bundle.py /path/to/prebuilt-server /path/to/native-source.tar.gz artifacts/dev-bundle
cargo build --locked --offline --release --target wasm32-wasip2 \
  --manifest-path dist/zed-argo-workflows-dev-linux-amd64/Cargo.toml
```

The process consumer preserves commands, raw output/exit codes, input and package hashes, and assertions for deterministic archives, usable assets/notices, real native identity, and invalid input refusal. Real Zed installation separately verifies no override, fresh-cache startup, diagnostics, cache repair, and coexistence. Bundle cache content must match embedded bytes before the existing identity handshake. Unexpected cache file types fail safely. A failed repair (for example an in-use Windows executable or an interrupted temporary write) requires stopping the server and removing the affected cache entry before retrying.

## Behavioral verification

Use real CLI/LSP consumers with independent inputs and expectations. Preserve raw output, exit codes, source and executable hashes, and failed runs. Run related checks at the end of a coherent change. Do not infer acceptance from compilation alone.

```sh
python3 tests/e2e/wf-delivery.py build/argo-workflows-ls artifacts/delivery
python3 tests/e2e/wf-argument-supply.py build/argo-workflows-ls artifacts/argument-supply-check
python3 tests/e2e/wf-inheritance-family.py build/argo-workflows-ls artifacts/inheritance-3.6 --targets 3.6.0
```

The delivery consumer checks default and explicit targets, complete/error/incomplete exits, warnings-as-errors, experimental scopes, exact edits and configuration transitions. The supply consumer distinguishes argument validity from required-input supply. Historical matrix consumers remain experimental coverage, not a readiness declaration. Real Zed installation and configuration forwarding must be checked separately from native stdio. Use fresh output directories.

## Analysis and edit boundaries


The stdio server supports incremental UTF-16 edits, versioned diagnostics, schema field completion and hover, static domain navigation and name completion, restricted local symbol rename, and limited Quick Fix actions. Fixes cover scalar field names, static template-reference candidates, single-line schema enums, and explicit version pins in opened configuration mappings. Edits require current scope/revision identity, exact source locations, and versioned `documentChanges`; the server does not write buffers to disk. Legal target inheritance does not create a diagnostic.

Schema completion returns exact plain-text edits for keys and finite enum/const values, including empty strings. It excludes existing and schema-forbidden keys, and preserves unrelated fields, comments, and delimiters in single-line and multiline flow collections. Hover shows field types, descriptions, default annotations, the selected target, and its fixed upstream source. Typed maps, local references, and logical schema branches use the compiled validation schema; unresolved branches are labeled as candidates, and defaults do not become enum constraints.

Read-only queries cover template, task, step, workflow parameter, and template input/output parameter/artifact declarations across Workflow, CronWorkflow, WorkflowTemplate, and ClusterWorkflowTemplate. Definitions, references, and document/workspace symbols use exact UTF-16 locations. Task ancestry, preceding Steps groups, lexical `let` bindings, and constant bracket members determine identity. Known external resources, templates, arguments, and producer outputs resolve only within the same root, scope, and namespace (or explicit cluster scope). References combine local and external uses without repeating declarations. Name completion replaces only the active token. Unrelated schema or expression errors do not hide independently proven identities; dynamic or indirect `$env` access, unsupported inline scopes, and encoded member locations are not guessed. Duplicate local declarations remain visible as definition candidates, while ambiguous references and completion are withheld. Definitions also list proven exact candidates for ambiguous external resources, explicit templates, and matching input arguments. Candidate navigation does not bind ambiguous references or enable Rename; encoded or derived declarations still count toward ambiguity without guessed navigation spans.

For the fixed 3.6.0 local analysis contract, bounded YAML aliases and merges contribute resolved ownership, declarations, external calls, and argument values. Explicit keys override merged defaults; earlier mappings in a merge sequence take precedence. Duplicate explicit keys remain invalid. Navigation and edits use independent source origins: derived, encoded, or actually shared anchor declarations count toward ambiguity without receiving invented locations. An unused anchor does not make an otherwise exact declaration shared, and unrelated exact siblings remain available. Field completion and hover use strict projected GVK and effective merge values while retaining exact CST token ranges. Normal new-field completion excludes inherited existing keys; explicit overrides retain value queries. Temporary marker recovery cannot establish ownership for a foreign original GVK field or suppress strict projection refusals. Reference diagnostics traverse projected template containers, arguments, DAG/Steps scopes, outputs, and string values using the existing static-member and Expr tag grammar. Derived or shared diagnostics point to their real source host; ambiguous template or call identities do not select an arbitrary callee for missing-output checks. Read-only lexical bindings use effective dependencies and callee identities for independently exact occurrences, and omit overridden merge values. Derived, encoded and shared variable tokens remain unavailable as locations. This does not provide invented locations for fully alias-expanded containers. For fixed 3.6.0, workflow argument name completion preserves independently known entrypoint and onExit input contexts. Overlapping argument tokens keep all proven roles and do not yield a unique reference identity.

Local Workflow and CronWorkflow aggregate globals require a nonempty parameter list. The `workflow.parameters.json` aggregate alias is available from 3.4.2; a real parameter named `json` retains its independent read-only declaration identity at every locked target. Independent WorkflowTemplate and ClusterWorkflowTemplate validation allows undeclared `workflow.parameters` references from 3.4.2, without inventing declaration locations. This rule family reads effective mapping keys as well as values. Other variable namespaces still require separate effective-key coverage. Aggregate or reserved-name access does not broaden Rename.

The bounded call/parameter family distinguishes argument value validity from callee required-input supply across locked targets. ConfigMap argument admission starts at 3.2.0, forwarding to inputs at 3.2.5, and present name-only arguments clear input defaults from 3.4.10. Standalone template arguments permit empty values from 3.2.4. Restricted argument valueFrom forms follow the 3.4.17 and 3.5.5 branch boundaries; external values are never fetched. The exact absent-optional-output marker is unsupplied from 3.7.16. Before 3.4.6, explicit whole-workflow exits use the caller-local template context and caller globals visit the entry and statically reachable local Steps/DAG children. From 3.4.6 exits use the referenced resource with fallback, and caller globals inspect all base templates. Entry arguments merge whole named objects caller-first; exit inputs retain caller arguments. WF_ARGUMENT_VALUE_INVALID reports workflow argument value/form errors independently of WF_ARGUMENT_MISSING for required callee inputs. For the accepted 3.6.0 target, read-only parameter sources remain independent of value errors. Historical query contexts remain experimental. Exact uses expose real caller-first declaration candidates; shared template uses retain their standalone base origin and proven caller contexts, without making distinct or unresolved identities uniquely referencable. At 3.6.0, explicit onExit navigation follows the referenced template context. These checks do not provide inherited Rename. This covers single-level, complete static resources and compact simple tags; nested whole-resource inheritance, controller behavior, value evaluation and other historical semantic families remain separate limits.

DAG dependency mode checks apply across the whole DAG: nonempty `depends` excludes nonempty `dependencies` and non-null `continueOn`, while empty fields retain their typed meaning. `Omitted` is accepted from 3.3.9; a version diagnostic does not erase an exact task reference. `AnySucceeded` and `AllFailed` require a uniquely identified producer with a nonempty `withItems`, nonempty `withParam`, or non-null `withSequence`. Repeated items-result occurrences keep all exact references and produce one loop-required diagnostic per producer. These rules are checked against every locked target; other historical task and expression semantics remain separate acceptance gates.

Rename covers templates, DAG tasks, steps, template input/output parameters and artifacts, and workflow parameters inside one Workflow or CronWorkflow. It follows resolved local call arguments and static tag/expression references, including constant string indices. Indirect `$env` accesses conservatively block affected namespaces; dynamic or whole-environment access blocks all accessible namespaces. Dynamic accesses that could select the symbol, unknown bindings, aliases, public or cross-file identities, and incomplete scopes refuse edits. A workflow argument that also supplies an entrypoint or onExit input is not renamed. Expression references use the syntax tree of Expr v1.16.9, pinned to the Argo 3.6.0 dependency, without evaluating expressions. Exact source mapping is required; encoded or folded reference text can prevent rename.

Input limits include 5 MiB per YAML file, 1 MiB per scalar or configuration file, syntax depth 256, and 128 MiB of retained snapshot text per analysis across its loaded roots and scopes. Each analysis reserves existing open-buffer text before loading disk snapshots. Open buffers share a server-wide 128 MiB text limit across roots, including opened configuration files. A refused buffer remains unavailable rather than falling back to disk; after freeing space, send a full replacement or close and reopen it. Core analysis has a five-second deadline. Resource exhaustion reports an incomplete result and never makes a truncated scope complete.

Each logical analysis also limits alias/merge documents to 100,000 expanded nodes in aggregate across its loaded roots and scopes. For a document containing an alias or merge, the full resolved tree counts containers, mapping keys, and scalar/null values, including repeated expanded occurrences. Plain-only documents do not consume this graph allowance. Reused projections have the same admission cost as cold projections; edit simulations receive separate before/after allowances. Over-budget workspace symbol queries fail explicitly instead of returning a partial list. The existing per-document preflight also limits raw expanded syntax to 100,000 nodes before decoding; alias references and merge syntax contribute to that separate count. A graph within the resolved-tree allowance can therefore still be refused by preflight, and workspace symbol queries propagate either refusal explicitly. This logical-result limit is not a process RSS guarantee.

The extension ID is `argo-workflows-lsp`; the server ID is `argo-workflows-ls`. The development adapter reads `lsp.argo-workflows-ls.settings.serverPath`, verifies server identity, and starts `serve --stdio`. Zed's standard `binary.path` bypasses adapter checks and cannot verify that path. Keep the existing YAML language server enabled. The adapter refuses a server whose `analysisReady` is false and forwards the current workspace settings to the server.

Resource consumers use the fixed 10,000-file/50 MiB workload, bounded retained-fact growth, and an 800 MiB combined Workflows/Argo CD RSS budget. Each measurement must identify its exact binaries and input; earlier measurements are not automatically attributed to later builds. These budgets are not universal memory bounds for arbitrary workloads.

Local Linux development installation in Zed 1.26.2 has been verified, including hover, applied completion, cross-file definition, external-call Rename refusal, local Rename and undo, and live target settings with experimental refusal and recovery. Editor acceptance on other platforms, native platform CI, and formal distribution remain unfinished. Full historical semantic coverage and unsupported dynamic or nested inheritance behavior remain outside this delivery. No release is published by this repository's build commands.

Real-process verification accepts an explicit native executable, for example:

```sh
python3 tests/e2e/wf-quickfixes.py build/argo-workflows-ls artifacts/local-quickfixes
```

The runner records inputs, independent expected results, protocol output, exit codes, and hashes. These are CLI/LSP checks, not editor acceptance.

