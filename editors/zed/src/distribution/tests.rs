use super::*;
use serde_json::{json, Value};

#[cfg(target_os = "wasi")]
mod runtime;

const VERSION: &str = "0.0.1-alpha.1";

#[test]
fn default_native_release_is_pinned_separately_from_the_adapter_version() {
    assert_eq!(server_version(), VERSION);
    let cache = Cache::new();
    let mut host = FakeHost::new(VERSION);
    let path = resolve(&cache.root(), Some("linux-amd64"), false, None, &mut host).unwrap();
    assert_eq!(fs::read(path).unwrap(), host.bytes);
    assert!(host.events.iter().any(
        |event| event.ends_with("/v0.0.1-alpha.1/argo-workflows-ls-0.0.1-alpha.1-linux-amd64")
    ));
}

#[test]
fn release_pins_are_canonical_stable_or_prerelease_versions() {
    for version in ["0.0.1", "0.0.1-alpha.1", "1.2.3-rc.0", "2.0.0-beta-test.2"] {
        let cache = Cache::new();
        let mut host = FakeHost::new(version);
        let path = install(&cache.root(), "linux-amd64", version, &mut host).unwrap();
        assert_eq!(fs::read(path).unwrap(), host.bytes);
        assert!(host.events[0].ends_with(&format!("/v{version}/SHA256SUMS")));
    }
    for invalid in [
        "../0.1.0",
        "0.1.0/other",
        "latest",
        "v0.1.0",
        "0.1",
        "00.1.0",
        "0.1.0-alpha..1",
        "0.1.0-alpha.01",
        "0.1.0-",
        "0.1.0+build.1",
        "0.1.0-alpha/1",
        "0.1.0\n",
    ] {
        let cache = Cache::new();
        let mut host = FakeHost::new(invalid);
        assert!(install(&cache.root(), "linux-amd64", invalid, &mut host).is_err());
        assert!(host.events.is_empty());
        assert!(!cache.root().exists());
    }
}

struct Cache(PathBuf);
impl Cache {
    fn new() -> Self {
        let path =
            std::env::temp_dir().join(format!("workflows-installer-test-{}", nonce().unwrap()));
        fs::create_dir(&path).unwrap();
        Self(path)
    }
    fn root(&self) -> PathBuf {
        self.0.join("servers")
    }
    fn entries(&self) -> usize {
        fs::read_dir(self.root()).unwrap().count()
    }
}
impl Drop for Cache {
    fn drop(&mut self) {
        let _ = fs::remove_dir_all(&self.0);
    }
}

fn release_identity(version: &str) -> Value {
    json!({"product":"argo-workflows", "serverVersion":version, "adapterContractVersion":1,
        "analysisReady":true, "configSchemaVersions":[1], "reportSchemaVersions":[1],
        "toolkitVersion":"0.2.3", "catalogHash":"fixture", "sourceCommit":"a".repeat(40),
        "dirty":false, "buildMode":"release"})
}
struct FakeHost {
    bytes: Vec<u8>,
    sums: Option<Vec<u8>>,
    identity: Value,
    failure: Option<&'static str>,
    download_error: Option<(String, String)>,
    events: Vec<String>,
    installed: Option<String>,
    lookups: usize,
}
impl FakeHost {
    fn new(version: &str) -> Self {
        Self {
            bytes: b"test executable bytes".to_vec(),
            sums: None,
            identity: release_identity(version),
            failure: None,
            download_error: None,
            events: vec![],
            installed: None,
            lookups: 0,
        }
    }
    fn network_calls(&self) -> usize {
        self.events
            .iter()
            .filter(|event| event.starts_with("fetch:"))
            .count()
    }
    fn probes(&self) -> usize {
        self.events
            .iter()
            .filter(|event| event.starts_with("probe:"))
            .count()
    }
    fn executable_calls(&self) -> usize {
        self.events
            .iter()
            .filter(|event| event.starts_with("chmod:"))
            .count()
    }
}
impl Host for FakeHost {
    fn which(&mut self) -> Option<String> {
        self.lookups += 1;
        self.installed.clone()
    }
    fn fetch(&mut self, url: &str, destination: &Path, _limit: u64) -> Result<(), String> {
        self.events.push(format!("fetch:{url}"));
        if let Some((failed_url, message)) = &self.download_error {
            if url == failed_url {
                return Err(message.clone());
            }
        }
        if self.failure == Some("offline") {
            return Err("network unavailable".into());
        }
        if url.ends_with("/SHA256SUMS") {
            let sums = self.sums.clone().unwrap_or_else(|| {
                [
                    "linux-amd64",
                    "linux-arm64",
                    "darwin-amd64",
                    "darwin-arm64",
                    "windows-amd64",
                ]
                .iter()
                .map(|platform| {
                    format!(
                        "{:x}  {}\n",
                        Sha256::digest(&self.bytes),
                        asset_name(self.identity["serverVersion"].as_str().unwrap(), platform)
                            .unwrap()
                    )
                })
                .collect::<String>()
                .into_bytes()
            });
            fs::write(destination, sums).unwrap();
        } else {
            fs::write(destination, &self.bytes).unwrap();
            if self.failure == Some("oversized") {
                fs::OpenOptions::new()
                    .write(true)
                    .open(destination)
                    .unwrap()
                    .set_len(MAX_BINARY + 1)
                    .unwrap();
            }
            if self.failure == Some("interrupted") {
                return Err("connection interrupted after partial file".into());
            }
        }
        Ok(())
    }
    fn make_executable(&mut self, path: &Path) -> Result<(), String> {
        self.events.push(format!("chmod:{}", path.display()));
        if self.failure == Some("permissions") {
            return Err("permission denied".into());
        }
        Ok(())
    }
    fn identity(&mut self, path: &Path) -> Result<Vec<u8>, String> {
        self.events.push(format!("probe:{}", path.display()));
        if self.failure == Some("identity") {
            return Err("execution failed".into());
        }
        Ok(serde_json::to_vec(&self.identity).unwrap())
    }
}

