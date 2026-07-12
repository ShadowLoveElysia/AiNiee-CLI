#![cfg_attr(not(debug_assertions), windows_subsystem = "windows")]

use std::fs::{self, OpenOptions};
use std::net::TcpStream;
use std::path::{Path, PathBuf};
use std::process::{Child, Command, Stdio};
use std::sync::Mutex;
use std::thread;
use std::time::{Duration, Instant};

use rfd::{MessageButtons, MessageDialog, MessageDialogResult, MessageLevel};
use tauri::{Manager, RunEvent, WebviewUrl, WebviewWindowBuilder};

struct BackendState {
    child: Mutex<Option<Child>>,
}

#[derive(Debug, Clone)]
struct RuntimeContext {
    project_root: PathBuf,
    host_script: PathBuf,
    uv_path: Option<PathBuf>,
}

impl Default for BackendState {
    fn default() -> Self {
        Self {
            child: Mutex::new(None),
        }
    }
}

fn normalize_dialog_title(title: Option<String>) -> String {
    match title {
        Some(value) if !value.trim().is_empty() => value,
        _ => "AiNiee".to_string(),
    }
}

#[tauri::command]
fn show_native_alert(
    message: String,
    title: Option<String>,
    level: Option<String>,
) -> Result<bool, String> {
    let dialog_title = normalize_dialog_title(title);
    let level = level
        .unwrap_or_else(|| "info".to_string())
        .trim()
        .to_lowercase();

    let dialog_level = match level.as_str() {
        "error" => MessageLevel::Error,
        "warning" => MessageLevel::Warning,
        _ => MessageLevel::Info,
    };

    MessageDialog::new()
        .set_title(dialog_title)
        .set_description(message)
        .set_level(dialog_level)
        .set_buttons(MessageButtons::Ok)
        .show();

    Ok(true)
}

#[tauri::command]
fn show_native_confirm(
    message: String,
    title: Option<String>,
) -> Result<bool, String> {
    let dialog_title = normalize_dialog_title(title);
    let result = MessageDialog::new()
        .set_title(dialog_title)
        .set_description(message)
        .set_level(MessageLevel::Warning)
        .set_buttons(MessageButtons::OkCancel)
        .show();

    Ok(matches!(
        result,
        MessageDialogResult::Ok | MessageDialogResult::Yes
    ))
}

fn io_error(message: impl Into<String>) -> std::io::Error {
    std::io::Error::new(std::io::ErrorKind::Other, message.into())
}

fn parse_port() -> u16 {
    std::env::var("AINIEE_GUI_PORT")
        .ok()
        .and_then(|v| v.parse::<u16>().ok())
        .unwrap_or(18000)
}

fn read_text_if_exists(path: &Path) -> Option<String> {
    fs::read_to_string(path).ok()
}

fn copy_dir_recursive(source: &Path, destination: &Path) -> Result<(), String> {
    fs::create_dir_all(destination)
        .map_err(|e| format!("Failed to create {}: {e}", destination.display()))?;

    for entry in fs::read_dir(source)
        .map_err(|e| format!("Failed to read directory {}: {e}", source.display()))?
    {
        let entry = entry.map_err(|e| format!("Failed to read directory entry: {e}"))?;
        let source_path = entry.path();
        let destination_path = destination.join(entry.file_name());
        let file_type = entry
            .file_type()
            .map_err(|e| format!("Failed to read file type for {}: {e}", source_path.display()))?;

        if file_type.is_dir() {
            copy_dir_recursive(&source_path, &destination_path)?;
        } else if file_type.is_file() {
            if let Some(parent) = destination_path.parent() {
                fs::create_dir_all(parent).map_err(|e| {
                    format!("Failed to create parent directory {}: {e}", parent.display())
                })?;
            }
            fs::copy(&source_path, &destination_path).map_err(|e| {
                format!(
                    "Failed to copy {} to {}: {e}",
                    source_path.display(),
                    destination_path.display()
                )
            })?;
        }
    }

    Ok(())
}

fn runtime_manifest_text(root: &Path) -> Option<String> {
    read_text_if_exists(&root.join("runtime_manifest.json"))
}

