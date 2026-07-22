//! Build script: fetch the ECharts library so `render_neighborhood` can inline
//! it into a self-contained page WITHOUT vendoring 1 MB of minified JS into git.
//! It lands in `OUT_DIR` and `render_neighborhood.rs` pulls it in via
//! `include_str!(concat!(env!("OUT_DIR"), "/echarts.min.js"))`.
//!
//! Resolution order (first that works wins):
//!   1. `ECHARTS_JS_PATH=/path/to/echarts.min.js` — explicit override for
//!      offline / air-gapped / CI builds (a HARD error if set but unusable).
//!   2. a copy already cached in `OUT_DIR` — incremental builds don't re-fetch.
//!   3. download via `curl` — honors `HTTPS_PROXY` + the system CA in this env.
//!
//! If none succeed (offline, no override), a STUB is written and a
//! `cargo:warning` is emitted: the crate still builds, and `render_neighborhood`
//! detects the stub at runtime and falls back to the `--cdn` page (with a
//! notice). So a fresh clone with no network still produces a working binary —
//! it just can't emit the self-contained page until rebuilt with network.

use std::path::Path;
use std::process::Command;

const ECHARTS_URL: &str = "https://cdn.jsdelivr.net/npm/echarts@5.5.0/dist/echarts.min.js";
const STUB: &str = "/* echarts-not-bundled: build had no network and no ECHARTS_JS_PATH */";
/// Minimum plausible size of the real library (it's ~1 MB); anything smaller is
/// a truncated download or a proxy error page.
const MIN_BYTES: usize = 100_000;

fn main() {
    // Only re-run when the script itself or the override env var changes, so a
    // normal incremental build never hits the network.
    println!("cargo:rerun-if-changed=build.rs");
    println!("cargo:rerun-if-env-changed=ECHARTS_JS_PATH");

    let out_dir = std::env::var("OUT_DIR").expect("OUT_DIR is set by cargo");
    let dest = Path::new(&out_dir).join("echarts.min.js");

    // 1. Explicit override — set but unusable is a hard error (the user asked
    //    for exactly this file, so don't silently degrade to a stub).
    if let Ok(path) = std::env::var("ECHARTS_JS_PATH") {
        let bytes =
            std::fs::read(&path).unwrap_or_else(|e| panic!("ECHARTS_JS_PATH={path} unreadable: {e}"));
        let bytes = validate(bytes, &path).unwrap_or_else(|e| panic!("{e}"));
        std::fs::write(&dest, &bytes).expect("write echarts to OUT_DIR");
        return;
    }

    match cached_or_download(&dest) {
        Ok(bytes) => std::fs::write(&dest, &bytes).expect("write echarts to OUT_DIR"),
        Err(reason) => {
            println!(
                "cargo:warning=ECharts not bundled ({reason}); render_neighborhood will emit the \
                 --cdn page instead. Set ECHARTS_JS_PATH=/path/to/echarts.min.js or build with \
                 network for a self-contained page."
            );
            std::fs::write(&dest, STUB).expect("write echarts stub to OUT_DIR");
        }
    }
}

fn cached_or_download(dest: &Path) -> Result<Vec<u8>, String> {
    // 2. Reuse a good copy already in OUT_DIR (incremental build, no network).
    if let Ok(meta) = std::fs::metadata(dest) {
        if meta.len() as usize >= MIN_BYTES {
            return std::fs::read(dest).map_err(|e| e.to_string());
        }
    }
    // 3. Download via curl.
    let out = Command::new("curl")
        .args(["-fsSL", "--retry", "3", ECHARTS_URL])
        .output()
        .map_err(|e| format!("could not run curl: {e}"))?;
    if !out.status.success() {
        return Err(format!("curl exited {:?} for {ECHARTS_URL}", out.status.code()));
    }
    validate(out.stdout, ECHARTS_URL)
}

/// Guard against a truncated download or an HTML error page masquerading as JS.
fn validate(bytes: Vec<u8>, src: &str) -> Result<Vec<u8>, String> {
    if bytes.len() < MIN_BYTES {
        return Err(format!("{src} is too small ({} bytes)", bytes.len()));
    }
    let head = String::from_utf8_lossy(&bytes[..bytes.len().min(2000)]);
    if !(head.contains("Apache Software Foundation") || head.contains("echarts")) {
        return Err(format!(
            "{src} does not look like echarts.min.js (proxy error page?)"
        ));
    }
    Ok(bytes)
}