#[test]
fn install_verifies_before_probe_and_reuses_cache_without_network() {
    let cache = Cache::new();
    let mut host = FakeHost::new(VERSION);
    let path = install(&cache.root(), "linux-amd64", VERSION, &mut host).unwrap();
    assert_eq!(fs::read(&path).unwrap(), host.bytes);
    assert_eq!(host.network_calls(), 2);
    assert!(host.events[0].ends_with("/v0.0.1-alpha.1/SHA256SUMS"));
    assert!(host.events[1].ends_with("/v0.0.1-alpha.1/argo-workflows-ls-0.0.1-alpha.1-linux-amd64"));
    assert!(host.events[2].starts_with("chmod:"));
    assert!(host.events[3].starts_with("probe:"));
    host.failure = Some("offline");
    host.events.clear();
    assert_eq!(
        install(&cache.root(), "linux-amd64", VERSION, &mut host).unwrap(),
        path
    );
    assert_eq!(host.network_calls(), 0);
    assert_eq!(host.probes(), 1);
    assert_eq!(cache.entries(), 1);
}

#[test]
fn installation_keeps_the_identity_probe_path() {
    let cache = Cache::new();
    let mut host = FakeHost::new(VERSION);
    let selected = install(&cache.root(), "windows-amd64", VERSION, &mut host).unwrap();
    assert_eq!(
        host.events.last(),
        Some(&format!("probe:{}", selected.display())),
        "Do not move a directory after executing its server: Windows may retain a non-delete-sharing handle"
    );
    assert_eq!(fs::read(selected).unwrap(), host.bytes);
}

struct ProbeHost<F> {
    inner: FakeHost,
    after_probe: F,
}
impl<F: FnMut(&Path) -> Result<(), String>> Host for ProbeHost<F> {
    fn fetch(&mut self, url: &str, destination: &Path, limit: u64) -> Result<(), String> {
        self.inner.fetch(url, destination, limit)
    }
    fn make_executable(&mut self, path: &Path) -> Result<(), String> {
        self.inner.make_executable(path)
    }
    fn identity(&mut self, path: &Path) -> Result<Vec<u8>, String> {
        let identity = self.inner.identity(path)?;
        (self.after_probe)(path)?;
        Ok(identity)
    }
}

#[test]
fn complete_bytes_are_invisible_until_the_identity_probe_finishes() {
    let cache = Cache::new();
    let mut host = ProbeHost {
        inner: FakeHost::new(VERSION),
        after_probe: |path: &Path| {
            assert!(
                !path.parent().unwrap().join(COMPLETE).exists(),
                "Publish completion only after the native identity has passed"
            );
            // A second instance arrives after all bytes have been downloaded,
            // but while A's native probe is still in progress. Hashes alone
            // must not make this generation eligible for cached execution.
            let mut observer = FakeHost::new(VERSION);
            observer.failure = Some("offline");
            let error = install(&cache.root(), "linux-amd64", VERSION, &mut observer)
                .expect_err("An unfinished generation must not be selected");
            assert!(error.contains("network unavailable"));
            assert_eq!(observer.probes(), 0);
            assert_eq!(observer.executable_calls(), 0);
            Ok(())
        },
    };
    let selected = install(&cache.root(), "linux-amd64", VERSION, &mut host).unwrap();
    assert!(selected.parent().unwrap().join(COMPLETE).is_dir());
    assert_eq!(cache.entries(), 1);
    let mut offline = FakeHost::new(VERSION);
    offline.failure = Some("offline");
    assert_eq!(
        install(&cache.root(), "linux-amd64", VERSION, &mut offline).unwrap(),
        selected
    );
    assert_eq!(offline.network_calls(), 0);
    assert_eq!(offline.probes(), 1);
}

