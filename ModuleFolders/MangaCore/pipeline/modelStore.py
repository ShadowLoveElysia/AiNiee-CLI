from __future__ import annotations

import inspect
import json
import os
import re
import threading
from datetime import datetime
from pathlib import Path
from typing import Callable

from ModuleFolders.MangaCore.bridge.providerAdapter import (
    download_runtime_assets,
    get_detect_runtime_ids,
    get_inpaint_runtime_id,
    get_ocr_runtime_id,
    get_runtime_asset_status,
    runtime_device_status_from_config,
)
from ModuleFolders.MangaCore.pipeline.modelCatalog import (
    DEFAULT_OCR_MODEL_ID,
    get_model_preset,
    get_model_package,
    list_model_presets,
    list_model_packages,
    normalize_model_id,
)

_DIRECT_REQUIRED_FILES: dict[str, tuple[str, ...]] = {
    "comic-text-detector": (
        "yolo-v5.safetensors",
        "unet.safetensors",
        "dbnet.safetensors",
    ),
}
_DEFAULT_HF_ENDPOINT = "https://hf-mirror.com"
MANGA_ENGINE_STAGE_CONFIG_KEYS: dict[str, str] = {
    "detect": "manga_detect_engine",
    "segment": "manga_segment_engine",
    "ocr": "manga_ocr_engine",
    "inpaint": "manga_inpaint_engine",
}
_RUNTIME_OPTIONAL_ENGINE_STAGES = {"segment"}
DownloadProgressCallback = Callable[[dict[str, object]], None]


def _now_iso() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")


def _safe_name(value: str) -> str:
    return re.sub(r"[^a-z0-9._-]+", "-", value.lower()).strip("-") or "model"


def _quarantine_corrupt_json(path: Path) -> None:
    if not path.exists():
        return
    timestamp = datetime.now().strftime("%Y%m%d%H%M%S")
    quarantine_path = path.with_name(f"{path.name}.corrupt-{timestamp}")
    suffix = 1
    while quarantine_path.exists():
        quarantine_path = path.with_name(f"{path.name}.corrupt-{timestamp}-{suffix}")
        suffix += 1
    try:
        path.replace(quarantine_path)
    except OSError:
        return


