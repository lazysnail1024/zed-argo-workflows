//! The installer and all its filesystem operations execute in WASI. The parent
//! supplies only the download/chmod/native-process boundaries that Zed supplies.
use crate::distribution::{self, Host};
use serde_json::{json, Value};
use std::{
    fs,
    io::{self, Write},
    path::{Path, PathBuf},
};

fn request(value: Value) -> Result<Value, String> {
    let mut stdout = io::stdout().lock();
    writeln!(stdout, "\nARGO_CACHE_HOST:{}", value).map_err(|error| error.to_string())?;
    stdout.flush().map_err(|error| error.to_string())?;
    drop(stdout);
    let mut reply = String::new();
    io::stdin()
        .read_line(&mut reply)
        .map_err(|error| error.to_string())?;
    if reply.is_empty() || reply.len() > 64 * 1024 {
        return Err("Missing or oversized runtime host reply".into());
    }
    let reply: Value = serde_json::from_str(&reply).map_err(|error| error.to_string())?;
    if let Some(error) = reply.get("error").and_then(Value::as_str) {
        return Err(error.to_owned());
    }
    reply
        .get("result")
        .cloned()
        .ok_or_else(|| "Malformed runtime host reply".into())
}

#[derive(Default)]
struct RuntimeHost {
    offline: bool,
    fetches: usize,
    probes: Vec<PathBuf>,
}

impl Host for RuntimeHost {
    fn fetch(&mut self, url: &str, destination: &Path, limit: u64) -> Result<(), String> {
        if self.offline {
            return Err("Runtime fixture is offline".into());
        }
        self.fetches += 1;
        request(json!({"op":"fetch", "url":url, "path":destination, "limit":limit}))?;
        Ok(())
    }

    fn make_executable(&mut self, path: &Path) -> Result<(), String> {
        request(json!({"op":"chmod", "path":path}))?;
        Ok(())
    }

    fn identity(&mut self, path: &Path) -> Result<Vec<u8>, String> {
        self.probes.push(path.to_path_buf());
        request(json!({"op":"identity", "path":path}))?
            .as_str()
            .map(|value| value.as_bytes().to_vec())
            .ok_or_else(|| "Runtime identity is not a string".into())
    }
}

#[test]
fn wasi_cache_runtime() {
    let platform = std::env::var("ARGO_CACHE_PLATFORM").unwrap();
    let scenario = std::env::var("ARGO_CACHE_SCENARIO").unwrap();
    let client = std::env::var("ARGO_CACHE_CLIENT").unwrap();
    assert!(matches!(client.as_str(), "a" | "b"));
    let mut controls = json!({"applicable":false});
    if platform == "windows-amd64" && scenario == "fresh" {
        request(json!({"op":"lock-control"})).unwrap();
        // Both operations use the actual Windows-hosted WASI filesystem. The
        // parent holds a real non-delete-sharing handle, not an injected errno.
        let rename = fs::rename("lock-control", "lock-control-moved").unwrap_err();
        let remove = fs::remove_dir_all("lock-control").unwrap_err();
        assert_eq!(rename.kind(), io::ErrorKind::PermissionDenied);
        assert_eq!(remove.kind(), io::ErrorKind::PermissionDenied);
        assert!(Path::new("lock-control/server.exe").is_file());
        controls = json!({"applicable":true, "rename":rename.to_string(),
                          "remove":remove.to_string()});
    }
    let mut host = RuntimeHost::default();
    let install = |host: &mut RuntimeHost| {
        distribution::install(
            Path::new("servers"),
            &platform,
            distribution::server_version(),
            host,
        )
    };
    let mut interrupted = None;
    if scenario == "interrupted" {
        let error = install(&mut host).unwrap_err();
        assert!(error.contains("runtime fixture interrupted transfer"));
        assert!(host.probes.is_empty());
        interrupted = Some(error);
    }
    let first = install(&mut host).unwrap();
    let selected = if scenario == "corrupt" {
        // Corrupt the manifest while the native parent retains the executable's
        // handle. Rewriting an in-use executable is itself prohibited by some OSes.
        let manifest = first.parent().unwrap().join("SHA256SUMS");
        request(json!({"op":"corrupt", "path":manifest})).unwrap();
        let repaired = install(&mut host).unwrap();
        assert_ne!(first, repaired);
        assert_eq!(
            fs::read(&manifest).unwrap(),
            b"corrupted in-use cached manifest"
        );
        assert_eq!(
            distribution::digest(&first).unwrap(),
            distribution::digest(&repaired).unwrap()
        );
        repaired
    } else {
        first.clone()
    };
    // A probe path must not be moved or removed afterwards, including a losing
    // concurrent installer and the retained, corrupt generation being repaired.
    assert!(host.probes.iter().all(|path| path.is_file()));
    let fetches = host.fetches;
    host.offline = true;
    let offline = install(&mut host).unwrap();
    if scenario != "concurrent" {
        assert_eq!(selected, offline);
    }
    assert_eq!(
        distribution::digest(&selected).unwrap(),
        distribution::digest(&offline).unwrap()
    );
    assert!(host.probes.iter().all(|path| path.is_file()));
    assert_eq!(fetches, host.fetches);
    let report = json!({"passed":true, "target":std::env::consts::ARCH,
        "platform":platform, "scenario":scenario, "client":client,
        "first":first, "selected":selected, "offline":offline,
        "sha256":distribution::digest(&selected).unwrap(), "fetches":host.fetches,
        "probePaths":host.probes, "lockControl":controls, "interrupted":interrupted});
    fs::write(format!("guest-report-{client}.json"), report.to_string()).unwrap();
}
