//! Identity checks run before starting either a user-selected or downloaded server.

pub fn validate(bytes: &[u8], release: Option<&str>) -> Result<(), String> {
    let value: serde_json::Value = serde_json::from_slice(bytes)
        .map_err(|error| format!("Invalid server identity: {error}"))?;
    for key in [
        "product",
        "serverVersion",
        "adapterContractVersion",
        "analysisReady",
        "configSchemaVersions",
        "reportSchemaVersions",
        "toolkitVersion",
        "catalogHash",
        "sourceCommit",
        "dirty",
        "buildMode",
    ] {
        if value.get(key).is_none() {
            return Err(format!("Server identity missing {key}"));
        }
    }
    if value["product"] != "argo-workflows" || value["adapterContractVersion"] != 1 {
        return Err("Server product or adapter contract mismatch".into());
    }
    if value["analysisReady"] != true {
        return Err("Server analysis is not ready; refusing to start".into());
    }
    for field in ["configSchemaVersions", "reportSchemaVersions"] {
        if !value[field]
            .as_array()
            .is_some_and(|versions| versions.contains(&serde_json::json!(1)))
        {
            return Err(format!("Server does not support {field} version 1"));
        }
    }
    if let Some(version) = release {
        if value["serverVersion"] != version
            || value["buildMode"] != "release"
            || value["dirty"] != false
        {
            return Err(format!(
                "Downloaded server must be a clean release build of {version}"
            ));
        }
        if !value["sourceCommit"].as_str().is_some_and(|sha| {
            sha.len() == 40
                && sha
                    .bytes()
                    .all(|byte| byte.is_ascii_digit() || (b'a'..=b'f').contains(&byte))
        }) {
            return Err("Downloaded server is missing its source commit".into());
        }
    }
    Ok(())
}