#[test]
fn overlapping_installs_keep_both_paths_and_support_offline_reuse() {
    for repair in [false, true] {
        let cache = Cache::new();
        let mut original = FakeHost::new(VERSION);
        let corrupt = if repair {
            let path = install(&cache.root(), "linux-amd64", VERSION, &mut original).unwrap();
            fs::write(&path, b"corrupted published server").unwrap();
            Some(path)
        } else {
            None
        };
        let mut winner = None;
        let mut host = ProbeHost {
            inner: FakeHost::new(VERSION),
            after_probe: |path: &Path| {
                assert!(!path.parent().unwrap().join(COMPLETE).exists());
                let other = install(
                    &cache.root(),
                    "linux-amd64",
                    VERSION,
                    &mut FakeHost::new(VERSION),
                )
                .unwrap();
                fs::write(
                    other.parent().unwrap().join("winner-sentinel"),
                    b"still in use",
                )
                .unwrap();
                winner = Some(other);
                Ok(())
            },
        };
        let selected = install(&cache.root(), "linux-amd64", VERSION, &mut host).unwrap();
        drop(host);
        let winner = winner.unwrap();
        assert_ne!(selected, winner);
        for path in [&selected, &winner] {
            assert_eq!(fs::read(path).unwrap(), original.bytes);
            assert!(path.parent().unwrap().join(COMPLETE).is_dir());
        }
        assert_eq!(
            fs::read(winner.parent().unwrap().join("winner-sentinel")).unwrap(),
            b"still in use"
        );
        if let Some(corrupt) = &corrupt {
            assert_eq!(fs::read(corrupt).unwrap(), b"corrupted published server");
        }
        assert_eq!(cache.entries(), if repair { 3 } else { 2 });

        let mut offline = FakeHost::new(VERSION);
        offline.failure = Some("offline");
        let reused = install(&cache.root(), "linux-amd64", VERSION, &mut offline).unwrap();
        assert!(reused == selected || reused == winner);
        assert_eq!(offline.network_calls(), 0);
        assert_eq!(offline.probes(), 1);
    }
}

fn write_cache_fixture(folder: &Path, platform: &str) -> PathBuf {
    fs::create_dir(folder).unwrap();
    let asset = asset_name(VERSION, platform).unwrap();
    let bytes = FakeHost::new(VERSION).bytes;
    fs::write(
        folder.join("SHA256SUMS"),
        format!("{:x}  {asset}\n", Sha256::digest(&bytes)),
    )
    .unwrap();
    let binary = folder.join(asset);
    fs::write(&binary, bytes).unwrap();
    binary
}

#[test]
fn legacy_fixed_and_generation_caches_are_reused_offline_without_markers() {
    for suffix in ["", ".gen-old-publisher"] {
        let cache = Cache::new();
        directory(&cache.root()).unwrap();
        // Construct the historical on-disk format independently of install().
        let folder = cache
            .root()
            .join(format!("v{VERSION}-windows-amd64{suffix}"));
        let binary = write_cache_fixture(&folder, "windows-amd64");
        let mut host = FakeHost::new(VERSION);
        host.failure = Some("offline");
        assert_eq!(
            install(&cache.root(), "windows-amd64", VERSION, &mut host).unwrap(),
            binary
        );
        assert_eq!(host.network_calls(), 0);
        assert_eq!(host.probes(), 1);
        assert!(!folder.join(COMPLETE).exists());
        assert_eq!(cache.entries(), 1);
    }
}

