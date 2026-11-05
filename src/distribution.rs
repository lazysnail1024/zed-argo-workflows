//! Pinned, checksum-verified downloads in the extension's private work directory.
//! The host boundary is injected so acceptance tests exercise this exact installer.
use sha2::{Digest, Sha256};
use std::{
    fs,
    io::Read,
    path::{Path, PathBuf},
    sync::atomic::{AtomicU64, Ordering},
    time::{SystemTime, UNIX_EPOCH},
};

pub const VERSION: &str = env!("CARGO_PKG_VERSION");
pub const MAX_BINARY: u64 = 128 * 1024 * 1024;
pub const MAX_CHECKSUMS: u64 = 64 * 1024;
const REPOSITORY: &str = "https://github.com/lazysnail1024/zed-argo-workflows";
static ATTEMPT: AtomicU64 = AtomicU64::new(0);

pub trait Host {
    fn which(&mut self) -> Option<String> {
        None
    }
    fn fetch(&mut self, url: &str, destination: &Path, limit: u64) -> Result<(), String>;
    fn make_executable(&mut self, path: &Path) -> Result<(), String>;
    fn identity(&mut self, path: &Path) -> Result<Vec<u8>, String>;
}

pub fn asset_name(version: &str, platform: &str) -> Result<String, String> {
    if !matches!(
        platform,
        "linux-amd64" | "linux-arm64" | "darwin-amd64" | "darwin-arm64" | "windows-amd64"
    ) {
        return Err(format!("Unsupported server platform {platform}; configure settings.serverPath for a compatible native build"));
    }
    if version.is_empty()
        || !version
            .bytes()
            .all(|byte| byte.is_ascii_alphanumeric() || matches!(byte, b'.' | b'-' | b'+'))
    {
        return Err("Invalid pinned release version".into());
    }
    Ok(format!(
        "argo-workflows-ls-{version}-{platform}{}",
        if platform.starts_with("windows-") {
            ".exe"
        } else {
            ""
        }
    ))
}

fn existing_directory(path: &Path) -> Result<bool, String> {
    match fs::symlink_metadata(path) {
        Ok(metadata) if metadata.file_type().is_dir() => Ok(true),
        Ok(_) => Err(format!(
            "Server cache must be a regular directory: {}",
            path.display()
        )),
        Err(error) if error.kind() == std::io::ErrorKind::NotFound => Ok(false),
        Err(error) => Err(format!(
            "Cannot inspect server cache {}: {error}",
            path.display()
        )),
    }
}

fn directory(path: &Path) -> Result<(), String> {
    directory_with_create(path, |path| fs::create_dir(path))
}

fn directory_with_create(
    path: &Path,
    create: impl FnOnce(&Path) -> std::io::Result<()>,
) -> Result<(), String> {
    if existing_directory(path)? {
        return Ok(());
    }
    match create(path) {
        Ok(()) => Ok(()),
        // Another process can create the shared root after our initial check.
        // Recheck its type instead of treating that benign race as a failure.
        Err(error)
            if error.kind() == std::io::ErrorKind::AlreadyExists && existing_directory(path)? =>
        {
            Ok(())
        }
        Err(error) => Err(format!(
            "Cannot create server cache {}: {error}",
            path.display()
        )),
    }
}

fn regular(path: &Path, limit: u64) -> Result<bool, String> {
    match fs::symlink_metadata(path) {
        Ok(metadata) if !metadata.file_type().is_file() => Err(format!(
            "Server cache entry must be a regular file: {}",
            path.display()
        )),
        Ok(metadata) => Ok(metadata.len() > 0 && metadata.len() <= limit),
        Err(error) if error.kind() == std::io::ErrorKind::NotFound => Ok(false),
        Err(error) => Err(format!(
            "Cannot inspect cache file {}: {error}",
            path.display()
        )),
    }
}

