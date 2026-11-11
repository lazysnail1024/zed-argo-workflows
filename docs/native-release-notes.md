# Argo Workflows language server — 0.0.1-alpha.3

This prerelease brings the shared CRD editing and incremental workspace work from main into a distributable native LSP. It remains a prerelease before 0.0.1.

- Common YAML source/value projection and Schema queries now come from the pinned shared CRD toolkit; Argo-specific semantics and verification remain in this server.
- Current-file diagnostics and lightweight read requests stay independent of slower workspace discovery. The bounded queues, per-file budgets and incremental readiness preserve the existing five-second analysis deadline and memory limits.
- Static WorkflowTemplate / ClusterWorkflowTemplate resource names and their templateRef / workflowTemplateRef uses can be renamed together only when scope, namespace, source identity and all affected document versions are provably safe. Unsafe or incomplete cases are refused.
- Large trailing-comment analysis is cheaper without weakening strict YAML checks or the 128 MiB input accounting.

Zero-configuration exact-version coverage remains **Argo Workflows 3.0.0–4.1.6**. This is static analysis, not controller execution or automatic inference of the cluster version.

Native executables and archives cover Linux amd64/arm64, macOS amd64/arm64 and Windows amd64. Distribution supplies original license/source provenance and `SHA256SUMS`. The independently versioned Zed adapter `zed-v0.0.1-alpha.4` pins native `v0.0.1-alpha.3`; separately installed native binaries must be upgraded separately.

Automated CLI/LSP, large-workspace, resource and distribution checks do not constitute real Zed GUI acceptance or Zed extension-store publication.
