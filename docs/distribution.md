# 分发与安装

## 两个独立版本

| 组件 | 当前源码中的版本入口 | 交付形式 |
| --- | --- | --- |
| 原生 Go LSP/CLI | 根 [VERSION](../VERSION)，当前 `0.0.1-alpha.2` | `v<VERSION>` 下的平台可执行文件、独立 archive、来源记录、`SHA256SUMS` |
| Zed adapter | [extension.toml](../editors/zed/extension.toml) 与 [Cargo.toml](../editors/zed/Cargo.toml)，当前 `0.0.1-alpha.3` | `zed-v<VERSION>` 下预编译 adapter ZIP、身份记录、`SHA256SUMS` |
| adapter 选择的原生服务 | [native-server-version](../editors/zed/native-server-version)，当前 `0.0.1-alpha.2` | 固定 native release，独立于 adapter 自身版本 |

这里列出源码配置，不据此宣称对应 release 已发布或包含 main 的后续改动。
安装前以 [Releases](https://github.com/lazysnail1024/zed-argo-workflows/releases) 的资产为准，
运行后用 `version --json` 核对 `sourceCommit`/`toolkitVersion`/`catalogHash`。
固定 tag 的产物不会随源码更新；已安装 alpha.2 不因此获得后续索引重构。
下载使用明确版本和直达 URL，不依赖 latest-stable endpoint。修正已发布产物需新版本。

## 发布自动化

根 `VERSION` 变更合并到 main 触发 [release.yml](../.github/workflows/release.yml)；
`editors/zed/extension.toml` 变更触发独立 [zed-release.yml](../.github/workflows/zed-release.yml)，
adapter 的 Cargo 版本必须一致。匹配版本的 tag push 和 main/对应 tag 的手动运行也受支持。
各自完整验收后在精确 commit 建 tag 并发布已验证资产；已有 tag 不移动，已有 release 或 draft 会停止发布。
main 上 release workflow 自身变化可重试原生发布，但同样不覆盖现有产物。

adapter 发布前核对 native pin：原生 release 必须包含 `SHA256SUMS` 及 Linux amd64/arm64、
macOS amd64/arm64、Windows amd64 五个原始可执行文件。若 release 为 404 或 draft，
每 30 秒等待一次、最多 45 分钟；权限/其他 HTTP 错误、tag 不匹配或已发布但资产不完整直接失败。
等待超时后，可在原生 release 就绪后重跑发布 job。adapter 不重建 Go，native 可以来自不同 commit。
原生发布说明固定读取 [native-release-notes.md](native-release-notes.md)。

## 预编译安装、升级与回滚

普通安装入口见 [README](../README.md)。先关闭 Zed，运行对应平台安装器，再启动 Zed；
重复执行用于升级。使用预编译 ZIP 不需要本地 Go/Rust 工具链。

安装器下载所选 `zed-v<version>` 的 ZIP 与 `SHA256SUMS`，检查 hash 和扩展身份，在当前用户
`extensions/installed` 的相邻位置暂存后替换。相同文件重复安装不变；
只保留一个旧版本于 `extensions/.alpha-installer/argo-workflows-lsp.previous`，
失败恢复原状态，且不触碰 adapter 下载的原生服务缓存。

关闭 Zed 后，Linux/macOS 回滚命令为：

```sh
curl -fsSL https://raw.githubusercontent.com/lazysnail1024/zed-argo-workflows/main/editors/zed/install.sh | sh -s -- --rollback
```

Windows 下载 `editors/zed/install.ps1` 后，在下载目录执行
`powershell -ExecutionPolicy Bypass -File ./install.ps1 -Rollback`。
再次 rollback 交换两个保留版本；若中断导致活动目录缺失而 backup 存在，下次安装先恢复它。

离线安装先把同一 release 的 ZIP 与 `SHA256SUMS` 放到一个目录，再运行
`sh install.sh --assets-dir /path/to/assets` 或 PowerShell 的
`./install.ps1 -AssetsDir C:/path/to/assets`。
`--version VERSION` / `-Version VERSION` 选择已发布 adapter，默认版本由安装脚本固定。
也可关闭 Zed 后将 ZIP 内 `argo-workflows-lsp/` 放入 `extensions/installed`；
保留原目录用于恢复，关闭 Zed 后删除该目录即卸载。

源码开发则用 Zed 的 **Install Dev Extension** 选择 `editors/zed`，需要 Rust。
若要运行当前重构源码，还需自行构建 native 并用 `serverPath` 指向它；只重建 adapter 仍使用其 native pin。
这里的手动 alpha 安装与 extension store 是不同交付路径。

## 下载验证

从同一 release 获取 `SHA256SUMS`。Linux 把资产放在它旁边，执行
`sha256sum --check --ignore-missing SHA256SUMS`，确保所需 archive 报 `OK`；
macOS 用 `shasum -a 256 <archive>`，Windows 用 `Get-FileHash <archive> -Algorithm SHA256`，
与清单中的完整 digest 比较。同一 HTTPS 来源的校验和用于发现损坏和版本不一致，不是独立发布者签名。
保留 archive 自带的 dependency/schema/runtime 法律文本与来源记录。

## 服务选择和缓存

adapter 按顺序选择绝对 `lsp.argo-workflows-ls.settings.serverPath`、worktree shell PATH 中的
`argo-workflows-ls`、固定 native release。无效显式选择会报错。
托管下载先验证 SHA-256，再执行 identity probe，检查产品、合同、readiness 和 release 身份。

新缓存使用唯一 `servers/v<version>-<platform>.gen2-<nonce>/`，
在不变路径验证 EXE 后原子创建空 `.complete/`。未标记的新 generation 不可使用。
旧固定版本/`.gen-` 目录独立验证，不能把无标记的新目录冒充旧缓存。
每次离线复用仍检查文件类型、大小、清单、hash 和 identity；
已发布 generation 不移动或删除，并发安装可各自产生有效 generation。
失败仅尽力清理本次未完成目录，清理失败保留最初错误。
[安装器实现](../editors/zed/src/distribution.rs)

离线首次使用可解压 native archive，放入 PATH 或显式配置：

```json
{
  "lsp": {
    "argo-workflows-ls": {
      "settings": { "serverPath": "/absolute/path/to/argo-workflows-ls" }
    }
  }
}
```

Windows 使用绝对 `.exe` 路径；POSIX 解压后若执行位丢失，恢复 `chmod +x argo-workflows-ls`。
已有 `serverPath`/PATH 选择仍优先，所以升级 adapter 不会替换用户自行选择的原生服务。

## 排障与真实 Zed 验收

打开 `zed: open log`，用被选中二进制的 `version --json` 核对身份，检查固定 release 和平台资产，
修复具体配置/网络/权限问题后重启语言服务。保留通用 YAML 服务；原始 Helm 模板不会自动渲染，
需要渲染输入时使用显式 `render-check`。

Windows 的“验证后激活失败、随后临时目录无法清理”曾来自执行 EXE 后移动目录。
该问题与 YAML 校验无关；临时绕过可使用独立验证后的 EXE 和 `serverPath`，
修复原因、运行时证据边界及回归见 [经验教训](lessons-learned.md)。

自动化层次见 [CI](CI.md)。文件系统安装 workflow 明确使用 alpha.1 release fixture；
它不证明当前 candidate 的 GUI 启动。WASI 安装器测试包含真实 native probe 和受控 Windows 句柄，
也不等于完整 Zed GUI 会话。

真实编辑器验收记录 Zed/OS/架构、adapter/native hash、源码身份、设置和日志，观察：

- 干净 profile 首次启动、固定 release 下载、已有缓存的断网重启。
- 诊断、补全应用、跨文件跳转、允许与拒绝的 Rename、Quick Fix 及 undo。
- 目标版本设置、错误可见性和修复恢复，与 YAML/Helm 服务共存。
- 中断/并发安装、损坏缓存后的恢复；原有有效路径仍可使用。

不同版本、平台和产物的结果分别记录，不用本地一次通过替代其他平台或未发布候选的验证。
