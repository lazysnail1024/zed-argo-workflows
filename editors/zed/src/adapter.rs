use crate::distribution::{self, ZedHost};
use std::path::Path;
use zed_extension_api::{self as zed, settings::LspSettings, Command, LanguageServerId, Worktree};

struct Workflows;
impl zed::Extension for Workflows {
    fn new() -> Self {
        Self
    }
    fn language_server_workspace_configuration(
        &mut self,
        id: &LanguageServerId,
        worktree: &Worktree,
    ) -> zed::Result<Option<serde_json::Value>> {
        if id.as_ref() != "argo-workflows-ls" {
            return Err("Unexpected language server ID".into());
        }
        Ok(LspSettings::for_worktree("argo-workflows-ls", worktree)?.settings)
    }
    fn language_server_command(
        &mut self,
        id: &LanguageServerId,
        worktree: &Worktree,
    ) -> zed::Result<Command> {
        if id.as_ref() != "argo-workflows-ls" {
            return Err("Unexpected language server ID".into());
        }
        let settings = LspSettings::for_worktree("argo-workflows-ls", worktree)?.settings;
        let (os, arch) = zed::current_platform();
        let windows = matches!(os, zed::Os::Windows);
        let platform = match (os, arch) {
            (zed::Os::Linux, zed::Architecture::X8664) => Some("linux-amd64"),
            (zed::Os::Linux, zed::Architecture::Aarch64) => Some("linux-arm64"),
            (zed::Os::Mac, zed::Architecture::X8664) => Some("darwin-amd64"),
            (zed::Os::Mac, zed::Architecture::Aarch64) => Some("darwin-arm64"),
            (zed::Os::Windows, zed::Architecture::X8664) => Some("windows-amd64"),
            _ => None,
        };
        let path = distribution::resolve(
            Path::new("servers"),
            platform,
            windows,
            settings.as_ref(),
            &mut ZedHost(worktree),
        )?;
        let absolute = if path.starts_with("servers") {
            std::env::current_dir()
                .map_err(|error| error.to_string())?
                .join(path)
        } else {
            path
        };
        Ok(Command {
            command: absolute
                .to_str()
                .ok_or("Server path is not UTF-8")?
                .to_owned(),
            args: vec!["serve".into(), "--stdio".into()],
            env: Vec::new(),
        })
    }
}
zed::register_extension!(Workflows);