#[test]
fn unmarked_new_generation_is_not_treated_as_a_legacy_cache() {
    let cache = Cache::new();
    directory(&cache.root()).unwrap();
    let folder = cache
        .root()
        .join(format!("v{VERSION}-linux-amd64.gen2-crashed"));
    let binary = write_cache_fixture(&folder, "linux-amd64");
    let mut host = FakeHost::new(VERSION);
    host.failure = Some("offline");
    assert!(install(&cache.root(), "linux-amd64", VERSION, &mut host).is_err());
    assert_eq!(host.probes(), 0);
    assert_eq!(host.executable_calls(), 0);
    assert_eq!(fs::read(&binary).unwrap(), host.bytes);
    assert!(!folder.join(COMPLETE).exists());
    assert_eq!(cache.entries(), 1);

    // Only the atomic marker changes eligibility; it never replaces validation.
    fs::create_dir(folder.join(COMPLETE)).unwrap();
    host.events.clear();
    assert_eq!(
        install(&cache.root(), "linux-amd64", VERSION, &mut host).unwrap(),
        binary
    );
    assert_eq!(host.network_calls(), 0);
    assert_eq!(host.probes(), 1);
}

#[test]
fn generation_collisions_never_write_or_remove_an_existing_entry() {
    let cache = Cache::new();
    directory(&cache.root()).unwrap();
    let slot = format!("v{VERSION}-linux-amd64");
    let complete = cache.root().join(format!("{slot}.gen2-0"));
    let binary = write_cache_fixture(&complete, "linux-amd64");
    fs::create_dir(complete.join(COMPLETE)).unwrap();
    let incomplete = cache.root().join(format!("{slot}.gen2-1"));
    fs::create_dir(&incomplete).unwrap();
    fs::write(incomplete.join("keep"), b"another installer").unwrap();
    let file = cache.root().join(format!("{slot}.gen2-2"));
    fs::write(&file, b"unexpected file").unwrap();
    let mut attempts = 0;
    let selected = generation(&cache.root(), &slot, || {
        let value = attempts.to_string();
        attempts += 1;
        Ok(value)
    })
    .unwrap();
    assert_eq!(attempts, 4);
    assert_eq!(selected, cache.root().join(format!("{slot}.gen2-3")));
    assert_eq!(fs::read(binary).unwrap(), FakeHost::new(VERSION).bytes);
    assert_eq!(
        fs::read(incomplete.join("keep")).unwrap(),
        b"another installer"
    );
    assert_eq!(fs::read(file).unwrap(), b"unexpected file");

    let mut attempts = 0;
    assert!(generation(&cache.root(), &slot, || {
        attempts += 1;
        Ok("0".into())
    })
    .unwrap_err()
    .contains("repeated collisions"));
    assert_eq!(attempts, 8);
    assert_eq!(cache.entries(), 4);
}

#[test]
fn completion_marker_failure_is_visible_and_retryable() {
    let cache = Cache::new();
    let mut host = ProbeHost {
        inner: FakeHost::new(VERSION),
        after_probe: |path: &Path| {
            fs::write(path.parent().unwrap().join(COMPLETE), b"obstruct marker").unwrap();
            Ok(())
        },
    };
    let error = install(&cache.root(), "windows-amd64", VERSION, &mut host).unwrap_err();
    assert!(error.contains("Cannot publish verified server cache"));
    assert!(error.contains(&format!("v{VERSION} for windows-amd64")));
    assert!(error.contains("settings.serverPath"));
    assert_eq!(cache.entries(), 0);
    let selected = install(
        &cache.root(),
        "windows-amd64",
        VERSION,
        &mut FakeHost::new(VERSION),
    )
    .unwrap();
    assert!(selected.parent().unwrap().join(COMPLETE).is_dir());
}

#[test]
fn cleanup_failure_preserves_primary_error_and_installation_context() {
    let cache = Cache::new();
    let mut replaced = None;
    let mut host = ProbeHost {
        inner: FakeHost::new(VERSION),
        after_probe: |path: &Path| {
            let folder = path.parent().unwrap();
            // Deterministic type/ownership obstruction on every host. The
            // separate WASI/Windows E2E exercises real sharing violations.
            fs::remove_dir_all(folder).unwrap();
            fs::write(folder, b"unexpected replacement").unwrap();
            replaced = Some(folder.to_path_buf());
            Err("native identity probe denied".into())
        },
    };
    let error = install(&cache.root(), "windows-amd64", VERSION, &mut host).unwrap_err();
    drop(host);
    for detail in [
        "native identity probe denied",
        "Refusing to remove non-directory",
        "incomplete cache cleanup deferred",
        "windows-amd64",
        VERSION,
        "settings.serverPath",
    ] {
        assert!(error.contains(detail), "missing {detail}: {error}");
    }
    let replaced = replaced.unwrap();
    assert_eq!(fs::read(&replaced).unwrap(), b"unexpected replacement");
    fs::remove_file(replaced).unwrap();
    assert!(install(
        &cache.root(),
        "windows-amd64",
        VERSION,
        &mut FakeHost::new(VERSION)
    )
    .is_ok());
}

