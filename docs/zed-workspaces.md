# Zed workspaces and multiple repositories

Open a common parent directory when Workflows resources in several repositories
need to resolve within one product scope. The parent is an explicit indexing
boundary. A Git repository below it does not automatically become another
workspace root, configuration boundary or scope.

This page separates the native server's ownership rules from the host's process
and request routing. The current-buffer explanation is the most useful way to
check which rules apply to the file you are editing.

The usage below describes current source. Check `argo-workflows-ls version --json`
when comparing an installed server with a tested commit; the adapter's release
and native pin are independent. See [distribution](distribution.md) for those
installation identities.

## Choose a common parent

For example, open `/work/argo-workspace` in Zed:

| Path relative to that directory | Purpose |
| --- | --- |
| `.argo-workflows-ls.yaml` | Configuration for the parent workspace root |
| `workflows-repo/workflow.yaml` | A Workflow that references a shared template |
| `templates-repo/template.yaml` | The WorkflowTemplate declaration |
| `workflows-repo/.git/` and `templates-repo/.git/` | Independent Git repositories; their contents are not indexed |

Without configured scopes, admitted files use the root's `root` scope. To state
the intended shared inventory explicitly, place this file at the opened parent:

```yaml
schemaVersion: 1
targetVersion: auto
referencePolicy: open
exclude:
  - "**/generated/**"
scopes:
  - name: shared
    include:
      - "workflows-repo/**"
      - "templates-repo/**"
```

The two include patterns belong to the same scope. Placing each repository in a
different scope would isolate their bindings. The resource name, namespace and
kind must still match; directory membership alone does not prove a reference.
Use explicit `metadata.namespace`, or set a reviewed `defaultNamespace` only
when it describes this inventory. `auto` uses the bundled compatibility mode; it does
not discover a cluster's controller version.

Keep `referencePolicy: open` for a partial checkout. Choose `closed` only when the
scope contains the references that must be checked together. Resource Rename
also requires a complete current scope, a unique static binding, a known
namespace for namespaced resources, and every affected file open with a client
version. A Definition result or a visible candidate does not authorize Rename.

For this cross-file operation, select the resource name in `templateRef.name`,
`workflowTemplateRef.name`, or the WorkflowTemplate/ClusterWorkflowTemplate's
`metadata.name`. That is different from the exported template name in
`templateRef.template` or `spec.templates[].name`. Those positions can support
Definition while the local Rename model refuses them; such a refusal alone does
not demonstrate a scope-isolation guard. The dispatch and resource proof are in
[externalrename.go](../internal/analysis/externalrename.go).

See the [configuration reference](configuration.md) for strict keys, target
precedence and render profiles. Opening a broad parent can increase discovery
work; choose the intended parent and use scopes/exclusions to describe its
inventory instead of opening an unrelated ancestor.

## Keep the boundaries explicit

| Boundary | Meaning |
| --- | --- |
| Visible worktree | A folder shown by Zed as a project root. Host policy decides which server instances serve it. |
| Native process session | One `argo-workflows-ls serve --stdio` process and its own input/catalog lifecycle. A second process has separate state. |
| Client-supplied workspace root | A root received through initialization or workspace-folder notifications. The explanation's `roots` lists this session's roots. |
| Product scope | A root-qualified membership and reference boundary. Identical scope names under different owning roots do not merge them. |

The native server supports `workspaceFolders` and folder changes. That capability
does not prove that a particular Zed version shares one process among visible
worktrees or sends those changes to an existing process.

The executable membership rules are in
[workspace configuration](../internal/workspace/config.go),
[disk discovery](../internal/workspace/stream.go) and
[catalog ownership](../internal/lsp/catalog.go):

- The product reads `.argo-workflows-ls.yaml` at each root it owns. Opening the
  common parent does not recursively load a second configuration from every
  nested Git repository. Move the intended policy into the parent configuration,
  or deliberately use separate roots when isolation is intended.
- Scope includes and exclusions are relative to the owning root, with `/` glob
  separators. A file matching two scopes is an error; unmatched files use
  `unassigned` when scopes are configured. Same-spelled resource names in
  separate scopes remain separate bindings.