pub fn checksum(manifest: &[u8], asset: &str) -> Result<String, String> {
    if manifest.len() as u64 > MAX_CHECKSUMS {
        return Err("Release checksum manifest exceeds its size limit".into());
    }
    let text =
        std::str::from_utf8(manifest).map_err(|_| "Release checksum manifest is not UTF-8")?;
    let mut found = None;
    for line in text.lines() {
        let Some((digest, name)) = line.split_once("  ") else {
            return Err("Malformed SHA256SUMS entry".into());
        };
        if digest.len() != 64
            || !digest
                .bytes()
                .all(|byte| byte.is_ascii_digit() || (b'a'..=b'f').contains(&byte))
            || name.is_empty()
            || name.contains(['/', '\\'])
            || name.bytes().any(|byte| byte.is_ascii_whitespace())
        {
            return Err("Malformed SHA256SUMS digest or asset name".into());
        }
        if name == asset && found.replace(digest.to_owned()).is_some() {
            return Err(format!("Duplicate SHA256SUMS entry for {asset}"));
        }
    }
    found.ok_or_else(|| format!("Release checksum missing for {asset}"))
}

pub fn digest(path: &Path) -> Result<String, String> {
    let mut file =
        fs::File::open(path).map_err(|error| format!("Cannot read server binary: {error}"))?;
    let mut hash = Sha256::new();
    let mut buffer = [0_u8; 64 * 1024];
    let mut received = 0_u64;
    loop {
        let count = file
            .read(&mut buffer)
            .map_err(|error| format!("Cannot hash server binary: {error}"))?;
        if count == 0 {
            break;
        }
        received += count as u64;
        if received > MAX_BINARY {
            return Err("Server binary exceeds its size limit".into());
        }
        hash.update(&buffer[..count]);
    }
    Ok(format!("{:x}", hash.finalize()))
}

fn verified<H: Host>(
    folder: &Path,
    asset: &str,
    version: &str,
    host: &mut H,
) -> Result<Option<PathBuf>, String> {
    let sums = folder.join("SHA256SUMS");
    let binary = folder.join(asset);
    if !regular(&sums, MAX_CHECKSUMS)? || !regular(&binary, MAX_BINARY)? {
        return Ok(None);
    }
    let data = fs::read(&sums).map_err(|error| error.to_string())?;
    let expected = match checksum(&data, asset) {
        Ok(value) => value,
        Err(_) => return Ok(None),
    };
    if digest(&binary)? != expected {
        return Ok(None);
    }
    let validated = (|| {
        host.make_executable(&binary)?;
        crate::identity::validate(&host.identity(&binary)?, Some(version))
    })();
    validated.map_err(|error| format!("Cannot validate cached server {}: {error}. Stop the language server and remove this version directory before retrying installation, or configure settings.serverPath.", folder.display()))?;
    Ok(Some(binary))
}

fn nonce() -> Result<String, String> {
    let now = SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .map_err(|error| error.to_string())?
        .as_nanos();
    Ok(format!("{now}-{}", ATTEMPT.fetch_add(1, Ordering::Relaxed)))
}

fn unique(root: &Path, prefix: &str) -> Result<PathBuf, String> {
    Ok(root.join(format!(".{prefix}-{}", nonce()?)))
}

fn staging(root: &Path) -> Result<PathBuf, String> {
    for _ in 0..8 {
        let path = unique(root, "download")?;
        match fs::create_dir(&path) {
            Ok(()) => return Ok(path),
            // Clocks and per-process counters can coincide across hosts. Only
            // exclusive creation establishes ownership of a staging directory.
            Err(error) if error.kind() == std::io::ErrorKind::AlreadyExists => continue,
            Err(error) => return Err(format!("Cannot create download staging directory: {error}")),
        }
    }
    Err("Cannot allocate a download staging directory after repeated collisions".into())
}

fn cached<H: Host>(
    root: &Path,
    slot: &str,
    asset: &str,
    version: &str,
    host: &mut H,
) -> Result<Option<PathBuf>, String> {
    let legacy = root.join(slot);
    if existing_directory(&legacy)? {
        if let Some(binary) = verified(&legacy, asset, version, host)? {
            return Ok(Some(binary));
        }
    }
    let prefix = format!("{slot}.gen-");
    let mut generations = Vec::new();
    for entry in fs::read_dir(root).map_err(|error| error.to_string())? {
        let entry = entry.map_err(|error| error.to_string())?;
        if entry
            .file_name()
            .to_str()
            .is_some_and(|name| name.starts_with(&prefix) && name.len() > prefix.len())
        {
            generations.push(entry.path());
        }
    }
    generations.sort();
    for generation in generations {
        if existing_directory(&generation)? {
            // Incomplete or byte-corrupt slots are retained but not executed.
            // A valid immutable repair generation can still work offline.
            if let Some(binary) = verified(&generation, asset, version, host)? {
                return Ok(Some(binary));
            }
        }
    }
    Ok(None)
}