#[test]
fn completion_marker_must_be_a_directory_and_is_not_a_trust_bypass() {
    let cache = Cache::new();
    let mut host = FakeHost::new(VERSION);
    let selected = install(&cache.root(), "linux-amd64", VERSION, &mut host).unwrap();
    let marker = selected.parent().unwrap().join(COMPLETE);
    fs::remove_dir(&marker).unwrap();
    fs::write(marker, b"not an atomic completion marker").unwrap();
    host.events.clear();
    assert!(install(&cache.root(), "linux-amd64", VERSION, &mut host)
        .unwrap_err()
        .contains("regular directory"));
    assert_eq!(host.probes(), 0);
    assert_eq!(host.network_calls(), 0);
}

#[test]
fn shared_root_creation_accepts_only_a_directory() {
    let cache = Cache::new();
    directory(&cache.root()).unwrap();
    let sentinel = cache.root().join("keep");
    fs::write(&sentinel, b"shared root").unwrap();
    directory(&cache.root()).unwrap();
    assert_eq!(fs::read(sentinel).unwrap(), b"shared root");

    for directory_wins in [true, false] {
        let cache = Cache::new();
        // The actual mkdir call observes AlreadyExists when another installer
        // wins. No callback or check-then-create window is needed to model it.
        if directory_wins {
            fs::create_dir(cache.root()).unwrap();
        } else {
            fs::write(cache.root(), b"unexpected file").unwrap();
        }
        let result = directory(&cache.root());
        assert_eq!(result.is_ok(), directory_wins);
        if !directory_wins {
            assert!(result.unwrap_err().contains("regular directory"));
            assert_eq!(fs::read(cache.root()).unwrap(), b"unexpected file");
        }
    }
}

#[test]
fn corrupted_binary_or_manifest_is_repaired_before_execution() {
    for corrupt_manifest in [false, true] {
        let cache = Cache::new();
        let mut host = FakeHost::new(VERSION);
        let path = install(&cache.root(), "linux-amd64", VERSION, &mut host).unwrap();
        fs::write(
            if corrupt_manifest {
                path.parent().unwrap().join("SHA256SUMS")
            } else {
                path.clone()
            },
            b"broken",
        )
        .unwrap();
        host.events.clear();
        let repaired = install(&cache.root(), "linux-amd64", VERSION, &mut host).unwrap();
        assert_ne!(repaired, path);
        assert_eq!(host.network_calls(), 2);
        assert_eq!(host.probes(), 1);
        assert_eq!(fs::read(repaired).unwrap(), host.bytes);
        assert_eq!(
            fs::read(if corrupt_manifest {
                path.parent().unwrap().join("SHA256SUMS")
            } else {
                path
            })
            .unwrap(),
            b"broken"
        );
        assert_eq!(cache.entries(), 2);
    }
}

#[test]
fn corrupted_cache_offline_never_executes_and_retry_recovers() {
    let cache = Cache::new();
    let mut host = FakeHost::new(VERSION);
    let path = install(&cache.root(), "linux-amd64", VERSION, &mut host).unwrap();
    fs::write(&path, b"damaged").unwrap();
    host.events.clear();
    host.failure = Some("offline");
    assert!(install(&cache.root(), "linux-amd64", VERSION, &mut host)
        .unwrap_err()
        .contains("network unavailable"));
    assert_eq!(host.probes(), 0);
    assert_eq!(host.executable_calls(), 0);
    host.failure = None;
    let repaired = install(&cache.root(), "linux-amd64", VERSION, &mut host).unwrap();
    assert_ne!(repaired, path);
    assert_eq!(fs::read(repaired).unwrap(), host.bytes);
    assert_eq!(fs::read(path).unwrap(), b"damaged");
}

#[test]
fn digest_mismatch_never_chmods_or_probes_download() {
    let cache = Cache::new();
    let mut host = FakeHost::new(VERSION);
    host.sums = Some(
        format!(
            "{}  {}\n",
            "0".repeat(64),
            asset_name(VERSION, "linux-amd64").unwrap()
        )
        .into_bytes(),
    );
    assert!(install(&cache.root(), "linux-amd64", VERSION, &mut host)
        .unwrap_err()
        .contains("SHA-256 mismatch"));
    assert_eq!(host.probes(), 0);
    assert_eq!(host.executable_calls(), 0);
    assert_eq!(cache.entries(), 0);
}

