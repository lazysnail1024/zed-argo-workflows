# Architecture ablation and acceptance

The initial alpha removes obsolete coupling after exercising the actual native
program, Rust adapter and release archives. These checks are reproducible from
the repository; CI retains successful and failed evidence as workflow artifacts.

## Deletions retained

| Removal | Observable result | Decision |
| --- | --- | --- |
| Every editor file from a clean native source fixture | Production native build, release staging and extracted executable identity succeed | Keep the LSP independent of editors |
| Go sources and native release tools from an adapter fixture | Rust installer tests and portable WASM build succeed | Keep the Zed crate self-contained |
| Native version derived from the adapter's Cargo version | Adapter version can change to `9.9.9-alpha.1` while native pin stays `0.0.1-alpha.1` | Keep the separate native pin |
| Combined candidate/development-bundle pipeline | Native and Zed production packagers independently create verified runtime archives | Delete the superseded candidate and mixed-bundle scripts/workflow |
| Pin actions whose effective version is already `auto` | Default, explicit, inherited and editor-overridden auto produce no action or file change; exact-version pins still pass | Remove the no-op configuration action |

No editor registry, generic version provider or placeholder VS Code/Neovim
implementation was added. The stdio protocol and native identity are the boundary.

## Removals rejected by behavior

| Isolated mutation | Observed regression | Decision |
| --- | --- | --- |
| Keep only the newest schema | Valid historical `podPriority` content is rejected | Retain whole-release alternatives |
| Open every structured object | Invalid new-field types and impossible version combinations are accepted | Retain structural closure, preserving upstream open maps |
| Remove query branch selection | Completion suggests `podResources` in a document containing incompatible `podPriority` | Retain structural branch selection |
| Inline all shared schema definitions | Workflow automatic JSON grows from 918,948 to 15,925,392 bytes; CLI analysis exceeds its unchanged deadline | Retain build-time schema sharing |
| Restore Cargo-version-to-native-version coupling | The independent-pin installer assertions fail | Retain explicit `native-server-version` |
| Remove compiled WASM release identity checking | A stale native pin can be packaged under a new manifest | Retain the small identity section and package guard |
| Remove original dependency notices | The unpacked archive fails the independent license inventory check | Retain original legal texts |

On the local Linux host, the inline Workflow mutant's basic probe reached
900,776 KiB peak RSS, about 21.01 times the baseline.
The mutant returned exit 3 with `analysisComplete: false` and a deadline reason;
the baseline completed. This experiment establishes a resource regression. It
does **not** establish semantic parity for the incomplete mutant, and the
measurements are not a portable performance promise.

The native Linux ablation gate originally accepted a deadline regression only
when **both** valid mutant probes timed out. In PR #9 CI, the expanded-schema
mutant exhausted the unchanged deadline for the historical Workflow, while the
simpler current Workflow completed; despite a 17.33× larger schema and 22.47×
higher measured peak RSS for the latter, the gate incorrectly reported that the
regression was undetected. The gate now accepts either two completed valid
probes or any mixture of completed valid probes and explicitly reported
analysis-deadline exhaustion (with at least one timeout), while retaining the
schema-size and memory-ratio thresholds. Other error codes, diagnostics and
unrelated incomplete results never count as evidence. The classifier is
tested with eight positive and negative cases before running the expensive
ablation. This change affects test evidence classification, not LSP behavior.

Loading all eight automatic resource schemas initially exceeded the existing
128 MiB session gate. WorkflowTemplate and ClusterWorkflowTemplate differed only
in their root description in every supported release. Normalizing that
non-constraint root description for automatic schemas lets the existing hash
cache share the same compiled graph. No runtime cache layer was added. The
eight-kind automatic LSP session then measured 113,520 KiB. This gate does not
bound every explicit-pin workload; the full acceptance run observed a higher
peak across other subprocesses.

## Reproduce

```sh
python3 tests/native_isolation.py --output artifacts/native-isolation
python3 tests/e2e/wf-multi-version-ablation.py /path/to/argo-workflows-ls artifacts/version-ablation
python3 tests/e2e/wf-multi-version-ablation.py --self-test
cd editors/zed
python3 tests/ablate_distribution.py --output ../../artifacts/zed-ablation
```

Before publication, local native release staging passed 32 checks, native archive
acceptance passed 41, and the adapter package suite passed 28 cases. Adapter
isolation/ablation passed five controls. The native matrix and adapter workflows
repeat the relevant checks against the exact release commit. Local isolated-source
fixtures have synthetic commits and are not represented as published production
builds. Real Zed GUI installation is a separate manual check.

The auto-pin correction passes 31 quickfix cases and the related delivery/target
tests. Running those same assertions against the preceding producer detects
three incorrect auto-pin actions, providing a behavioral negative control.