fn remove_directory(path: &Path) -> Result<(), String> {
    // remove_dir_all does not follow symlinks. The top-level type is checked too.
    if !fs::symlink_metadata(path)
        .map_err(|error| error.to_string())?
        .file_type()
        .is_dir()
    {
        return Err(format!(
            "Refusing to remove non-directory cache entry {}",
            path.display()
        ));
    }
    fs::remove_dir_all(path)
        .map_err(|error| format!("Cannot clean temporary cache {}: {error}", path.display()))
}

fn activate<H: Host>(
    stage: &Path,
    root: &Path,
    slot: &str,
    asset: &str,
    version: &str,
    host: &mut H,
    rename: &mut impl FnMut(&Path, &Path) -> std::io::Result<()>,
) -> Result<(PathBuf, bool), String> {
    let mut destination = root.join(format!("{slot}.gen-{}", nonce()?));
    for _ in 0..8 {
        // Published slots always contain the manifest and executable. Directory
        // rename cannot replace a nonempty destination on Unix or Windows. We
        // never move or remove a published slot, even when it is byte-corrupt.
        match rename(stage, &destination) {
            Ok(()) => return Ok((destination.join(asset), true)),
            Err(error) => {
                if !existing_directory(&destination)? {
                    return Err(format!("Cannot activate verified server: {error}"));
                }
                if let Some(winner) = verified(&destination, asset, version, host)? {
                    return Ok((winner, false));
                }
                // A repair may also have completed in another immutable slot
                // after our final cache lookup. Prefer it before allocating one.
                if let Some(winner) = cached(root, slot, asset, version, host)? {
                    return Ok((winner, false));
                }
                destination = root.join(format!("{slot}.gen-{}", nonce()?));
            }
        }
    }
    Err("Cannot activate verified server after repeated cache directory collisions".into())
}

pub fn install<H: Host>(
    root: &Path,
    platform: &str,
    version: &str,
    host: &mut H,
) -> Result<PathBuf, String> {
    install_with_rename(root, platform, version, host, &mut |from, to| {
        fs::rename(from, to)
    })
}

fn install_with_rename<H: Host>(
    root: &Path,
    platform: &str,
    version: &str,
    host: &mut H,
    rename: &mut impl FnMut(&Path, &Path) -> std::io::Result<()>,
) -> Result<PathBuf, String> {
    let asset = asset_name(version, platform)?;
    directory(root)?;
    let slot = format!("v{version}-{platform}");
    if let Some(binary) = cached(root, &slot, &asset, version, host)? {
        return Ok(binary);
    }
    let stage = staging(root)?;
    let mut stage_owned = true;
    let result = (|| {
        let url = format!("{REPOSITORY}/releases/download/v{version}");
        let sums = stage.join("SHA256SUMS");
        let sums_url = format!("{url}/SHA256SUMS");
        host.fetch(&sums_url, &sums, MAX_CHECKSUMS)
            .map_err(|error| {
                format!("Cannot download checksum manifest from {sums_url}: {error}")
            })?;
        if !regular(&sums, MAX_CHECKSUMS)? {
            return Err("Missing or oversized release checksum manifest".into());
        }
        let expected = checksum(&fs::read(&sums).map_err(|error| error.to_string())?, &asset)?;
        let binary = stage.join(&asset);
        let binary_url = format!("{url}/{asset}");
        host.fetch(&binary_url, &binary, MAX_BINARY)
            .map_err(|error| format!("Cannot download native server from {binary_url}: {error}"))?;
        if !regular(&binary, MAX_BINARY)? || digest(&binary)? != expected {
            return Err(format!(
                "SHA-256 mismatch for {asset}; refusing to execute downloaded bytes"
            ));
        }
        host.make_executable(&binary)?;
        crate::identity::validate(&host.identity(&binary)?, Some(version))?;
        if let Some(winner) = cached(root, &slot, &asset, version, host)? {
            return Ok(winner);
        }
        let (binary, moved) = activate(&stage, root, &slot, &asset, version, host, rename)?;
        stage_owned = !moved;
        Ok(binary)
    })();
    // After publication the old staging pathname is no longer ours. Another
    // process may reuse it; successful rename must not trigger any cleanup there.
    let cleanup = if stage_owned {
        stage
            .try_exists()
            .map_err(|error| error.to_string())
            .and_then(|exists| {
                if exists {
                    remove_directory(&stage)
                } else {
                    Ok(())
                }
            })
    } else {
        Ok(())
    };
    if let Err(cleanup) = cleanup {
        match &result {
            // The selected server has already passed validation. A leftover
            // staging directory must not prevent it from starting.
            Ok(_) => eprintln!("{cleanup}; temporary cache cleanup deferred"),
            Err(error) => return Err(format!("{error}; {cleanup}")),
        }
    }
    result.map_err(|error| format!("Cannot install Workflows server v{version} for {platform}: {error}. Check network access and release assets, or configure lsp.argo-workflows-ls.settings.serverPath for an offline installation. Existing versions are retained; no older release is selected automatically."))
}

