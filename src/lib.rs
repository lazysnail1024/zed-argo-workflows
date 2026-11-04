use zed_extension_api::{self as zed, settings::LspSettings, Command, LanguageServerId, Worktree};

struct Workflows;
impl zed::Extension for Workflows {
    fn new() -> Self { Self }
    fn language_server_workspace_configuration(&mut self, id: &LanguageServerId, worktree: &Worktree) -> zed::Result<Option<serde_json::Value>> {
        if id.as_ref() != "argo-workflows-ls" { return Err("unexpected language server ID".into()); }
        Ok(LspSettings::for_worktree("argo-workflows-ls", worktree)?.settings)
    }
    fn language_server_command(&mut self, id: &LanguageServerId, worktree: &Worktree) -> zed::Result<Command> {
        if id.as_ref() != "argo-workflows-ls" { return Err("unexpected language server ID".into()); }
        let settings = LspSettings::for_worktree("argo-workflows-ls", worktree)?.settings;
        let path = match settings.as_ref().and_then(|settings| settings.get("serverPath")) {
            Some(value) => value.as_str().filter(|value| !value.is_empty())
                .ok_or("settings.serverPath must be an absolute path string")?.to_owned(),
            None => bundled_server()?,
        };
        let (os, _) = zed::current_platform();
        let absolute = match os { zed::Os::Windows => path.starts_with("\\\\") || (path.as_bytes().get(1)==Some(&b':') && matches!(path.as_bytes().get(2),Some(b'\\'|b'/'))), _ => path.starts_with('/') };
        if !absolute { return Err("binary path must be absolute".into()); }
        let args = vec!["serve".to_string(), "--stdio".to_string()];
        let env = Vec::new();
        let output = zed::process::Command::new(&path).args(["version", "--json"]).output()?;
        if output.status != Some(0) { return Err("server version handshake failed".into()); }
        let value: serde_json::Value = serde_json::from_slice(&output.stdout).map_err(|e| format!("invalid server identity: {e}"))?;
        for key in ["product", "serverVersion", "adapterContractVersion", "analysisReady", "configSchemaVersions", "reportSchemaVersions", "toolkitVersion", "catalogHash", "sourceCommit", "dirty", "buildMode"] {
            if value.get(key).is_none() { return Err(format!("server identity missing {key}")); }
        }
        if value["product"] != "argo-workflows" || value["adapterContractVersion"] != 1 { return Err("server product or adapter contract mismatch".into()); }
        if value["analysisReady"] != true { return Err("server analysis is not ready; refusing to start".into()); }
        Ok(Command { command: path, args, env })
    }
}
zed::register_extension!(Workflows);

#[cfg(not(feature = "bundled-server"))]
fn bundled_server() -> zed::Result<String> {
    Err("Source checkout requires lsp.argo-workflows-ls.settings.serverPath. Install a precompiled development bundle for automatic startup.".into())
}

#[cfg(feature = "bundled-server")]
fn bundled_server() -> zed::Result<String> {
    use std::{fs, path::Path};
    const PAYLOAD: &[u8] = include_bytes!("../native/server.bin");
    let manifest: serde_json::Value = serde_json::from_str(include_str!("../native/manifest.json"))
        .map_err(|error| format!("invalid bundled server manifest: {error}"))?;
    let platform = match zed::current_platform() {
        (zed::Os::Linux, zed::Architecture::X8664) => "linux/amd64",
        (zed::Os::Linux, zed::Architecture::Aarch64) => "linux/arm64",
        (zed::Os::Mac, zed::Architecture::X8664) => "darwin/amd64",
        (zed::Os::Mac, zed::Architecture::Aarch64) => "darwin/arm64",
        (zed::Os::Windows, zed::Architecture::X8664) => "windows/amd64",
        _ => return Err("unsupported bundled server platform".into()),
    };
    let hash = manifest["binarySha256"].as_str().ok_or("bundled binary SHA-256 is missing")?;
    if manifest["schemaVersion"] != 1 || manifest["product"] != "argo-workflows"
        || manifest["platform"] != platform || manifest["binarySize"].as_u64() != Some(PAYLOAD.len() as u64)
        || hash.len() != 64 || !hash.bytes().all(|byte| byte.is_ascii_digit() || (b'a'..=b'f').contains(&byte)) {
        return Err("bundled server metadata or host platform mismatch".into());
    }
    let directory = format!("argo-workflows-{}-{hash}", platform.replace('/', "-"));
    match fs::symlink_metadata(&directory) {
        Ok(metadata) if metadata.file_type().is_dir() => {},
        Ok(_) => return Err("bundled server cache directory is not a regular directory".into()),
        Err(error) if error.kind() == std::io::ErrorKind::NotFound => {
            fs::create_dir(&directory).map_err(|error| format!("cannot create bundled cache directory: {error}"))?;
        },
        Err(error) => return Err(format!("cannot inspect bundled cache directory: {error}")),
    }
    let name = format!("{directory}/argo-workflows-ls{}", if platform.starts_with("windows/") { ".exe" } else { "" });
    let path = Path::new(&name);
    // The extension work directory is private to this extension. Never execute cached bytes
    // until they match the embedded payload; never follow a cache symlink when repairing.
    if let Ok(metadata) = fs::symlink_metadata(path) {
        if !metadata.file_type().is_file() { return Err("bundled server cache is not a regular file".into()); }
    }
    if fs::read(path).ok().as_deref() != Some(PAYLOAD) {
        let temporary = format!("{name}.tmp");
        let mut file = fs::OpenOptions::new().write(true).create_new(true).open(&temporary)
            .map_err(|error| format!("cannot create bundled server cache: {error}"))?;
        use std::io::Write;
        let result = file.write_all(PAYLOAD).and_then(|_| file.sync_all());
        drop(file);
        if let Err(error) = result {
            let _ = fs::remove_file(&temporary);
            return Err(format!("cannot write bundled server cache: {error}"));
        }
        // Windows cannot replace an existing executable via rename. Refuse safely if it
        // is in use; after shutdown a subsequent startup can repair the cache.
        if path.exists() {
            if let Err(error) = fs::remove_file(path) {
                let _ = fs::remove_file(&temporary);
                return Err(format!("cannot replace bundled server cache: {error}"));
            }
        }
        if let Err(error) = fs::rename(&temporary, path) {
            let _ = fs::remove_file(&temporary);
            return Err(format!("cannot install bundled server cache: {error}"));
        }
    }
    zed::make_file_executable(&name)?;
    let absolute = std::env::current_dir().map_err(|error| error.to_string())?.join(path);
    absolute.to_str().map(str::to_owned).ok_or("bundled server path is not UTF-8".into())
}
