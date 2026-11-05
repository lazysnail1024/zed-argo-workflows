#[cfg(not(test))]
mod adapter;
mod distribution;
mod identity;

// Release metadata lets the packager reject stale or mismatched WASM bytes.
#[cfg(target_arch = "wasm32")]
const RELEASE_IDENTITY: &str = concat!(
    "argo-workflows-lsp\n",
    env!("CARGO_PKG_VERSION"),
    "\n",
    include_str!("../native-server-version")
);

#[cfg(target_arch = "wasm32")]
const fn release_bytes<const N: usize>(value: &str) -> [u8; N] {
    let mut bytes = [0; N];
    bytes.copy_from_slice(value.as_bytes());
    bytes
}

#[cfg(target_arch = "wasm32")]
#[unsafe(link_section = "argo:release")]
#[used]
pub static RELEASE_METADATA: [u8; RELEASE_IDENTITY.len()] = release_bytes(RELEASE_IDENTITY);