#[test]
fn invalid_checksum_manifests_never_download_binary() {
    let asset = asset_name(VERSION, "linux-amd64").unwrap();
    let line = format!("{}  {asset}\n", "a".repeat(64));
    for invalid in [
        vec![],
        b"truncated".to_vec(),
        b"\xff".to_vec(),
        format!("{line}{line}").into_bytes(),
        format!("{}  other-server\n", "a".repeat(64)).into_bytes(),
        format!("{}  ../{asset}\n", "a".repeat(64)).into_bytes(),
        format!("{} *{asset}\n", "a".repeat(64)).into_bytes(),
        vec![b'a'; MAX_CHECKSUMS as usize + 1],
    ] {
        let cache = Cache::new();
        let mut host = FakeHost::new(VERSION);
        host.sums = Some(invalid);
        assert!(install(&cache.root(), "linux-amd64", VERSION, &mut host).is_err());
        assert_eq!(host.network_calls(), 1);
        assert_eq!(host.probes(), 0);
        assert_eq!(cache.entries(), 0);
    }
}

#[test]
fn download_failures_identify_the_exact_stage_and_preserve_offline_versions() {
    for message in [
        "HTTP 403 Forbidden",
        "HTTP 404 Not Found",
        "request timed out",
    ] {
        for (asset, phase) in [
            ("SHA256SUMS".to_owned(), "checksum manifest"),
            (asset_name("0.2.0", "linux-amd64").unwrap(), "native server"),
        ] {
            let cache = Cache::new();
            let mut old = FakeHost::new(VERSION);
            let old_path = install(&cache.root(), "linux-amd64", VERSION, &mut old).unwrap();
            let old_bytes = fs::read(&old_path).unwrap();
            let url = format!("{REPOSITORY}/releases/download/v0.2.0/{asset}");
            let mut next = FakeHost::new("0.2.0");
            next.download_error = Some((url.clone(), message.to_owned()));
            let error = install(&cache.root(), "linux-amd64", "0.2.0", &mut next).unwrap_err();
            for detail in [
                phase,
                &url,
                message,
                "v0.2.0",
                "linux-amd64",
                "settings.serverPath",
            ] {
                assert!(error.contains(detail), "missing {detail:?}: {error}");
            }
            assert_eq!(next.events.last(), Some(&format!("fetch:{url}")));
            assert_eq!(next.executable_calls(), 0);
            assert_eq!(next.probes(), 0);
            assert_eq!(cache.entries(), 1);
            assert_eq!(fs::read(&old_path).unwrap(), old_bytes);
            old.failure = Some("offline");
            old.events.clear();
            assert_eq!(
                install(&cache.root(), "linux-amd64", VERSION, &mut old).unwrap(),
                old_path
            );
            assert_eq!(old.network_calls(), 0);
            next.download_error = None;
            let new_path = install(&cache.root(), "linux-amd64", "0.2.0", &mut next).unwrap();
            assert_ne!(new_path, old_path);
            assert_eq!(fs::read(new_path).unwrap(), next.bytes);
            assert_eq!(fs::read(old_path).unwrap(), old_bytes);
            assert_eq!(cache.entries(), 2);
        }
    }
}

#[test]
fn interrupted_fetch_chmod_or_probe_failure_leaves_retryable_cache() {
    for failure in ["interrupted", "permissions", "identity"] {
        let cache = Cache::new();
        let mut host = FakeHost::new(VERSION);
        host.failure = Some(failure);
        let error = install(&cache.root(), "linux-amd64", VERSION, &mut host).unwrap_err();
        assert_eq!(cache.entries(), 0);
        if failure == "interrupted" {
            assert!(error.contains(&format!(
                "{REPOSITORY}/releases/download/v{VERSION}/{}",
                asset_name(VERSION, "linux-amd64").unwrap()
            )));
            assert!(error.contains("connection interrupted after partial file"));
            assert_eq!(host.executable_calls(), 0);
            assert_eq!(host.probes(), 0);
        }
        if failure == "permissions" {
            assert_eq!(host.probes(), 0);
        }
        host.failure = None;
        assert!(install(&cache.root(), "linux-amd64", VERSION, &mut host)
            .unwrap()
            .is_file());
        assert_eq!(cache.entries(), 1);
    }
}