def _atomic_write_json(path: Path, payload: dict[str, object]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp_path = path.with_name(f"{path.name}.tmp")
    with open(temp_path, "w", encoding="utf-8") as handle:
        json.dump(payload, handle, ensure_ascii=False, indent=2)
        handle.write("\n")
        handle.flush()
        os.fsync(handle.fileno())
    os.replace(temp_path, path)


def _ensure_huggingface_endpoint() -> str:
    endpoint = (os.environ.get("HF_ENDPOINT") or _DEFAULT_HF_ENDPOINT).rstrip("/")
    os.environ["HF_ENDPOINT"] = endpoint
    os.environ.setdefault("HF_HUB_ENDPOINT", endpoint)
    return endpoint


def _snapshot_progress_class(progress_callback: DownloadProgressCallback | None = None):
    class SnapshotProgress:
        _lock = threading.RLock()

        def __init__(self, iterable=None, *args, **kwargs) -> None:
            self.iterable = iterable
            self.total = int(kwargs.get("total") or 0)
            self.n = int(kwargs.get("initial") or 0)
            self.description = str(kwargs.get("desc") or "Hugging Face snapshot")

        def __enter__(self):
            self._emit()
            return self

        def __exit__(self, _exc_type: object, _exc: object, _traceback: object) -> None:
            return None

        def __iter__(self):
            if self.iterable is None:
                return iter(())
            for item in self.iterable:
                yield item
                self.update(1)

        def update(self, n: int = 1, *args, **kwargs) -> None:
            self.n += int(n or 0)
            self._emit()

        def close(self) -> None:
            return None

        def refresh(self, *args, **kwargs) -> None:
            return None

        def reset(self, total: int | None = None, *args, **kwargs) -> None:
            if total is not None:
                self.total = int(total or 0)
            self.n = 0
            self._emit()

        def set_description(self, desc: str | None = None, *args, **kwargs) -> None:
            if desc:
                self.description = str(desc)
                self._emit()

        def _emit(self) -> None:
            if progress_callback is None:
                return
            try:
                progress_callback(
                    {
                        "kind": "manga_model_download",
                        "stage": "snapshot",
                        "message": self.description,
                        "bytes_downloaded": self.n,
                        "bytes_total": self.total,
                        "unit": "files",
                    }
                )
            except Exception:
                return

        @classmethod
        def get_lock(cls):
            return cls._lock

        @classmethod
        def set_lock(cls, lock) -> None:
            cls._lock = lock

    return SnapshotProgress


def _snapshot_download(
    repo_id: str,
    cache_dir: str,
    *,
    quiet: bool = False,
    progress_callback: DownloadProgressCallback | None = None,
) -> str:
    endpoint = _ensure_huggingface_endpoint()
    try:
        from huggingface_hub import snapshot_download
    except ModuleNotFoundError as exc:
        raise RuntimeError(
            "huggingface-hub is required only when downloading non-runtime MangaCore "
            "model packages with Hugging Face snapshots. Runtime-backed default models "
            "use the dedicated MangaCore model downloader."
        ) from exc

    kwargs: dict[str, object] = {
        "repo_id": repo_id,
        "cache_dir": cache_dir,
        "local_files_only": False,
    }
    try:
        signature = inspect.signature(snapshot_download)
        supports_kwargs = any(
            parameter.kind == inspect.Parameter.VAR_KEYWORD
            for parameter in signature.parameters.values()
        )
        if "endpoint" in signature.parameters or supports_kwargs:
            kwargs["endpoint"] = endpoint
        if quiet and ("tqdm_class" in signature.parameters or supports_kwargs):
            kwargs["tqdm_class"] = _snapshot_progress_class(progress_callback)
    except (TypeError, ValueError):
        pass
    return str(snapshot_download(**kwargs))


class MangaModelStore:
    def __init__(self, root_dir: str | Path | None = None) -> None:
        if root_dir is None:
            root_dir = Path(__file__).resolve().parents[3] / "Resource" / "Models" / "MangaCore"
        self.root_dir = Path(root_dir)

    def huggingface_cache_dir(self) -> Path:
        return self.root_dir / "huggingface"

    def registry_dir(self) -> Path:
        return self.root_dir / "registry"

    def registry_path(self, model_id: str) -> Path:
        model_id = normalize_model_id(model_id)
        return self.registry_dir() / f"{_safe_name(model_id)}.json"

    def local_direct_snapshot_path(self, model_id: str) -> Path | None:
        model_id = normalize_model_id(model_id)
        package = get_model_package(model_id)
        candidate = self.huggingface_cache_dir() / package.repo_id
        required_files = _DIRECT_REQUIRED_FILES.get(package.model_id)
        if required_files:
            if all((candidate / filename).is_file() for filename in required_files):
                return candidate
            return None
        if candidate.exists():
            return candidate
        return None

    def get_status(self, model_id: str) -> dict[str, object]:
        model_id = normalize_model_id(model_id)
        package = get_model_package(model_id)
        record = self._read_record(model_id)
        snapshot_path = Path(record.get("snapshot_path", "")) if record.get("snapshot_path") else None
        runtime_status = get_runtime_asset_status(model_id, self.root_dir)
        if not snapshot_path and runtime_status.available and runtime_status.storage_path:
            snapshot_path = Path(runtime_status.storage_path)
        if runtime_status.supported:
            available = runtime_status.available
        else:
            if not snapshot_path or not snapshot_path.exists():
                snapshot_path = self.local_direct_snapshot_path(model_id)
            available = bool(snapshot_path and snapshot_path.exists())
        payload = package.to_dict()
        payload.update(
            {
                "available": available,
                "storage_root": str(self.root_dir),
                "cache_dir": str(self.huggingface_cache_dir()),
                "snapshot_path": str(snapshot_path) if snapshot_path else "",
                "downloaded_at": str(record.get("downloaded_at", "")),
                "revision": str(record.get("revision", "")),
                "runtime_supported": runtime_status.supported,
                "runtime_assets_path": runtime_status.storage_path,
                "runtime_engine_id": runtime_status.runtime_engine_id,
            }
        )
        return payload

    def list_statuses(self) -> list[dict[str, object]]:
        return [self.get_status(package.model_id) for package in list_model_packages()]

    def build_manager_manifest(self) -> dict[str, object]:
        statuses = self.list_statuses()
        statuses_by_id = {str(status.get("model_id") or ""): status for status in statuses}
        presets: list[dict[str, object]] = []
        for preset in list_model_presets():
            preset_payload = preset.to_dict()
            model_ids = [normalize_model_id(model_id) for model_id in preset.model_ids]
            preset_statuses = [statuses_by_id[model_id] for model_id in model_ids if model_id in statuses_by_id]
            available_ids = [
                model_id
                for model_id in model_ids
                if bool(statuses_by_id.get(model_id, {}).get("available"))
            ]
            missing_ids = [model_id for model_id in model_ids if model_id not in available_ids]
            preset_payload.update(
                {
                    "model_ids": model_ids,
                    "models": preset_statuses,
                    "available_model_ids": available_ids,
                    "missing_model_ids": missing_ids,
                    "available": not missing_ids,
                    "missing_count": len(missing_ids),
                    "model_count": len(model_ids),
                }
            )
            presets.append(preset_payload)
        available_model_ids = [
            str(status.get("model_id"))
            for status in statuses
            if status.get("model_id") and bool(status.get("available"))
        ]
        missing_model_ids = [
            str(status.get("model_id"))
            for status in statuses
            if status.get("model_id") and not bool(status.get("available"))
        ]
        return {
            "default_ocr_model_id": DEFAULT_OCR_MODEL_ID,
            "presets": presets,
            "models": statuses,
            "engine_options": self._build_engine_options(statuses),
            "model_ids": [str(status.get("model_id")) for status in statuses if status.get("model_id")],
            "available_model_ids": available_model_ids,
            "missing_model_ids": missing_model_ids,
            "available": not missing_model_ids,
            "missing_count": len(missing_model_ids),
            "model_count": len(statuses),
        }

    def _build_engine_options(self, statuses: list[dict[str, object]]) -> dict[str, list[dict[str, object]]]:
        options_by_stage: dict[str, list[dict[str, object]]] = {
            stage: [] for stage in MANGA_ENGINE_STAGE_CONFIG_KEYS
        }
        for status in statuses:
            stage = str(status.get("stage") or "")
            config_key = MANGA_ENGINE_STAGE_CONFIG_KEYS.get(stage)
            if not config_key:
                continue

            runtime_supported = bool(status.get("runtime_supported"))
            runtime_selectable = runtime_supported or stage in _RUNTIME_OPTIONAL_ENGINE_STAGES
            available = bool(status.get("available"))
            disabled_reason = ""
            if not available:
                disabled_reason = "missing"
            elif not runtime_selectable:
                disabled_reason = "unsupported_runtime"

            option = dict(status)
            option.update(
                {
                    "config_key": config_key,
                    "selectable": available and runtime_selectable,
                    "runtime_selectable": runtime_selectable,
                    "disabled_reason": disabled_reason,
                }
            )
            options_by_stage[stage].append(option)

        def sort_key(option: dict[str, object]) -> tuple[int, str, str]:
            return (
                0 if option.get("selectable") else 1,
                str(option.get("hardware_tier") or ""),
                str(option.get("model_id") or ""),
            )

        return {
            stage: sorted(options, key=sort_key)
            for stage, options in options_by_stage.items()
        }

    def register_downloaded_snapshot(
        self,
        model_id: str,
        snapshot_path: str | Path,
        *,
        revision: str = "",
    ) -> dict[str, object]:
        model_id = normalize_model_id(model_id)
        package = get_model_package(model_id)
        snapshot = Path(snapshot_path).resolve()
        self._write_record(
            model_id,
            {
                "model_id": package.model_id,
                "repo_id": package.repo_id,
                "repo_url": package.repo_url,
                "snapshot_path": str(snapshot),
                "downloaded_at": _now_iso(),
                "revision": revision,
            },
        )
        return self.get_status(model_id)

    def download(
        self,
        model_id: str,
        *,
        quiet: bool = False,
        progress_callback: DownloadProgressCallback | None = None,
    ) -> dict[str, object]:
        model_id = normalize_model_id(model_id)
        package = get_model_package(model_id)
        runtime_status = get_runtime_asset_status(model_id, self.root_dir)
        if runtime_status.supported:
            if progress_callback is not None:
                progress_callback(
                    {
                        "kind": "manga_model_download",
                        "stage": "model_started",
                        "message": f"Preparing model package: {package.display_name}",
                        "model_id": model_id,
                        "display_name": package.display_name,
                    }
                )
            downloaded_runtime_status = download_runtime_assets(
                model_id,
                self.root_dir,
                quiet=quiet,
                progress_callback=progress_callback,
            )
            if downloaded_runtime_status is None or not downloaded_runtime_status.available:
                raise RuntimeError(f"Failed to prepare runtime assets for manga model: {model_id}")
            status = self.register_downloaded_snapshot(
                model_id,
                downloaded_runtime_status.storage_path,
                revision=f"runtime:{downloaded_runtime_status.runtime_engine_id}",
            )
            if progress_callback is not None:
                progress_callback(
                    {
                        "kind": "manga_model_download",
                        "stage": "model_completed",
                        "message": f"Prepared model package: {package.display_name}",
                        "model_id": model_id,
                        "display_name": package.display_name,
                    }
                )
            return status

        self.huggingface_cache_dir().mkdir(parents=True, exist_ok=True)
        if progress_callback is not None:
            progress_callback(
                {
                    "kind": "manga_model_download",
                    "stage": "snapshot",
                    "message": f"Downloading model snapshot: {package.display_name}",
                    "model_id": model_id,
                    "display_name": package.display_name,
                    "repo_id": package.repo_id,
                }
            )
        snapshot_path = _snapshot_download(
            package.repo_id,
            str(self.huggingface_cache_dir()),
            quiet=quiet,
            progress_callback=progress_callback,
        )
        status = self.register_downloaded_snapshot(
            model_id,
            snapshot_path,
            revision="downloaded",
        )
        if progress_callback is not None:
            progress_callback(
                {
                    "kind": "manga_model_download",
                    "stage": "model_completed",
                    "message": f"Prepared model package: {package.display_name}",
                    "model_id": model_id,
                    "display_name": package.display_name,
                }
            )
        return status

    def download_preset(
        self,
        preset_id: str,
        *,
        quiet: bool = False,
        progress_callback: DownloadProgressCallback | None = None,
    ) -> dict[str, object]:
        preset = get_model_preset(preset_id)
        statuses: list[dict[str, object]] = []
        for model_id in preset.model_ids:
            statuses.append(self.download(model_id, quiet=quiet, progress_callback=progress_callback))
        return {
            "preset": preset.to_dict(),
            "models": statuses,
            "config_overrides": dict(preset.config_overrides),
        }

    def download_all(
        self,
        *,
        quiet: bool = False,
        progress_callback: DownloadProgressCallback | None = None,
    ) -> dict[str, object]:
        statuses: list[dict[str, object]] = []
        for package in list_model_packages():
            status = self.get_status(package.model_id)
            if not status.get("available"):
                status = self.download(package.model_id, quiet=quiet, progress_callback=progress_callback)
            statuses.append(status)
        available_model_ids = [
            str(status.get("model_id"))
            for status in statuses
            if status.get("model_id") and bool(status.get("available"))
        ]
        missing_model_ids = [
            str(status.get("model_id"))
            for status in statuses
            if status.get("model_id") and not bool(status.get("available"))
        ]
        return {
            "models": statuses,
            "model_ids": [str(status.get("model_id")) for status in statuses if status.get("model_id")],
            "available_model_ids": available_model_ids,
            "missing_model_ids": missing_model_ids,
            "available": not missing_model_ids,
            "missing_count": len(missing_model_ids),
            "model_count": len(statuses),
            "config_overrides": {},
        }

    def _read_record(self, model_id: str) -> dict[str, object]:
        path = self.registry_path(model_id)
        if not path.exists():
            return {}
        try:
            with open(path, "r", encoding="utf-8") as handle:
                payload = json.load(handle)
        except (OSError, json.JSONDecodeError):
            _quarantine_corrupt_json(path)
            return {}
        return payload if isinstance(payload, dict) else {}

    def _write_record(self, model_id: str, payload: dict[str, object]) -> None:
        path = self.registry_path(model_id)
        _atomic_write_json(path, payload)


def build_engine_status(config_snapshot: dict[str, object] | None = None) -> dict[str, object]:
    snapshot = dict(config_snapshot) if isinstance(config_snapshot, dict) else {}
    store = MangaModelStore()

    ocr_id = normalize_model_id(str(snapshot.get("manga_ocr_engine") or DEFAULT_OCR_MODEL_ID))
    detect_id = str(snapshot.get("manga_detect_engine") or "comic-text-bubble-detector")
    segment_id = str(snapshot.get("manga_segment_engine") or "comic-text-detector")
    inpaint_id = str(snapshot.get("manga_inpaint_engine") or "aot-inpainting")
    detector_runtime_id, segmenter_runtime_id = get_detect_runtime_ids(detect_id, segment_id, store.root_dir)
    ocr_device = runtime_device_status_from_config(snapshot, "ocr")
    detect_device = runtime_device_status_from_config(snapshot, "detect")
    inpaint_device = runtime_device_status_from_config(snapshot, "inpaint")

    return {
        "ocr": {
            "configured_engine_id": ocr_id,
            "configured_device": ocr_device.configured,
            "resolved_device": ocr_device.resolved,
            "device": ocr_device.to_dict(),
            "runtime_engine_id": get_ocr_runtime_id(ocr_id, store.root_dir),
            "package": store.get_status(ocr_id),
        },
        "detect": {
            "configured_detector_id": detect_id,
            "configured_segmenter_id": segment_id,
            "configured_device": detect_device.configured,
            "resolved_device": detect_device.resolved,
            "device": detect_device.to_dict(),
            "runtime_detector_id": detector_runtime_id,
            "runtime_segmenter_id": segmenter_runtime_id,
            "detector_package": store.get_status(detect_id),
            "segmenter_package": store.get_status(segment_id),
        },
        "inpaint": {
            "configured_engine_id": inpaint_id,
            "configured_device": inpaint_device.configured,
            "resolved_device": inpaint_device.resolved,
            "device": inpaint_device.to_dict(),
            "runtime_engine_id": get_inpaint_runtime_id(inpaint_id, store.root_dir),
            "package": store.get_status(inpaint_id),
        },
    }