- Disk discovery considers `.yaml`/`.yml`, skips the product configuration and
  `.git`, `vendor`, `node_modules` and `.argo-ls` directories, and does not follow
  child symlinks. A directory link does not bring another repository into the
  recursively indexed inventory.
- Explicitly opened excluded files can still be analyzed in isolated
  `opened:<URI>` scopes. Opening one does not add the excluded directory to disk
  membership or lend its definitions to the normal workspace scope. Buffer
  admission and disk discovery are separate decisions.
- An open buffer remains an input while the client keeps that document open.
  Deleting its disk file alone does not end the accepted buffer lifetime. If an
  obsolete open declaration still makes a binding ambiguous, close that buffer
  and request a new explanation; inspect the received input rather than assuming
  a filesystem deletion also sent `didClose`.
- When a single native session receives nested roots, the deepest matching root
  owns its files and the parent's disk membership excludes that nested root.
  This rule uses the roots received by that process. A nested folder visible in
  the editor is not sufficient evidence that both roots reached the same
  session.

The server does not search arbitrary ancestors to combine repositories and does
not use a shared background daemon. Root removal and re-addition must be treated
as lifecycle events, even when the path string is unchanged. When a child root
is removed from a session, a retained parent can become the owner again. Removing
a visible row is not itself a filesystem exclusion.

## Explain the current buffer

In an Argo Workflows YAML buffer, run **editor: toggle code actions** and choose
**Argo Workflows: explain current analysis**. At a supported symbol, select its
name so the explanation can also describe reference certainty and Rename
availability. The operation is read-only and only displays a message when
explicitly selected.

The visible message summarizes the buffer version, owning root/scope,
document/scope completeness, reference certainty and operation refusals. The
structured `argo-workflows/explainAnalysis` response carries the full fields:

| Field | What it establishes |
| --- | --- |
| `root` and `roots` | The selected file's owner and the roots actually supplied to this server session |
| `scope` and `targetVersion` | The effective reference boundary and compatibility target |
| `inputIdentity.source`, `version`, `sha256`, `configurationHash` | Which buffer version/bytes and effective configuration were analyzed |
| `phase`, `completeness.document`, `completeness.workspace` | Whether local analysis and the selected scope are complete; one does not imply the other |
| `references.complete` | Whether the reference count is proven; an incomplete zero count does not prove there are no references |
| `reasonCode`, `nextStep`, related locations | The actual guard preventing an operation and the relevant recovery action |

The notification does not display every structured identity field. `roots` is a
session-local fact, not a process identifier; similar paths in two messages do
not establish that one process produced both.

For example, `AFFECTED_FILE_NOT_OPEN` asks you to open the listed files;
`INDEX_INCOMPLETE` asks you to resolve unavailable inputs or wait for that scope;
`TARGET_UNKNOWN` requires a resolvable resource identity and namespace. After a
buffer, configuration or root change, request a new action. An earlier
explanation is not permission to apply an old edit.

The terminal checks saved bytes:

```sh
argo-workflows-ls explain-config \
  --root /work/argo-workspace --file workflows-repo/workflow.yaml
```