fn sync_packaged_runtime(packaged_root: &Path, runtime_root: &Path) -> Result<(), String> {
    let packaged_manifest = runtime_manifest_text(packaged_root)
        .ok_or_else(|| format!("Missing runtime_manifest.json in {}", packaged_root.display()))?;
    let local_manifest = runtime_manifest_text(runtime_root);
    let refresh_required = !runtime_root.exists() || local_manifest.as_deref() != Some(packaged_manifest.as_str());

    if refresh_required {
        if runtime_root.exists() {
            fs::remove_dir_all(runtime_root).map_err(|e| {
                format!(
                    "Failed to remove stale runtime directory {}: {e}",
                    runtime_root.display()
                )
            })?;
        }
        copy_dir_recursive(packaged_root, runtime_root)?;
    }

    Ok(())
}

fn ensure_runtime_directories(project_root: &Path) -> Result<(), String> {
    for relative_path in [
        "output",
        "output/cache",
        "output/logs",
        "output/temp_edit",
        "updatetemp",
    ] {
        let full_path = project_root.join(relative_path);
        fs::create_dir_all(&full_path)
            .map_err(|e| format!("Failed to create {}: {e}", full_path.display()))?;
    }
    Ok(())
}

fn is_dir_writable(path: &Path) -> bool {
    if fs::create_dir_all(path).is_err() {
        return false;
    }

    let test_file = path.join(".ainiee_write_test");
    match fs::write(&test_file, b"ok") {
        Ok(_) => {
            let _ = fs::remove_file(test_file);
            true
        }
        Err(_) => false,
    }
}

fn resolve_runtime_root(app_handle: &tauri::AppHandle) -> Result<PathBuf, String> {
    if let Ok(custom_runtime_dir) = std::env::var("AINIEE_RUNTIME_DIR") {
        let trimmed = custom_runtime_dir.trim();
        if !trimmed.is_empty() {
            let path = PathBuf::from(trimmed);
            if is_dir_writable(&path) {
                return Ok(path);
            }
            return Err(format!(
                "AINIEE_RUNTIME_DIR is not writable: {}",
                path.display()
            ));
        }
    }

    if let Ok(current_exe) = std::env::current_exe() {
        if let Some(exe_dir) = current_exe.parent() {
            let runtime_root = exe_dir.join("ainiee-runtime");
            if is_dir_writable(&runtime_root) {
                return Ok(runtime_root);
            }
        }
    }

    let local_data_dir = app_handle
        .path()
        .app_local_data_dir()
        .map_err(|e| format!("Failed to resolve app local data directory: {e}"))?;
    let runtime_root = local_data_dir.join("ainiee-runtime");
    if is_dir_writable(&runtime_root) {
        return Ok(runtime_root);
    }

    Err(format!(
        "No writable runtime directory found. Checked executable directory and {}",
        runtime_root.display()
    ))
}

fn uv_executable_name() -> &'static str {
    if cfg!(target_os = "windows") {
        "uv.exe"
    } else {
        "uv"
    }
}

fn command_works(command_path: &Path) -> bool {
    Command::new(command_path)
        .arg("--version")
        .stdin(Stdio::null())
        .stdout(Stdio::null())
        .stderr(Stdio::null())
        .status()
        .map(|status| status.success())
        .unwrap_or(false)
}

fn candidate_uv_paths() -> Vec<PathBuf> {
    let mut candidates = Vec::new();
    let uv_name = uv_executable_name();

    if let Ok(cargo_home) = std::env::var("CARGO_HOME") {
        candidates.push(PathBuf::from(cargo_home).join("bin").join(uv_name));
    }

    if let Ok(user_profile) = std::env::var("USERPROFILE") {
        candidates.push(PathBuf::from(user_profile).join(".cargo").join("bin").join(uv_name));
    }

    if let Ok(home) = std::env::var("HOME") {
        candidates.push(PathBuf::from(home).join(".cargo").join("bin").join(uv_name));
    }

    candidates
}