#[test]
fn release_identity_rejects_incompatible_and_unstamped_binaries() {
    for (field, invalid) in [
        ("product", json!("argocd")),
        ("adapterContractVersion", json!(2)),
        ("analysisReady", json!(false)),
        ("configSchemaVersions", json!([2])),
        ("reportSchemaVersions", json!([])),
        ("sourceCommit", json!(null)),
        ("dirty", json!(true)),
        ("buildMode", json!("development")),
    ] {
        let cache = Cache::new();
        let mut host = FakeHost::new(VERSION);
        host.identity[field] = invalid;
        assert!(
            install(&cache.root(), "linux-amd64", VERSION, &mut host).is_err(),
            "{field}"
        );
        assert_eq!(cache.entries(), 0);
    }
    let cache = Cache::new();
    let mut host = FakeHost::new(VERSION);
    host.sums = Some(
        format!(
            "{:x}  {}\n",
            Sha256::digest(&host.bytes),
            asset_name("0.2.0", "linux-amd64").unwrap()
        )
        .into_bytes(),
    );
    assert!(install(&cache.root(), "linux-amd64", "0.2.0", &mut host)
        .unwrap_err()
        .contains("clean release build of 0.2.0"));
}

#[test]
fn upgrades_are_isolated_and_never_silently_fall_back() {
    let cache = Cache::new();
    let mut host = FakeHost::new("0.1.0");
    let old = install(&cache.root(), "linux-amd64", "0.1.0", &mut host).unwrap();
    host.identity = release_identity("0.2.0");
    host.failure = Some("offline");
    host.events.clear();
    assert!(install(&cache.root(), "linux-amd64", "0.2.0", &mut host).is_err());
    assert!(old.is_file());
    assert_eq!(host.probes(), 0);
    host.failure = None;
    let new = install(&cache.root(), "linux-amd64", "0.2.0", &mut host).unwrap();
    assert_ne!(old, new);
    assert!(old.is_file());
    assert!(new.is_file());
    assert_eq!(cache.entries(), 2);
}

#[test]
fn abandoned_partial_directory_does_not_block_next_attempt() {
    let cache = Cache::new();
    fs::create_dir(cache.root()).unwrap();
    let abandoned = cache.root().join(".download-interrupted-process");
    fs::create_dir(&abandoned).unwrap();
    fs::write(abandoned.join("partial"), b"incomplete").unwrap();
    assert!(install(
        &cache.root(),
        "linux-amd64",
        VERSION,
        &mut FakeHost::new(VERSION)
    )
    .unwrap()
    .is_file());
    assert!(abandoned.exists()); // Never execute or indiscriminately delete another attempt.
}

#[test]
fn all_five_platform_assets_are_independently_installable() {
    let cache = Cache::new();
    let mut host = FakeHost::new(VERSION);
    for platform in [
        "linux-amd64",
        "linux-arm64",
        "darwin-amd64",
        "darwin-arm64",
        "windows-amd64",
    ] {
        let path = install(&cache.root(), platform, VERSION, &mut host).unwrap();
        assert_eq!(
            path.file_name().unwrap(),
            asset_name(VERSION, platform).unwrap().as_str()
        );
        assert_eq!(
            path.extension().is_some_and(|ext| ext == "exe"),
            platform == "windows-amd64"
        );
    }
    assert_eq!(cache.entries(), 5);
    assert!(install(&cache.root(), "windows-arm64", VERSION, &mut host).is_err());
    assert!(asset_name("../unsafe", "linux-amd64").is_err());
}

#[test]
fn explicit_path_wins_without_lookup_or_network_on_unsupported_platform() {
    let cache = Cache::new();
    let mut host = FakeHost::new("0.1.0-dev");
    host.identity["buildMode"] = json!("development");
    host.identity["dirty"] = json!(true);
    let settings = json!({"serverPath":"/opt/custom/argo-workflows-ls"});
    assert_eq!(
        resolve(&cache.root(), None, false, Some(&settings), &mut host).unwrap(),
        PathBuf::from("/opt/custom/argo-workflows-ls")
    );
    assert_eq!(host.lookups, 0);
    assert_eq!(host.network_calls(), 0);
    assert_eq!(host.probes(), 1);
    assert!(!cache.root().exists());
}

#[test]
fn invalid_override_fails_without_network_or_path_fallback() {
    for value in [json!("relative"), json!(""), json!(null), json!(false)] {
        let cache = Cache::new();
        let mut host = FakeHost::new(VERSION);
        host.installed = Some("/usr/bin/argo-workflows-ls".into());
        assert!(resolve(
            &cache.root(),
            Some("linux-amd64"),
            false,
            Some(&json!({"serverPath":value})),
            &mut host
        )
        .is_err());
        assert_eq!(host.lookups, 0);
        assert_eq!(host.network_calls(), 0);
        assert_eq!(host.probes(), 0);
    }
    let cache = Cache::new();
    let mut host = FakeHost::new(VERSION);
    host.identity["product"] = json!("other");
    assert!(resolve(
        &cache.root(),
        Some("linux-amd64"),
        false,
        Some(&json!({"serverPath":"/custom/server"})),
        &mut host
    )
    .is_err());
    assert_eq!(host.network_calls(), 0);
}