Its `analysis.inputIdentity.source` is `disk`; the editor explanation uses
`buffer`. The CLI cannot explain an unsaved overlay. The structured custom LSP
request and full reason contract are documented in
[configuration](configuration.md#cli-commands-and-exit-codes).

Configuration has an input lifetime too. This native server does not request
dynamic watched-file registration. Configuration buffer/save events and received
workspace configuration/file events invalidate metadata; otherwise the background
metadata refresh is throttled to 30 seconds. An early read-only explanation can
therefore still describe the previous saved configuration. Opening the root
configuration makes its editor text an explicit input. An invalid or unavailable
effective configuration is not replaced by cached good state.

Final Rename and resolved Quick Fix edits have a separate
[configuration check at delivery](configuration.md#boundaries). The owner
reacquires the effective configuration before returning the `WorkspaceEdit`,
even when no file event was received. A ready explanation or successful Prepare
Rename is not the final authorization to edit.

## Observed Zed 1.28.1 sessions

The real host was Linux Zed
`1.28.1+stable.390.e8bc16acee5ae1937600d571d441c4b5d7b0d288`.
The table records ten logical flows, some with multiple saved checkpoints, using
actual process snapshots, executable hashes, initialize roots and raw LSP frames.
The two products used their released adapter entries with explicit local native
server paths. The recorded PIDs are identifiers in this test's process namespace.

| Native used in these ten flows | Commit prefix | Executable SHA-256 prefix | Coverage |
| --- | --- | --- | --- |
| Workflows | `0788830d3533` | `a99749a7bce5` | Every row in this table |
| Argo CD before its wording fix | `ec32e5d5285b` | `cee4d6efff9e` | Initial common-parent and `same-name-before-fix` checkpoints |
| Argo CD after its wording fix | `42f251ea5cdb` | `a1e79c4022ba` | From `same-name-after-fix` onward, including common recovery |

The complete Workflows executable hash for this matrix is
`a99749a7bce52b1b63bf797918bf0e1f1a33e808919a791bc31f675468fc2ca0`.
These observations retain that identity; the later edit-configuration guard is
a separate native revision and does not relabel this matrix.

| Flow | Observed Workflows result and limit | Evidence checkpoint suffixes |
| --- | --- | --- |
| Common parent | PID `1705` initialized with `common` only. Definition from `repo-b/workflow.yaml` reached `repo-a/template.yaml`. Workflow-local `main` supported Prepare Rename; the inline editor was canceled with Escape. Explain reported complete `common/root` and one proved reference. | `common-parent` |
| Separately added roots | A used PID `2190`; B used PID `2333`. Each received only its own root and matching cwd. With two Argo CD sessions, four native processes existed. A's open declaration did not resolve B's reference: B returned empty Definition/References and `TARGET_UNKNOWN`, with `references.complete: false`. | `separately-added` |
| Nested folder selected through Add Folder | Selecting `common/repo-b` retained one top-level `common` worktree and WF PID `1966`. No new initialization or workspace-folder event reached it; Explain still reported `common/root`. This did not create overlapping native roots or exercise deepest-root reassignment. | `nested-visible-root` |
| Remove and re-add the same path | Removing B retired PID `2333` while A survived. Re-adding B with its old tabs retained created no new B session or request. Closing and reopening those files started PID `2412`, initialized with B only, and restored fresh explanations. This was process replacement, not a same-process folder epoch transition. | `remove-b-open-tabs`, `readd-retained-tabs`, `remove-readd` |
| Same resource name | At the supported `templateRef.name` position, Prepare Rename and Explain returned `AMBIGUOUS_BINDING` with both resource declaration locations; the reference count remained unproved. Deleting a duplicate from disk still left its open overlay active. After `didClose`, PID `1857` returned a unique definition and successful Prepare Rename. No Rename was applied in these checkpoints. | `same-name-before-fix`, `common-recovery` |
| Excluded declaration still open | Both products' declaration buffers remained open while the common-root config excluded `repo-a/**`. The included Workflow caller reported `WF_TEMPLATE_REF_UNRESOLVED`; Prepare Rename returned `TARGET_UNKNOWN`, and Explain retained `references.complete: false` despite a complete selected scope. Opening excluded declarations did not lend binding authority. | `exclude` |
| Child symlinks to outside declarations | A matching outside WorkflowTemplate was reachable through a child directory link; the Argo CD fixture also used a file link. No alias/outside URI was opened. WF Definition/References were empty and Rename preparation returned `TARGET_UNKNOWN`. This verifies disk discovery, not explicitly opened aliases or separately registered alias roots. | `symlink` |
| Named scope isolation | Configured scopes `a` and `b` separated declaration and caller while both remained open. Definition/References were empty. An earlier explanation action returned `STALE_INPUT`; a new action reported complete scope `b`, `TARGET_UNKNOWN` and unproved references. | `named-scope-isolation` |
| Both YAML server preference orders | Only the preferred server received `prepareRename`; its foreign-resource response did not cause observed fallback. The exact typed-error and `null` results are distinguished below. Successful preparation was canceled before editing. | `routing-wf-first`, `routing-cd-first` |
| Invalid root and surviving root | A's early Explain used its previous configuration; a later background refresh reported unavailable analysis before the config was opened. The explicit config buffer then received `WF_CONFIGURATION_ERROR`, and A's Prepare Rename returned `-32803 / SAFETY_UNPROVEN`. B PID `2412` still published its own correct unresolved-reference diagnostic at version 12. Removing A left B alive, with new version-24 diagnostics. This establishes separate-session isolation, not immediate disk-config event delivery. | `invalid-before-config-open`, `invalid-root-survivor`, `close-root` |

The exported template name `templateRef.template: work` separately returned two
Definition candidates in the duplicate fixture, while its explanation reported
`NO_RENAMABLE_SYMBOL`. That read-only domain was not used as the proof of resource
Rename ambiguity: the table's refusal was captured at `templateRef.name`.

| YAML server order | Argo Workflows resource name | Argo CD project reference |
| --- | --- | --- |
| `argo-workflows-ls`, `argocd-ls` | WF PID `1857` returned the `shared-template` Prepare Rename range. | WF returned `-32602 / UNMODELED_BINDING`; CD received no Prepare Rename request. |
| `argocd-ls`, `argo-workflows-ls` | CD returned `null`; WF PID `1966` received no Prepare Rename request. | CD PID `1965` returned the `shared-project` Prepare Rename range. |

For this host version, set the preferred product first in
`languages.YAML.language_servers` when using F2. The setting is a server
preference, not selection by resource kind. A visible product explanation does
not prove that F2 was routed to the same product. If a removed folder is re-added
with retained tabs and no fresh actions appear, the observed recovery was to
close and reopen those files, then request a new explanation.

No workspace-folder notifications were observed in these checkpoints. Native
folder-change support and the source rules for overlapping roots remain separate
evidence. The old invalid-config checkpoint did not execute Rename. WF sent no
dynamic watch registration, so it cannot be described as Zed dropping an
acknowledged registration. Its later background detection also means opening the
config was not the first time that invalid disk content was detected.

Retained evidence is under `host/evidence/step8-*`. The independent frozen review
is `research/step8-host-review-before-wf-guard.snapshot.json`, SHA-256
`3010650fd670370f3d317d8e2d82f5ce8bc40cc82646dc3fcd23f6d02b3fe6be`.
`research/step8-config-refresh-timing-correction.json` records the corrected
server-frame order. These local evidence references identify this bounded
acceptance run; they are not claims about all later Zed versions.

### Edit configuration follow-up

The later configuration-delivery guard was tested separately on the same host
with commit `6d5d7e34e51cfce22456edb79c46c16b5436e6d1`, executable SHA-256
`d0236902936827bf30fee90c059d02c75a94a5750ac6a7b9066bf522476b028b`.
This was a clean Go 1.27.3 development build with `GOWORK=off`, using the same
adapter entry. WF PID `2799` initialized with the common parent.

After successful Prepare Rename, an external write changed the owning root's
policy from `closed` to `open`. The actual Rename request returned
`-32801 / STALE_INPUT` with no edit result. No WF watch notification, owning-config
`didOpen`, or resource `didChange` had delivered that change; the two saved
resources remained unchanged. This directly checks the final editing boundary,
independently of the old matrix's read-only explanation timing.

For recovery, the original `closed` configuration was restored and explicitly
opened/closed in the UI. A new Prepare Rename and Rename returned two version-0
document edits for `guard-recovered-template`. Zed applied both at version 1;
Definition from the renamed reference in B reached the renamed resource in A.
Undo in the Rename multibuffer restored both original texts at version 2 without
saving the resource files. The old refusal notification remained visible during
part of the successful recovery and was not treated as a new failure. Closing
the test window left no native server process.

The separate checkpoints are
`host/evidence/step8-wf-guard-{refused,applied,undone,window-closed}`. This follow-up
does not add dynamic watch registration or change the process-sharing and F2
routing observations above. The independently reconstructed raw-buffer review is
`research/wf-step8-config-guard-host-independent.json`, SHA-256
`1fecb9751401f0b86d21cfc32ac6371b4c29082f85e56a1760c8b450f2058574`.

## Pinned host/API boundary

The host source reviewed for Zed 1.28.1 is commit
`e8bc16acee5ae1937600d571d441c4b5d7b0d288`. Its
[`reuse_existing_language_server`](https://github.com/zed-industries/zed/blob/e8bc16acee5ae1937600d571d441c4b5d7b0d288/crates/project/src/lsp_store.rs#L3375)
returns without reuse for a visible worktree. That function searches existing
visible-worktree servers for an invisible worktree; it is not a declaration that
separately added visible roots share a process. Source inspection must be paired
with process identities and actual initialize/folder traffic when evaluating a
host session.

The adapter pins
[`zed_extension_api` 0.7.0](https://docs.rs/zed_extension_api/0.7.0/zed_extension_api/trait.Extension.html)
and currently implements command selection and workspace configuration in
[adapter.rs](../editors/zed/src/adapter.rs). The inspected public trait and pinned
[host extension manifest](https://github.com/zed-industries/zed/blob/e8bc16acee5ae1937600d571d441c4b5d7b0d288/crates/extension/src/extension_manifest.rs#L250)
do not expose a visible-root sharing declaration. The host commit's
[Rust API source](https://github.com/zed-industries/zed/blob/e8bc16acee5ae1937600d571d441c4b5d7b0d288/crates/extension_api/src/extension_api.rs)
includes newer API methods; its
[`since_v0.6.0` WIT](https://github.com/zed-industries/zed/blob/e8bc16acee5ae1937600d571d441c4b5d7b0d288/crates/extension_api/wit/since_v0.6.0/extension.wit)
is a compatibility interface. Neither is relabeled as the published 0.7.0 crate.
These findings are bounded to the inspected versions.

Request routing is a separate host decision. The pinned
[`FirstCapable` query path](https://github.com/zed-industries/zed/blob/e8bc16acee5ae1937600d571d441c4b5d7b0d288/crates/project/src/lsp_store.rs#L6250)
selects a capable server in preference order. Sharing a native process would not
itself add resource-aware fallback from one YAML server to another, nor change a
foreign-resource `null` response into permission for a different product to edit
the buffer.

## Minimal upstream proposal

This is a proposed host contract, not an implemented adapter setting:

1. Keep default isolation. Allow explicit host opt-in to share the same
   language-server ID within one project and trust domain only when resolved
   executable, arguments, environment, cwd, initialization options and effective
   host settings are compatible. A session-wide target override cannot silently
   serve roots requiring a different override. Different product IDs remain
   separate even when both handle YAML.
2. Require workspace-folder support and register only roots explicitly selected
   by the user. Preserve each product's root-qualified scope and configuration
   semantics. Process sharing must not imply cross-scope reference or edit
   authority.
3. Deliver add/remove transitions both while the server starts and after it is
   running, including removal from the pending initialization set. On removal,
   detach that owner's watchers, diagnostics and pending results while retaining
   other owners. Re-evaluate overlapping buffers against their remaining owner
   and configuration. Close the removed owner's server-side documents even if
   the UI retains their tabs. On re-addition, attach retained buffers using their
   current text and versions. Shut down when the last owner is gone; re-addition
   needs a fresh membership identity, and a fresh session after final shutdown.
   Simply deleting the visible-worktree reuse guard is insufficient.
4. Validate with actual process/session IDs, initialization roots and folder
   notifications: nested roots, removal/re-addition of the same path, one failed
   root alongside a valid root, incompatible launch/settings, both product IDs,
   and both YAML server preference orders. Check retained-root operation and
   discard of stale removed-root responses separately from rename routing.

The current common-parent workflow remains available without this proposal or
any adapter/runtime change. No claim here applies automatically to later Zed
versions.