fn locate_uv() -> Option<PathBuf> {
    if command_works(Path::new("uv")) {
        return Some(PathBuf::from("uv"));
    }

    for candidate in candidate_uv_paths() {
        if candidate.exists() && command_works(&candidate) {
            return Some(candidate);
        }
    }

    None
}

fn install_uv() -> Result<(), String> {
    #[cfg(target_os = "windows")]
    {
        let status = Command::new("powershell")
            .args([
                "-NoProfile",
                "-ExecutionPolicy",
                "Bypass",
                "-Command",
                "irm https://astral.sh/uv/install.ps1 | iex",
            ])
            .stdin(Stdio::null())
            .status()
            .map_err(|e| format!("Failed to execute uv installer: {e}"))?;

        if !status.success() {
            return Err(format!("uv installer failed with status: {status}"));
        }
    }

    #[cfg(not(target_os = "windows"))]
    {
        let status = Command::new("sh")
            .args(["-c", "curl -LsSf https://astral.sh/uv/install.sh | sh"])
            .stdin(Stdio::null())
            .status()
            .map_err(|e| format!("Failed to execute uv installer: {e}"))?;

        if !status.success() {
            return Err(format!("uv installer failed with status: {status}"));
        }
    }

    Ok(())
}

fn ensure_uv_available() -> Result<PathBuf, String> {
    if let Some(uv_path) = locate_uv() {
        return Ok(uv_path);
    }

    install_uv()?;

    locate_uv().ok_or_else(|| {
        "uv installation command completed but uv was not found in PATH or cargo bin.".to_string()
    })
}

fn sync_runtime_dependencies(project_root: &Path, uv_path: &Path) -> Result<(), String> {
    let manifest_content = runtime_manifest_text(project_root).unwrap_or_default();
    let marker_path = project_root.join(".uv_sync_marker");
    let marker_content = read_text_if_exists(&marker_path).unwrap_or_default();
    let marker_matches = marker_content == manifest_content;
    let venv_exists = project_root.join(".venv").exists();

    if marker_matches && venv_exists {
        return Ok(());
    }

    let status = Command::new(uv_path)
        .args(["sync", "--frozen"])
        .current_dir(project_root)
        .stdin(Stdio::null())
        .status()
        .map_err(|e| format!("Failed to run `uv sync --frozen`: {e}"))?;

    if !status.success() {
        let fallback = Command::new(uv_path)
            .arg("sync")
            .current_dir(project_root)
            .stdin(Stdio::null())
            .status()
            .map_err(|e| format!("Failed to run fallback `uv sync`: {e}"))?;

        if !fallback.success() {
            return Err(format!(
                "Dependency installation failed (uv sync exit status: {status}, fallback status: {fallback})."
            ));
        }
    }

    fs::write(&marker_path, manifest_content)
        .map_err(|e| format!("Failed to write sync marker {}: {e}", marker_path.display()))?;

    Ok(())
}

fn resolve_packaged_context(app_handle: &tauri::AppHandle) -> Result<Option<RuntimeContext>, String> {
    let resource_dir = match app_handle.path().resource_dir() {
        Ok(path) => path,
        Err(_) => return Ok(None),
    };

    let packaged_runtime = resource_dir.join("ainiee-runtime");
    if !packaged_runtime.join("runtime_manifest.json").exists() {
        return Ok(None);
    }

    let runtime_root = resolve_runtime_root(app_handle)?;

    sync_packaged_runtime(&packaged_runtime, &runtime_root)?;
    ensure_runtime_directories(&runtime_root)?;

    let uv_path = ensure_uv_available()?;
    sync_runtime_dependencies(&runtime_root, &uv_path)?;

    let host_script = runtime_root
        .join("Tools")
        .join("TauriShell")
        .join("tauri_web_host.py");
    if !host_script.exists() {
        return Err(format!("Missing host script: {}", host_script.display()));
    }

    Ok(Some(RuntimeContext {
        project_root: runtime_root,
        host_script,
        uv_path: Some(uv_path),
    }))
}

