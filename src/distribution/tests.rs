use super::*;
use serde_json::{json, Value};

struct Cache(PathBuf);
impl Cache {
    fn new() -> Self {
        let path = unique(&std::env::temp_dir(), "workflows-installer-test").unwrap();
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
    assert!(host.events[0].ends_with("/v0.1.0/SHA256SUMS"));
    assert!(host.events[1].ends_with("/v0.1.0/argo-workflows-ls-0.1.0-linux-amd64"));
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
fn concurrent_installation_keeps_verified_winner_and_defers_unavailable_cleanup() {
    struct ConcurrentHost {
        inner: FakeHost,
        winner: PathBuf,
        asset: String,
        stage: Option<PathBuf>,
        obstruct_cleanup: bool,
    }
    impl Host for ConcurrentHost {
        fn fetch(&mut self, url: &str, destination: &Path, limit: u64) -> Result<(), String> {
            self.inner.fetch(url, destination, limit)?;
            if url.ends_with("/SHA256SUMS") {
                // Another process finishes while this process's first download
                // is in flight. Its marker must survive our subsequent install.
                fs::create_dir(&self.winner).unwrap();
                fs::copy(destination, self.winner.join("SHA256SUMS")).unwrap();
                fs::write(self.winner.join(&self.asset), &self.inner.bytes).unwrap();
                fs::write(self.winner.join("winner-sentinel"), b"concurrent winner").unwrap();
                self.stage = Some(destination.parent().unwrap().to_path_buf());
            }
            Ok(())
        }
        fn make_executable(&mut self, path: &Path) -> Result<(), String> {
            self.inner.make_executable(path)
        }
        fn identity(&mut self, path: &Path) -> Result<Vec<u8>, String> {
            let identity = self.inner.identity(path)?;
            if self.obstruct_cleanup && path.parent() == Some(self.winner.as_path()) {
                // Make cleanup fail deterministically on every host, including
                // privileged test runners where chmod cannot deny deletion.
                let stage = self.stage.as_ref().unwrap();
                fs::remove_dir_all(stage).unwrap();
                fs::write(stage, b"unexpected staging entry").unwrap();
            }
            Ok(identity)
        }
    }

    for obstruct_cleanup in [false, true] {
        let cache = Cache::new();
        let asset = asset_name(VERSION, "linux-amd64").unwrap();
        let winner = cache.root().join(format!("v{VERSION}-linux-amd64"));
        let mut host = ConcurrentHost {
            inner: FakeHost::new(VERSION),
            winner: winner.clone(),
            asset: asset.clone(),
            stage: None,
            obstruct_cleanup,
        };
        let path = install(&cache.root(), "linux-amd64", VERSION, &mut host).unwrap();
        assert_eq!(path, winner.join(&asset));
        assert_eq!(fs::read(&path).unwrap(), host.inner.bytes);
        assert_eq!(
            fs::read(winner.join("winner-sentinel")).unwrap(),
            b"concurrent winner"
        );
        assert_eq!(host.inner.probes(), 2); // Staging and the actual winning cache.
        assert_eq!(
            host.inner.events.last().unwrap(),
            &format!("probe:{}", path.display())
        );
        let entries: Vec<_> = fs::read_dir(cache.root())
            .unwrap()
            .map(|entry| entry.unwrap().file_name())
            .collect();
        assert!(!entries
            .iter()
            .any(|name| name.to_string_lossy().starts_with(".previous-")));
        let stage = host.stage.unwrap();
        if obstruct_cleanup {
            assert_eq!(entries.len(), 2);
            assert_eq!(fs::read(stage).unwrap(), b"unexpected staging entry");
        } else {
            assert_eq!(entries.len(), 1);
            assert!(!stage.exists());
        }
    }
}

#[test]
fn publication_after_final_lookup_preserves_both_installers_and_offline_reuse() {
    for repair in [false, true] {
        let cache = Cache::new();
        let mut host = FakeHost::new(VERSION);
        let corrupt = if repair {
            let path = install(&cache.root(), "linux-amd64", VERSION, &mut host).unwrap();
            fs::write(&path, b"corrupted published server").unwrap();
            Some(path)
        } else {
            None
        };
        host.events.clear();
        let mut winner = None;
        let mut rename_calls = 0;
        let selected = install_with_rename(
            &cache.root(),
            "linux-amd64",
            VERSION,
            &mut host,
            &mut |stage, destination| {
                rename_calls += 1;
                assert_eq!(rename_calls, 1);
                // A has finished its final cache lookup. Run B's complete
                // installer before A's actual rename, then keep a live reference
                // to B's published directory as a real Zed process would.
                let path = install(
                    &cache.root(),
                    "linux-amd64",
                    VERSION,
                    &mut FakeHost::new(VERSION),
                )
                .unwrap();
                fs::write(
                    path.parent().unwrap().join("winner-sentinel"),
                    b"still in use",
                )
                .unwrap();
                winner = Some(path);
                fs::rename(stage, destination)
            },
        )
        .unwrap();
        let winner = winner.unwrap();
        assert_ne!(selected, winner);
        for path in [&selected, &winner] {
            assert_eq!(fs::read(path).unwrap(), host.bytes);
        }
        assert_eq!(
            fs::read(winner.parent().unwrap().join("winner-sentinel")).unwrap(),
            b"still in use"
        );
        if let Some(corrupt) = &corrupt {
            assert_eq!(fs::read(corrupt).unwrap(), b"corrupted published server");
        }
        assert_eq!(cache.entries(), if repair { 3 } else { 2 });
        assert!(fs::read_dir(cache.root()).unwrap().all(|entry| {
            let name = entry.unwrap().file_name();
            !name.to_string_lossy().starts_with('.')
        }));

        // A fresh adapter instance must find a verified generation without any
        // download, even when a retained corrupt slot sorts before it.
        let mut offline = FakeHost::new(VERSION);
        offline.failure = Some("offline");
        let reused = install(&cache.root(), "linux-amd64", VERSION, &mut offline).unwrap();
        assert!(reused == selected || reused == winner);
        assert_eq!(offline.network_calls(), 0);
        assert_eq!(offline.probes(), 1);
        assert_eq!(fs::read(&selected).unwrap(), host.bytes);
        assert_eq!(fs::read(&winner).unwrap(), host.bytes);
    }
}

#[test]
fn publication_collision_reuses_verified_target_without_moving_it() {
    let cache = Cache::new();
    let mut host = FakeHost::new(VERSION);
    let asset = asset_name(VERSION, "linux-amd64").unwrap();
    let mut collided = None;
    let selected = install_with_rename(
        &cache.root(),
        "linux-amd64",
        VERSION,
        &mut host,
        &mut |stage, destination| {
            // Simulate a coincident generation name across two processes. Both
            // candidates are complete, and the real filesystem decides which
            // rename wins; no platform-specific error kind is assumed.
            fs::create_dir(destination).unwrap();
            for name in ["SHA256SUMS", asset.as_str()] {
                fs::copy(stage.join(name), destination.join(name)).unwrap();
            }
            fs::write(destination.join("winner-sentinel"), b"published first").unwrap();
            collided = Some(destination.to_path_buf());
            fs::rename(stage, destination)
        },
    )
    .unwrap();
    let collided = collided.unwrap();
    assert_eq!(selected, collided.join(asset));
    assert_eq!(fs::read(&selected).unwrap(), host.bytes);
    assert_eq!(
        fs::read(collided.join("winner-sentinel")).unwrap(),
        b"published first"
    );
    assert_eq!(host.probes(), 2); // Staged candidate, then the actual winner.
    assert_eq!(
        host.events.last().unwrap(),
        &format!("probe:{}", selected.display())
    );
    assert_eq!(cache.entries(), 1);
}

#[test]
fn corrupt_publication_collision_uses_another_generation_without_removal() {
    let cache = Cache::new();
    let mut host = FakeHost::new(VERSION);
    let mut collided = None;
    let mut calls = 0;
    let selected = install_with_rename(
        &cache.root(),
        "linux-amd64",
        VERSION,
        &mut host,
        &mut |stage, destination| {
            calls += 1;
            if calls == 1 {
                fs::create_dir(destination).unwrap();
                fs::write(destination.join("SHA256SUMS"), b"corrupted manifest").unwrap();
                fs::write(destination.join("keep-sentinel"), b"do not delete").unwrap();
                collided = Some(destination.to_path_buf());
            }
            fs::rename(stage, destination)
        },
    )
    .unwrap();
    let collided = collided.unwrap();
    assert_ne!(selected.parent().unwrap(), collided);
    assert_eq!(calls, 2);
    assert_eq!(fs::read(&selected).unwrap(), host.bytes);
    assert_eq!(
        fs::read(collided.join("keep-sentinel")).unwrap(),
        b"do not delete"
    );
    assert_eq!(
        fs::read(collided.join("SHA256SUMS")).unwrap(),
        b"corrupted manifest"
    );
    assert_eq!(host.probes(), 1);
    assert_eq!(cache.entries(), 2);
    host.events.clear();
    host.failure = Some("offline");
    assert_eq!(
        install(&cache.root(), "linux-amd64", VERSION, &mut host).unwrap(),
        selected
    );
    assert_eq!(host.network_calls(), 0);
}

#[test]
fn successful_publication_does_not_clean_reused_staging_pathname() {
    let cache = Cache::new();
    let mut host = FakeHost::new(VERSION);
    let mut reused = None;
    let selected = install_with_rename(
        &cache.root(),
        "linux-amd64",
        VERSION,
        &mut host,
        &mut |stage, destination| {
            fs::rename(stage, destination)?;
            fs::create_dir(stage)?;
            fs::write(stage.join("other-installer-sentinel"), b"new owner")?;
            reused = Some(stage.to_path_buf());
            Ok(())
        },
    )
    .unwrap();
    assert_eq!(fs::read(selected).unwrap(), host.bytes);
    assert_eq!(
        fs::read(reused.unwrap().join("other-installer-sentinel")).unwrap(),
        b"new owner"
    );
    assert_eq!(cache.entries(), 2);
}

#[test]
fn publication_failure_without_destination_is_visible_and_retryable() {
    let cache = Cache::new();
    let mut host = FakeHost::new(VERSION);
    let error = install_with_rename(
        &cache.root(),
        "linux-amd64",
        VERSION,
        &mut host,
        &mut |_, _| Err(std::io::Error::from(std::io::ErrorKind::PermissionDenied)),
    )
    .unwrap_err();
    assert!(error.contains("Cannot activate verified server"));
    assert_eq!(cache.entries(), 0);
    assert!(install(&cache.root(), "linux-amd64", VERSION, &mut host)
        .unwrap()
        .is_file());
}

#[test]
fn concurrent_root_creation_accepts_only_a_directory() {
    for directory_wins in [true, false] {
        let cache = Cache::new();
        let result = directory_with_create(&cache.root(), |path| {
            // The initial absence check has completed; another process creates
            // the shared path before our actual create_dir syscall.
            if directory_wins {
                fs::create_dir(path).unwrap();
            } else {
                fs::write(path, b"unexpected file").unwrap();
            }
            fs::create_dir(path)
        });
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