/// Explicit paths take precedence, even on platforms with no published asset.
/// Do not inspect them with WASI filesystem APIs: user binaries are outside the sandbox.
pub fn resolve<H: Host>(
    root: &Path,
    platform: Option<&str>,
    windows: bool,
    settings: Option<&serde_json::Value>,
    host: &mut H,
) -> Result<PathBuf, String> {
    let explicit = settings.and_then(|value| value.get("serverPath"));
    let path = match explicit {
        Some(value) => Some(
            value
                .as_str()
                .filter(|value| !value.is_empty())
                .ok_or("settings.serverPath must be an absolute path string")?
                .to_owned(),
        ),
        None => host.which(),
    };
    if let Some(path) = path {
        let absolute = if windows {
            path.starts_with("\\\\")
                || path.starts_with("//")
                || (path.as_bytes().first().is_some_and(u8::is_ascii_alphabetic)
                    && path.as_bytes().get(1) == Some(&b':')
                    && matches!(path.as_bytes().get(2), Some(b'\\' | b'/')))
        } else {
            path.starts_with('/')
        };
        if !absolute {
            return Err("Server executable path must be absolute".into());
        }
        let path = PathBuf::from(path);
        let description = if explicit.is_some() {
            "settings.serverPath"
        } else {
            "PATH executable argo-workflows-ls"
        };
        crate::identity::validate(&host.identity(&path).map_err(|error| format!("Cannot run {description} {path:?}: {error}. Correct the executable path or remove the override/PATH entry to use the managed release."))?, None)
            .map_err(|error| format!("Invalid {description} {path:?}: {error}. Correct the executable or remove it to use the managed release."))?;
        return Ok(path);
    }
    install(
        root,
        platform.ok_or("No published server for this platform; configure settings.serverPath")?,
        VERSION,
        host,
    )
}

#[cfg(test)]
mod tests;

#[cfg(not(test))]
pub struct ZedHost<'a>(pub &'a zed_extension_api::Worktree);
#[cfg(not(test))]
impl Host for ZedHost<'_> {
    fn which(&mut self) -> Option<String> {
        self.0.which("argo-workflows-ls")
    }
    fn fetch(&mut self, url: &str, destination: &Path, limit: u64) -> Result<(), String> {
        // Both assets use Zed's download API and its capability restrictions.
        // This API has no streaming limit; reject oversized files before reading,
        // hashing, chmod or execution and remove the entire staging directory.
        zed_extension_api::download_file(
            url,
            destination.to_str().ok_or("Server path is not UTF-8")?,
            zed_extension_api::DownloadedFileType::Uncompressed,
        )?;
        if !regular(destination, limit)? {
            return Err(format!(
                "Missing, empty, or oversized download (limit {limit} bytes)"
            ));
        }
        Ok(())
    }
    fn make_executable(&mut self, path: &Path) -> Result<(), String> {
        zed_extension_api::make_file_executable(path.to_str().ok_or("Server path is not UTF-8")?)
    }
    fn identity(&mut self, path: &Path) -> Result<Vec<u8>, String> {
        // Managed paths are sandbox-relative; host processes do not inherit that
        // working directory. External paths are already native absolute paths.
        let absolute = if path.starts_with("servers") {
            std::env::current_dir()
                .map_err(|error| error.to_string())?
                .join(path)
        } else {
            path.to_path_buf()
        };
        let output = zed_extension_api::process::Command::new(
            absolute.to_str().ok_or("Server path is not UTF-8")?,
        )
        .args(["version", "--json"])
        .output()?;
        if output.status != Some(0) {
            return Err("Server identity command failed".into());
        }
        Ok(output.stdout)
    }
}