fn resolve_dev_context() -> Result<RuntimeContext, String> {
    let shell_dir = PathBuf::from(env!("CARGO_MANIFEST_DIR"))
        .parent()
        .ok_or("Failed to resolve shell directory.")?
        .to_path_buf();

    let project_root = shell_dir
        .parent()
        .and_then(|path| path.parent())
        .ok_or("Failed to resolve development project root.")?
        .to_path_buf();

    let host_script = shell_dir.join("tauri_web_host.py");
    if !host_script.exists() {
        return Err(format!("Missing host script: {}", host_script.display()));
    }

    Ok(RuntimeContext {
        project_root,
        host_script,
        uv_path: locate_uv(),
    })
}

fn resolve_runtime_context(app_handle: &tauri::AppHandle) -> Result<RuntimeContext, String> {
    if let Some(packaged_context) = resolve_packaged_context(app_handle)? {
        return Ok(packaged_context);
    }

    resolve_dev_context()
}

fn backend_startup_log_path(context: &RuntimeContext) -> PathBuf {
    context.project_root.join("logs").join("backend-startup.log")
}

fn prepare_backend_startup_log(log_path: &Path) -> Result<(), String> {
    if let Some(parent) = log_path.parent() {
        fs::create_dir_all(parent)
            .map_err(|e| format!("Failed to create backend log directory {}: {e}", parent.display()))?;
    }
    fs::write(log_path, b"")
        .map_err(|e| format!("Failed to initialize backend log {}: {e}", log_path.display()))
}

fn backend_log_stdio(log_path: &Path) -> Result<(Stdio, Stdio), String> {
    let output = OpenOptions::new()
        .create(true)
        .append(true)
        .open(log_path)
        .map_err(|e| format!("Failed to open backend log {}: {e}", log_path.display()))?;
    let error = output
        .try_clone()
        .map_err(|e| format!("Failed to clone backend log handle: {e}"))?;

    // Keep both streams because Python import failures are written to stderr while uv uses stdout.
    Ok((Stdio::from(output), Stdio::from(error)))
}

fn spawn_backend_with_uv(
    context: &RuntimeContext,
    port: u16,
    uv_path: &Path,
    log_path: &Path,
) -> Result<Child, String> {
    let port_arg = port.to_string();
    let (stdout, stderr) = backend_log_stdio(log_path)?;
    let mut command = Command::new(uv_path);
    command
        .arg("run")
        .arg(&context.host_script)
        .arg("--port")
        .arg(&port_arg)
        .current_dir(&context.project_root)
        .stdin(Stdio::null())
        .stdout(stdout)
        .stderr(stderr);

    command
        .spawn()
        .map_err(|e| format!("Failed to start backend with uv: {e}"))
}

fn spawn_backend_with_python(context: &RuntimeContext, port: u16, log_path: &Path) -> Result<Child, String> {
    let mut last_error = String::new();
    let port_arg = port.to_string();

    for python_cmd in ["python", "python3"] {
        let (stdout, stderr) = backend_log_stdio(log_path)?;
        let mut command = Command::new(python_cmd);
        command
            .arg(&context.host_script)
            .arg("--port")
            .arg(&port_arg)
            .current_dir(&context.project_root)
            .stdin(Stdio::null())
            .stdout(stdout)
            .stderr(stderr);

        match command.spawn() {
            Ok(child) => return Ok(child),
            Err(err) => {
                last_error = format!("{python_cmd}: {err}");
            }
        }
    }

    Err(format!(
        "Failed to start backend with python fallback. Last error: {last_error}"
    ))
}

fn spawn_backend(context: &RuntimeContext, port: u16, log_path: &Path) -> Result<Child, String> {
    let uv_error = if let Some(uv_path) = context.uv_path.as_deref() {
        match spawn_backend_with_uv(context, port, uv_path, log_path) {
            Ok(child) => return Ok(child),
            Err(err) => Some(err),
        }
    } else {
        None
    };

    match spawn_backend_with_python(context, port, log_path) {
        Ok(child) => Ok(child),
        Err(py_err) => {
            if let Some(uv_err) = uv_error {
                Err(format!("uv error: {uv_err}; python error: {py_err}"))
            } else {
                Err(py_err)
            }
        }
    }
}