#[test]
fn path_executable_is_checked_without_managed_download() {
    let cache = Cache::new();
    let mut host = FakeHost::new("local-build");
    host.installed = Some("/usr/bin/argo-workflows-ls".into());
    assert_eq!(
        resolve(&cache.root(), Some("linux-amd64"), false, None, &mut host).unwrap(),
        PathBuf::from("/usr/bin/argo-workflows-ls")
    );
    assert_eq!(host.lookups, 1);
    assert_eq!(host.probes(), 1);
    assert_eq!(host.network_calls(), 0);
    host.identity["analysisReady"] = json!(false);
    assert!(resolve(&cache.root(), Some("linux-amd64"), false, None, &mut host).is_err());
    assert_eq!(host.network_calls(), 0);
}

#[test]
fn windows_paths_are_validated_using_native_rules() {
    let cache = Cache::new();
    let mut host = FakeHost::new("local-build");
    for path in [
        r"C:\Tools\server.exe",
        "D:/tools/server.exe",
        r"\\host\share\server.exe",
        "//host/share/server.exe",
    ] {
        assert!(
            resolve(
                &cache.root(),
                None,
                true,
                Some(&json!({"serverPath":path})),
                &mut host
            )
            .is_ok(),
            "{path}"
        );
    }
    for path in ["C:relative.exe", "/unix/path", "1:/bad-drive.exe"] {
        assert!(
            resolve(
                &cache.root(),
                None,
                true,
                Some(&json!({"serverPath":path})),
                &mut host
            )
            .is_err(),
            "{path}"
        );
    }
}

#[cfg(unix)]
#[test]
fn cache_symlinks_are_never_executed_or_overwritten() {
    use std::os::unix::fs::symlink;
    let linked_root = Cache::new();
    let outside_root = linked_root.0.join("outside-root");
    fs::create_dir(&outside_root).unwrap();
    symlink(&outside_root, linked_root.root()).unwrap();
    assert!(directory(&linked_root.root())
        .unwrap_err()
        .contains("regular directory"));
    assert_eq!(fs::read_dir(&outside_root).unwrap().count(), 0);

    let cache = Cache::new();
    let mut host = FakeHost::new(VERSION);
    let path = install(&cache.root(), "linux-amd64", VERSION, &mut host).unwrap();
    let outside = cache.0.join("outside");
    fs::write(&outside, b"untrusted").unwrap();
    fs::remove_file(&path).unwrap();
    symlink(&outside, &path).unwrap();
    host.events.clear();
    assert!(install(&cache.root(), "linux-amd64", VERSION, &mut host).is_err());
    assert_eq!(host.probes(), 0);
    assert_eq!(host.network_calls(), 0);
    assert_eq!(fs::read(outside).unwrap(), b"untrusted");
}

#[test]
fn zero_length_and_oversized_binaries_never_execute() {
    for oversized in [false, true] {
        let cache = Cache::new();
        let mut host = FakeHost::new(VERSION);
        if oversized {
            host.failure = Some("oversized");
        } else {
            host.bytes.clear();
        }
        assert!(install(&cache.root(), "linux-amd64", VERSION, &mut host).is_err());
        assert_eq!(host.probes(), 0);
        assert_eq!(host.executable_calls(), 0);
        assert_eq!(cache.entries(), 0);
    }
}

#[test]
fn cached_execution_failure_preserves_bytes_and_is_retryable() {
    let cache = Cache::new();
    let mut host = FakeHost::new(VERSION);
    let path = install(&cache.root(), "linux-amd64", VERSION, &mut host).unwrap();
    host.failure = Some("identity");
    host.events.clear();
    let error = install(&cache.root(), "linux-amd64", VERSION, &mut host).unwrap_err();
    assert!(error.contains("Cannot validate cached server"));
    assert!(error.contains("remove this version directory"));
    assert_eq!(host.network_calls(), 0);
    assert!(path.is_file());
    host.failure = Some("offline");
    assert_eq!(
        install(&cache.root(), "linux-amd64", VERSION, &mut host).unwrap(),
        path
    );
}
