# Lessons learned

## 2026-11-06: Windows native-server cache activation

### What failed, and what is known

A Windows Zed user reported the following pair of errors while starting the
Argo Workflows extension:

```text
Cannot activate verified server: Permission denied (os error 2)
Cannot clean temporary cache servers/.download-*: Permission denied (os error 2)
```

In adapter `0.0.1-alpha.1`, the production installer downloaded the manifest and
EXE into `.download-*`, checked SHA-256, ran `version --json`, and then renamed
the whole directory to its published cache path. The first error originates
from that rename. The second originates from cleanup after the first failure;
it is not a separate YAML or LSP-analysis error. Reaching activation means the
preceding download, checksum and identity checks returned successfully.

The confirmed design weakness was requiring a directory rename **after** a
native program at that path had been opened and executed. Windows handles
without delete sharing can prevent both moving the directory and deleting its
contents. Native execution success does not prove those filesystem operations
will also succeed.

The exact holder of a handle on the user's machine was not observed. Do not
attribute the incident to Defender, a leaked Zed process, or a universal WASI
rename restriction without further evidence. Zed's inspected host waits for
process output and grants read/write access to the extension work directory.
The error crosses a WASI boundary: its numeric `os error 2` must not be
interpreted as Win32 error 2 (file not found). Preserve the full error text and
record the runtime and operating system when diagnosing it.

The affected source is
[`distribution.rs` at the reported baseline](https://github.com/lazysnail1024/zed-argo-workflows/blob/9983ca661b3669f13ae5aa65099bc8ebe6c13c6a/editors/zed/src/distribution.rs).
The root Go language server and its native version pin need not change for
this adapter-only repair.

### Repair and invariants

New installations reserve a unique final directory before downloading:
`servers/v<version>-<platform>.gen2-<nonce>/`. They hash and probe the EXE at
the same path that will later be returned to Zed. The last publication step is
atomic creation of an empty `.complete/` directory inside that generation.
No directory or executable rename is required after probing.

The publication marker is a visibility boundary, not a substitute for trust
checks. A cache reader must require the marker for a new generation and still
check file types, sizes, manifest, binary hash and native identity on every use.
A fully downloaded but unmarked generation is unfinished and must not be probed
or selected, including by a simultaneous offline installer.

The separate `.gen2-` namespace is deliberate. Old fixed-version directories
and old `.gen-` generations were legitimately published without a marker and
remain independently verifiable for offline reuse. Treating every unmarked
new directory as a legacy cache would defeat the publication boundary.

Concurrent installers may each publish a valid immutable generation. Neither
needs to rename, replace or remove the other's result. Previously published
paths remain unchanged even if a later checksum detects corruption or another
process still uses the EXE. Failed attempts may clean only their own unfinished
directory; blocked cleanup must preserve the original error and its
version/platform context. It must not make an incomplete generation eligible
for execution or prevent a later fresh attempt.

### Why the existing checks missed it

The Rust matrix ran on Windows, but its `FakeHost.identity()` returned JSON
without executing the downloaded bytes or retaining native handles. A test
that injected `PermissionDenied` into rename asserted that installation failed
and a later unlocked retry succeeded. That established error handling, not the
required ability to install while a Windows handle forbids moving the EXE.

The release-package and file-install tests checked real artifacts, but did not
execute this installer inside the extension's WASI runtime. Successful native
builds, a WASM compilation and archive extraction cannot fill that gap.

### Required regression evidence

| Boundary | Required observable result |
| --- | --- |
| Stable executable path | The path passed to the identity probe equals the returned path and remains usable |
| Incomplete but correct bytes | An offline competing installer neither probes nor selects a generation without `.complete/` |
| Publication ordering | No marker exists during identity verification; rejected identity or failed marker creation never publishes success |
| Concurrent installation and repair | Every returned path and previous generation remains intact; no winner or in-use EXE is replaced |
| Compatibility and corruption | Independently constructed old layouts work offline; corrupt bytes never reach execution; unfinished/crashed attempts do not block retry |
| Actual Windows sharing rules | A retained native handle makes a real WASI rename/removal control fail, while the repaired production installation and offline reuse succeed |
| Test effectiveness | Removing a publication/hash guard or restoring post-probe movement fails the intended runtime assertion after a passing baseline |

The runtime acceptance uses the production Rust installer compiled for
`wasm32-wasip2`, a pinned Wasmtime runtime, and the actual pinned native release.
Its host bridge performs the native identity command and, on Windows, retains
handles without delete sharing. This establishes the relevant Windows/WASI
filesystem and native-process interaction under a controlled lock. It does
**not** identify the original user's lock holder, reproduce every Zed host
capability policy, or claim a real Zed GUI session.

Keep those acceptance layers separate:

| Check | Establishes | Does not establish |
| --- | --- | --- |
| Native Rust installer tests | Cache protocol, ordering, compatibility and failure assertions with injected host boundaries | Zed host behavior or real EXE locks |
| WASI cache runtime acceptance | Production filesystem code in WASI, real native identity execution, controlled Windows lock behavior | Zed GUI activation or the user's exact lock owner |
| WASM/package and adapter file-install tests | Compiled identity, delivered bytes, filesystem installation and rollback | First native-server startup in Zed |
| Native CLI/stdio LSP tests | Actual server execution and protocol behavior | Editor adapter activation |
| Real Zed acceptance | Startup and editing in the recorded Zed/OS/configuration | Untested editor versions or platforms |

CI must retain the exact source identity, platform/runtime versions, raw
stdout/stderr, control outcomes and nonempty result reports for the runtime
checks. A timeout, compilation failure, missing report, zero matching tests or
an unrelated error is an infrastructure/test failure, not a killed mutant.
Do not mark an incident resolved merely because the same operation succeeds
on an unlocked retry.

### Prevention checklist for installer changes

- Identify the executing artifact, target, runtime and OS, not just the CI
  runner label. Run changed filesystem/process interactions at that boundary.
- Assert that no native executable path changes after it is probed or made
  available to another process.
- Publish completion only after validation; test valid-looking incomplete
  state, not only truncated downloads.
- Test upgrade compatibility using independently assembled legacy caches,
  and test repair while the previous executable is still held open.
- Prove controls are active and assertions detect removed guards. Preserve
  raw failures and reject empty/partial acceptance runs.
- Keep first-start, offline restart, interrupted installation and concurrent
  Zed startup in real-editor acceptance. Record the exact adapter/native
  versions, Zed version and OS with those results.
- Do not prescribe administrator execution, antivirus disabling, broad
  permission changes or wholesale cache deletion as the product repair.
  An independently verified `serverPath` is the narrow temporary workaround.

See [development and test entry points](../DEVELOPMENT.md),
[distribution and recovery](distribution.md), and
[architecture ablations](ablation.md). This document records the incident and
required evidence; the exact-commit CI reports and release assets establish
which checks passed and which version has actually been published.