fn wait_for_backend(child: &mut Child, port: u16, timeout: Duration) -> Result<(), String> {
    let start = Instant::now();
    while start.elapsed() < timeout {
        if TcpStream::connect(("127.0.0.1", port)).is_ok() {
            return Ok(());
        }
        if let Some(status) = child
            .try_wait()
            .map_err(|e| format!("Failed to inspect backend process: {e}"))?
        {
            return Err(format!("Backend process exited before becoming ready ({status})."));
        }
        thread::sleep(Duration::from_millis(200));
    }
    Err(format!(
        "Backend did not become ready at http://127.0.0.1:{port} within timeout."
    ))
}

fn read_log_tail(log_path: &Path, max_lines: usize) -> String {
    let content = read_text_if_exists(log_path).unwrap_or_default();
    let lines: Vec<&str> = content.lines().collect();
    let start = lines.len().saturating_sub(max_lines);
    lines[start..].join("\n")
}

fn show_backend_startup_error(error: &str, log_path: &Path) {
    // A short tail keeps the native dialog useful even when dependency setup produced a long log.
    let tail = read_log_tail(log_path, 24);
    let detail = if tail.trim().is_empty() {
        error.to_string()
    } else {
        format!("{error}\n\n{tail}")
    };
    let message = format!("{detail}\n\nLog: {}", log_path.display());
    MessageDialog::new()
        .set_title("AiNiee startup failed")
        .set_description(message)
        .set_level(MessageLevel::Error)
        .set_buttons(MessageButtons::Ok)
        .show();
}

fn show_startup_error(error: &str) {
    MessageDialog::new()
        .set_title("AiNiee startup failed")
        .set_description(error)
        .set_level(MessageLevel::Error)
        .set_buttons(MessageButtons::Ok)
        .show();
}

fn stop_backend(app_handle: &tauri::AppHandle) {
    let state = app_handle.state::<BackendState>();
    let mut child_slot = match state.child.lock() {
        Ok(guard) => guard,
        Err(_) => return,
    };

    if let Some(child) = child_slot.as_mut() {
        let _ = child.kill();
        let _ = child.wait();
    }
    *child_slot = None;
}

fn main() {
    tauri::Builder::default()
        .invoke_handler(tauri::generate_handler![
            show_native_alert,
            show_native_confirm
        ])
        .manage(BackendState::default())
        .setup(|app| {
            let app_handle = app.handle().clone();
            let port = parse_port();
            let runtime_context = resolve_runtime_context(&app_handle).map_err(|error| {
                show_startup_error(&error);
                io_error(error)
            })?;
            let log_path = backend_startup_log_path(&runtime_context);
            prepare_backend_startup_log(&log_path).map_err(|error| {
                show_startup_error(&error);
                io_error(error)
            })?;
            let mut child = spawn_backend(&runtime_context, port, &log_path).map_err(|error| {
                show_backend_startup_error(&error, &log_path);
                io_error(error)
            })?;

            if let Err(error) = wait_for_backend(&mut child, port, Duration::from_secs(20)) {
                let _ = child.kill();
                let _ = child.wait();
                show_backend_startup_error(&error, &log_path);
                return Err(io_error(error).into());
            }

            {
                let state = app.state::<BackendState>();
                let mut child_slot = state
                    .child
                    .lock()
                    .map_err(|_| io_error("Failed to lock backend child state"))?;
                *child_slot = Some(child);
            }

            let target_url = format!("http://127.0.0.1:{port}");
            let url = target_url
                .parse()
                .map_err(|e| io_error(format!("Invalid URL {target_url}: {e}")))?;

            WebviewWindowBuilder::new(app, "main", WebviewUrl::External(url))
                .title("AiNiee GUI (Tauri PoC)")
                .inner_size(1280.0, 860.0)
                .min_inner_size(1024.0, 680.0)
                .build()
                .map_err(|e| io_error(format!("Failed to create window: {e}")))?;

            Ok(())
        })
        .build(tauri::generate_context!())
        .expect("error while building tauri application")
        .run(|app_handle, event| match event {
            RunEvent::ExitRequested { .. } | RunEvent::Exit => {
                stop_backend(app_handle);
            }
            _ => {}
        });
}
