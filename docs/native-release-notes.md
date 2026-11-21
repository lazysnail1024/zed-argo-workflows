# Argo Workflows language server - 0.0.1-alpha.5

This alpha adopts `k8s-crd-toolkit` `v0.0.1-alpha.2` (`toolkit-design-6`) and packages the analysis explanation, unbound resource candidate inspection, saved Helm render profile, local definition recovery, and startup/query performance improvements merged since `0.0.1-alpha.4`, while retaining automatic compatibility across Argo Workflows 3.0.0-4.1.6.

- Upgraded `github.com/lazysnail1024/k8s-crd-toolkit` from `toolkit-design-5` (`v0.0.0-20261112033740-db4adba0093e`) to published `v0.0.1-alpha.2` (`toolkit-design-6`), adopting bounded OS-thread-pinned native parser workers, slice yielding, cancellation at admission, and strict YAML projection/query optimizations.
- Added current-buffer analysis and safe-edit refusal explanations, plus modeled version-candidate explanations with pinned rule provenance.
- Added unbound `WorkflowTemplate` reference-candidate inspection and opening actions using fresh owner-proof checks without changing definition, reference, rename, or quick-fix identity rules.
- Added saved Helm render profiles (`--profile`), verified `render-cache` listing and owned-directory cleanup, and conservative local template definition recovery after incomplete YAML tails.
- Restored current open-document parsing facts before queries, reused compiled-schema unknown-field policy metadata, replaced polling timers with event-driven readiness/debounce wakeups, and rechecked configuration at edit delivery.

Native executables and archives cover Linux amd64/arm64, macOS amd64/arm64, and Windows amd64, with license/source provenance and `SHA256SUMS`. The independent Zed adapter release `zed-v0.0.1-alpha.6` pins native `v0.0.1-alpha.5` in a follow-up adapter release after native assets are published.

Use the Helm extension for Helm syntax; see the README for one-time language associations and known source-navigation limits. Separate visible roots remain separate indexes in Zed 1.28.1; open a common parent for cross-repository navigation. This remains an alpha, not a Zed extension-store publication.
