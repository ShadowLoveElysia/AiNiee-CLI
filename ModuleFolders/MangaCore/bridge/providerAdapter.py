from __future__ import annotations

import asyncio
import hashlib
import importlib
import importlib.util
import json
import os
import shutil
import sys
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

import numpy as np
from PIL import Image, ImageChops

from ModuleFolders.MangaCore.pipeline.modelCatalog import DEFAULT_OCR_MODEL_ID, normalize_model_id
from ModuleFolders.MangaCore.render.bubbleAssign import TextSeed


@dataclass(slots=True)
class RuntimeAssetStatus:
    supported: bool
    available: bool
    storage_path: str = ""
    runtime_engine_id: str = ""
    extra: dict[str, str] = field(default_factory=dict)


@dataclass(slots=True)
class RuntimeDetectOutput:
    runtime_detector_id: str
    runtime_segmenter_id: str
    text_regions: list[dict[str, object]]
    bubble_regions: list[dict[str, object]] = field(default_factory=list)
    segment_mask: np.ndarray | None = None
    bubble_mask: np.ndarray | None = None


@dataclass(slots=True)
class RuntimeOcrOutput:
    runtime_engine_id: str
    seeds: list[TextSeed]


@dataclass(slots=True)
class RuntimeInpaintOutput:
    runtime_engine_id: str
    mask_pixels: int


@dataclass(slots=True)
class RuntimeDependencyStatus:
    supported: bool
    ok: bool
    required_modules: tuple[str, ...] = ()
    missing_modules: tuple[str, ...] = ()


@dataclass(slots=True)
class RuntimeRequirementStatus:
    supported: bool
    model_root: str = ""
    storage_path: str = ""
    required_assets: tuple[str, ...] = ()
    required_asset_paths: tuple[str, ...] = ()
    missing_asset_paths: tuple[str, ...] = ()

    def to_dict(self) -> dict[str, object]:
        return {
            "supported": self.supported,
            "model_root": self.model_root,
            "storage_path": self.storage_path,
            "required_assets": list(self.required_assets),
            "required_asset_paths": list(self.required_asset_paths),
            "missing_asset_paths": list(self.missing_asset_paths),
        }


@dataclass(slots=True)
class RuntimeDeviceStatus:
    configured: str = "auto"
    resolved: str = "cpu"
    torch_available: bool = False
    torch_file: str = ""
    torch_version: str = ""
    torch_cuda_version: str = ""
    torch_error: str = ""
    cuda_available: bool = False
    cuda_device_count: int = 0
    cuda_device_name: str = ""
    mps_available: bool = False
    onnx_available: bool = False
    onnx_file: str = ""
    onnx_version: str = ""
    onnx_error: str = ""
    onnx_providers: tuple[str, ...] = ()
    onnx_cuda_available: bool = False

    def to_dict(self) -> dict[str, object]:
        return {
            "configured": self.configured,
            "resolved": self.resolved,
            "torch_available": self.torch_available,
            "torch_file": self.torch_file,
            "torch_version": self.torch_version,
            "torch_cuda_version": self.torch_cuda_version,
            "torch_error": self.torch_error,
            "cuda_available": self.cuda_available,
            "cuda_device_count": self.cuda_device_count,
            "cuda_device_name": self.cuda_device_name,
            "mps_available": self.mps_available,
            "onnx_available": self.onnx_available,
            "onnx_file": self.onnx_file,
            "onnx_version": self.onnx_version,
            "onnx_error": self.onnx_error,
            "onnx_providers": list(self.onnx_providers),
            "onnx_cuda_available": self.onnx_cuda_available,
        }


_OCR_RUNTIME_SPECS: dict[str, dict[str, str]] = {
    "32px": {
        "module": "manga_translator.ocr.model_32px",
        "class": "Model32pxOCR",
        "runtime": "32px/torch",
        "kind": "ocr",
    },
    "48px_ctc": {
        "module": "manga_translator.ocr.model_48px_ctc",
        "class": "Model48pxCTCOCR",
        "runtime": "48px_ctc/torch",
        "kind": "ocr",
    },
    "paddleocr-vl-1.5": {
        "module": "manga_translator.ocr.model_paddleocr_vl",
        "class": "ModelPaddleOCRVL",
        "runtime": "paddleocr-vl-1.5/transformers",
        "kind": "ocr",
    },
    "manga-ocr": {
        "module": "manga_translator.ocr.model_manga_ocr",
        "class": "ModelMangaOCR",
        "runtime": "manga-ocr/transformers",
        "kind": "ocr",
    },
    "mit48px-ocr": {
        "module": "manga_translator.ocr.model_48px",
        "class": "Model48pxOCR",
        "runtime": "mit48px-ocr/torch",
        "kind": "ocr",
    },
    "paddleocr": {
        "module": "manga_translator.ocr.model_paddleocr",
        "class": "ModelPaddleOCRChinese",
        "runtime": "paddleocr/onnx",
        "kind": "ocr",
    },
    "paddleocr_korean": {
        "module": "manga_translator.ocr.model_paddleocr",
        "class": "ModelPaddleOCRKorean",
        "runtime": "paddleocr_korean/onnx",
        "kind": "ocr",
    },
    "paddleocr_latin": {
        "module": "manga_translator.ocr.model_paddleocr",
        "class": "ModelPaddleOCRLatin",
        "runtime": "paddleocr_latin/onnx",
        "kind": "ocr",
    },
    "paddleocr_thai": {
        "module": "manga_translator.ocr.model_paddleocr",
        "class": "ModelPaddleOCRThai",
        "runtime": "paddleocr_thai/onnx",
        "kind": "ocr",
    },
}

_DETECT_RUNTIME_SPECS: dict[str, dict[str, str]] = {
    "comic-text-bubble-detector": {
        "module": "manga_translator.detection.ctd",
        "class": "ComicTextDetector",
        "runtime": "comic-text-bubble-detector/ctd",
        "kind": "detect",
    },
}

_INPAINT_RUNTIME_SPECS: dict[str, dict[str, str]] = {
    "aot-inpainting": {
        "module": "manga_translator.inpainting.inpainting_aot",
        "class": "AotInpainter",
        "runtime": "aot-inpainting/torch",
        "kind": "inpaint",
    },
    "lama-manga": {
        "module": "manga_translator.inpainting.inpainting_lama_mpe",
        "class": "LamaLargeInpainter",
        "runtime": "lama-manga/onnx-or-torch",
        "kind": "inpaint",
    },
}

_RUNTIME_SPECS: dict[str, dict[str, str]] = {
    **_OCR_RUNTIME_SPECS,
    **_DETECT_RUNTIME_SPECS,
    **_INPAINT_RUNTIME_SPECS,
}

_RUNTIME_REQUIRED_ASSETS: dict[str, tuple[str, ...]] = {
    "comic-text-bubble-detector": (
        "detection/comictextdetector.pt",
        "detection/comictextdetector.pt.onnx",
    ),
    "paddleocr-vl-1.5": (
        "ocr/PaddleOCR-VL-1.5",
        "ocr/ocr_ar_48px.ckpt",
        "ocr/alphabet-all-v7.txt",
    ),
    "32px": (
        "ocr/ocr.ckpt",
        "ocr/alphabet-all-v5.txt",
    ),
    "48px_ctc": (
        "ocr/ocr-ctc.ckpt",
        "ocr/alphabet-all-v5.txt",
    ),
    "manga-ocr": (
        "ocr/ocr_ar_48px.ckpt",
        "ocr/alphabet-all-v7.txt",
        "ocr/manga_ocr/config.json",
        "ocr/manga_ocr/preprocessor_config.json",
        "ocr/manga_ocr/pytorch_model.bin",
        "ocr/manga_ocr/special_tokens_map.json",
        "ocr/manga_ocr/tokenizer_config.json",
        "ocr/manga_ocr/vocab.txt",
    ),
    "mit48px-ocr": (
        "ocr/ocr_ar_48px.ckpt",
        "ocr/alphabet-all-v7.txt",
    ),
    "paddleocr": (
        "ocr/ch_PP-OCRv5_rec_server_infer.onnx",
        "ocr/ppocrv5_dict.txt",
        "ocr/ocr_ar_48px.ckpt",
        "ocr/alphabet-all-v7.txt",
    ),
    "paddleocr_korean": (
        "ocr/korean_PP-OCRv5_rec_mobile_infer.onnx",
        "ocr/ppocrv5_korean_dict.txt",
        "ocr/ocr_ar_48px.ckpt",
        "ocr/alphabet-all-v7.txt",
    ),
    "paddleocr_latin": (
        "ocr/latin_PP-OCRv5_rec_mobile_infer.onnx",
        "ocr/ppocrv5_latin_dict.txt",
        "ocr/ocr_ar_48px.ckpt",
        "ocr/alphabet-all-v7.txt",
    ),
    "paddleocr_thai": (
        "ocr/thai_PP-OCRv5_rec_mobile_infer.onnx",
        "ocr/ppocrv5_thai_dict.txt",
        "ocr/ocr_ar_48px.ckpt",
        "ocr/alphabet-all-v7.txt",
    ),
    "aot-inpainting": (
        "inpainting/inpainting.ckpt",
    ),
    "lama-manga": (
        "inpainting/inpainting_lama_mpe.ckpt",
        "inpainting/lamampe.onnx",
    ),
}

_RUNTIME_REQUIRED_MODULES: dict[str, tuple[str, ...]] = {
    "comic-text-bubble-detector": ("manga_translator", "cv2"),
    "32px": ("manga_translator", "torch"),
    "48px_ctc": ("manga_translator", "torch"),
    "paddleocr-vl-1.5": ("manga_translator", "torch", "transformers"),
    "manga-ocr": ("manga_translator", "torch", "transformers"),
    "mit48px-ocr": ("manga_translator", "torch"),
    "paddleocr": ("manga_translator", "torch", "onnxruntime"),
    "paddleocr_korean": ("manga_translator", "torch", "onnxruntime"),
    "paddleocr_latin": ("manga_translator", "torch", "onnxruntime"),
    "paddleocr_thai": ("manga_translator", "torch", "onnxruntime"),
    "aot-inpainting": ("manga_translator", "torch"),
    "lama-manga": ("manga_translator",),
}

_RUNTIME_CLASS_CACHE: dict[tuple[str, str, str], type] = {}
_RUNTIME_INSTANCE_CACHE: dict[tuple[str, str, str, str], Any] = {}
_ASCII_RUNTIME_MODEL_ROOT_CACHE: dict[tuple[str, str], Path] = {}
_DEFAULT_HF_ENDPOINT = "https://hf-mirror.com"
_DEFAULT_RUNTIME_DEVICE = "auto"
_PADDLEOCR_VL_MANGA_REPO_ID = "jzhang533/PaddleOCR-VL-For-Manga"
_PADDLEOCR_VL_MANGA_MARKER = ".ainiee_hf_snapshot.json"
_VALID_RUNTIME_DEVICE_PREFIXES = ("auto", "cpu", "cuda", "mps")
_STAGE_RUNTIME_DEVICE_KEYS = {
    "detect": "manga_detect_device",
    "ocr": "manga_ocr_device",
    "inpaint": "manga_inpaint_device",
}
_CV2_REQUIRED_ATTRIBUTES = (
    "imread",
    "IMREAD_COLOR",
    "cvtColor",
    "connectedComponentsWithStats",
    "inpaint",
)


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[3]


def _default_model_root() -> Path:
    return _repo_root() / "Resource" / "Models" / "MangaCore"


def _resolve_model_root(root_dir: str | Path | None = None) -> Path:
    return Path(root_dir) if root_dir is not None else _default_model_root()


def _is_ascii_path(path: str | Path) -> bool:
    try:
        str(path).encode("ascii")
    except UnicodeEncodeError:
        return False
    return True


def _ascii_cache_base_dir() -> Path:
    candidates = [
        Path(tempfile.gettempdir()) / "ainiee_manga_runtime_models",
        Path(os.environ.get("SYSTEMDRIVE", "C:") + "\\ainiee_manga_runtime_models"),
    ]
    for candidate in candidates:
        if _is_ascii_path(candidate):
            return candidate
    return candidates[0]


def _copy_runtime_asset_if_needed(source: Path, destination: Path) -> None:
    destination.parent.mkdir(parents=True, exist_ok=True)
    if destination.exists():
        try:
            source_stat = source.stat()
            destination_stat = destination.stat()
            if (
                source_stat.st_size == destination_stat.st_size
                and int(source_stat.st_mtime) == int(destination_stat.st_mtime)
            ):
                return
        except OSError:
            pass
    shutil.copy2(source, destination)


def _runtime_root_for_wrapper(
    model_id: str,
    model_root: Path,
    *,
    kind: str,
) -> Path:
    if os.name != "nt" or kind != "detect" or _is_ascii_path(model_root):
        return model_root
    required_assets = _RUNTIME_REQUIRED_ASSETS.get(str(model_id), ())
    if not required_assets or not all((model_root / asset_path).exists() for asset_path in required_assets):
        return model_root

    cache_key = (str(model_root.resolve()), str(model_id))
    cached = _ASCII_RUNTIME_MODEL_ROOT_CACHE.get(cache_key)
    if cached is not None:
        return cached

    digest = hashlib.sha256(str(model_root.resolve()).encode("utf-8")).hexdigest()[:16]
    mirror_root = _ascii_cache_base_dir() / digest
    for relative_path in required_assets:
        source = model_root / relative_path
        if source.exists() and source.is_file():
            _copy_runtime_asset_if_needed(source, mirror_root / relative_path)

    _ASCII_RUNTIME_MODEL_ROOT_CACHE[cache_key] = mirror_root
    return mirror_root


def _runtime_cache_root(root_dir: str | Path | None = None) -> Path:
    return _resolve_model_root(root_dir) / "cache"


def canonical_runtime_model_id(model_id: str | None) -> str:
    return normalize_model_id(model_id)


def _ensure_huggingface_endpoint() -> str:
    endpoint = (os.environ.get("HF_ENDPOINT") or _DEFAULT_HF_ENDPOINT).rstrip("/")
    os.environ["HF_ENDPOINT"] = endpoint
    os.environ.setdefault("HF_HUB_ENDPOINT", endpoint)
    return endpoint


def _ensure_transformers_module_cache(root_dir: str | Path | None = None) -> Path:
    cache_root = _runtime_cache_root(root_dir) / "huggingface"
    modules_cache = cache_root / "modules"
    modules_cache.mkdir(parents=True, exist_ok=True)

    modules_cache_str = str(modules_cache)
    _ensure_huggingface_endpoint()
    os.environ["HF_HOME"] = str(cache_root)
    os.environ["HF_MODULES_CACHE"] = modules_cache_str

    for module_name in (
        "transformers.utils",
        "transformers.utils.hub",
        "transformers.file_utils",
        "transformers.dynamic_module_utils",
    ):
        module = sys.modules.get(module_name)
        if module is not None and hasattr(module, "HF_MODULES_CACHE"):
            setattr(module, "HF_MODULES_CACHE", modules_cache_str)

    return modules_cache


def _ensure_upstream_import_path() -> None:
    upstream_root = _repo_root() / "manga-translator-ui-main"
    upstream_path = str(upstream_root)
    if upstream_path not in sys.path:
        sys.path.insert(0, upstream_path)


def _await_sync(coro):
    try:
        running_loop = asyncio.get_running_loop()
    except RuntimeError:
        running_loop = None

    if running_loop is None or not running_loop.is_running():
        return asyncio.run(coro)

    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(coro)
    finally:
        loop.close()


def normalize_runtime_device(value: str | None = None) -> str:
    raw_value = str(value or os.environ.get("AINIEE_MANGA_RUNTIME_DEVICE") or _DEFAULT_RUNTIME_DEVICE).strip().lower()
    if not raw_value:
        return _DEFAULT_RUNTIME_DEVICE
    if raw_value in {"gpu", "cuda-auto"}:
        return "cuda"
    if raw_value in {"cuda0", "cuda:0"}:
        return "cuda"
    if raw_value.startswith("cuda:"):
        suffix = raw_value.split(":", 1)[1]
        return raw_value if suffix.isdigit() else "cuda"
    if raw_value in _VALID_RUNTIME_DEVICE_PREFIXES:
        return raw_value
    return _DEFAULT_RUNTIME_DEVICE


def _torch_device_status(configured: str) -> tuple[str, bool, str, str, str, str, bool, int, str, bool]:
    try:
        import torch

        torch_file = str(getattr(torch, "__file__", "") or "")
        torch_version = str(getattr(torch, "__version__", "") or "")
        torch_cuda_version = str(getattr(torch.version, "cuda", "") or "")
        cuda_available = bool(torch.cuda.is_available())
        cuda_device_count = int(torch.cuda.device_count()) if cuda_available else 0
        cuda_device_name = torch.cuda.get_device_name(0) if cuda_available and cuda_device_count > 0 else ""
        mps_available = bool(hasattr(torch.backends, "mps") and torch.backends.mps.is_available())
    except Exception as exc:
        return "cpu", False, "", "", "", str(exc), False, 0, "", False

    if configured.startswith("cuda"):
        return ("cuda" if cuda_available else "cpu", True, torch_file, torch_version, torch_cuda_version, "", cuda_available, cuda_device_count, cuda_device_name, mps_available)
    if configured == "mps":
        return ("mps" if mps_available else "cpu", True, torch_file, torch_version, torch_cuda_version, "", cuda_available, cuda_device_count, cuda_device_name, mps_available)
    if configured == "auto":
        if cuda_available:
            return "cuda", True, torch_file, torch_version, torch_cuda_version, "", cuda_available, cuda_device_count, cuda_device_name, mps_available
        if mps_available:
            return "mps", True, torch_file, torch_version, torch_cuda_version, "", cuda_available, cuda_device_count, cuda_device_name, mps_available
    return "cpu", True, torch_file, torch_version, torch_cuda_version, "", cuda_available, cuda_device_count, cuda_device_name, mps_available


def get_runtime_device_status(preferred: str | None = None) -> RuntimeDeviceStatus:
    configured = normalize_runtime_device(preferred)
    (
        resolved,
        torch_available,
        torch_file,
        torch_version,
        torch_cuda_version,
        torch_error,
        cuda_available,
        cuda_device_count,
        cuda_device_name,
        mps_available,
    ) = _torch_device_status(configured)
    onnx_available = False
    onnx_file = ""
    onnx_version = ""
    onnx_error = ""
    onnx_providers: tuple[str, ...] = ()
    try:
        import onnxruntime as ort

        onnx_available = True
        onnx_file = str(getattr(ort, "__file__", "") or "")
        onnx_version = str(getattr(ort, "__version__", "") or "")
        onnx_providers = tuple(str(provider) for provider in ort.get_available_providers())
    except Exception as exc:
        onnx_error = str(exc)
    return RuntimeDeviceStatus(
        configured=configured,
        resolved=resolved,
        torch_available=torch_available,
        torch_file=torch_file,
        torch_version=torch_version,
        torch_cuda_version=torch_cuda_version,
        torch_error=torch_error,
        cuda_available=cuda_available,
        cuda_device_count=cuda_device_count,
        cuda_device_name=cuda_device_name,
        mps_available=mps_available,
        onnx_available=onnx_available,
        onnx_file=onnx_file,
        onnx_version=onnx_version,
        onnx_error=onnx_error,
        onnx_providers=onnx_providers,
        onnx_cuda_available="CUDAExecutionProvider" in onnx_providers,
    )


def _select_device(preferred: str | None = None) -> str:
    return get_runtime_device_status(preferred).resolved


def runtime_device_from_config(config_snapshot: dict[str, object] | None = None, stage: str | None = None) -> str:
    snapshot = dict(config_snapshot) if isinstance(config_snapshot, dict) else {}
    stage_key = _STAGE_RUNTIME_DEVICE_KEYS.get(str(stage or "").strip().lower())
    if stage_key:
        stage_value = str(snapshot.get(stage_key) or "").strip()
        if stage_value and normalize_runtime_device(stage_value) != "auto":
            return normalize_runtime_device(stage_value)
    return normalize_runtime_device(str(snapshot.get("manga_runtime_device") or "").strip() or None)


def runtime_device_status_from_config(
    config_snapshot: dict[str, object] | None = None,
    stage: str | None = None,
) -> RuntimeDeviceStatus:
    return get_runtime_device_status(runtime_device_from_config(config_snapshot, stage))


def _resolve_runtime_spec(model_id: str) -> dict[str, str] | None:
    return _RUNTIME_SPECS.get(normalize_model_id(model_id))


def _runtime_storage_path(model_id: str, root_dir: str | Path | None = None) -> str:
    model_id = normalize_model_id(model_id)
    spec = _resolve_runtime_spec(model_id)
    if spec is None:
        return ""
    subdir = {
        "ocr": "ocr",
        "detect": "detection",
        "inpaint": "inpainting",
    }[spec["kind"]]
    return str(_resolve_model_root(root_dir) / subdir)


def _local_runtime_assets_available(model_id: str, root_dir: str | Path | None = None) -> bool:
    model_id = normalize_model_id(model_id)
    required_assets = _RUNTIME_REQUIRED_ASSETS.get(str(model_id))
    if not required_assets:
        return False
    model_root = _resolve_model_root(root_dir)
    if not all((model_root / asset_path).exists() for asset_path in required_assets):
        return False
    if str(model_id) != "paddleocr-vl-1.5":
        return True

    marker_path = model_root / "ocr" / "PaddleOCR-VL-1.5" / _PADDLEOCR_VL_MANGA_MARKER
    try:
        marker = json.loads(marker_path.read_text(encoding="utf-8"))
    except Exception:
        return False
    return marker.get("repo_id") == _PADDLEOCR_VL_MANGA_REPO_ID


def get_runtime_dependency_status(model_id: str) -> RuntimeDependencyStatus:
    model_id = normalize_model_id(model_id)
    spec = _resolve_runtime_spec(model_id)
    if spec is None:
        return RuntimeDependencyStatus(supported=False, ok=False)

    required_modules = _RUNTIME_REQUIRED_MODULES.get(str(model_id), ())
    if "manga_translator" in required_modules:
        _ensure_upstream_import_path()
    missing_modules: list[str] = []
    for module_name in required_modules:
        try:
            module_spec = importlib.util.find_spec(module_name)
        except (ImportError, AttributeError, ValueError):
            module_spec = None
        if module_spec is None:
            missing_modules.append(module_name)
            continue
        if module_name != "cv2":
            continue
        try:
            cv2_module = importlib.import_module(module_name)
        except Exception:
            missing_modules.append(module_name)
            continue
        if any(not hasattr(cv2_module, attribute) for attribute in _CV2_REQUIRED_ATTRIBUTES):
            missing_modules.append(module_name)
    missing = tuple(missing_modules)
    return RuntimeDependencyStatus(
        supported=True,
        ok=not missing,
        required_modules=tuple(required_modules),
        missing_modules=missing,
    )


def get_runtime_requirement_status(
    model_id: str,
    root_dir: str | Path | None = None,
) -> RuntimeRequirementStatus:
    model_id = normalize_model_id(model_id)
    spec = _resolve_runtime_spec(model_id)
    if spec is None:
        return RuntimeRequirementStatus(supported=False)

    model_root = _resolve_model_root(root_dir)
    required_assets = _RUNTIME_REQUIRED_ASSETS.get(str(model_id), ())
    required_asset_paths = tuple(str(model_root / asset_path) for asset_path in required_assets)
    missing_asset_paths = tuple(
        asset_path
        for asset_path in required_asset_paths
        if not Path(asset_path).exists()
    )
    if str(model_id) == "paddleocr-vl-1.5" and not missing_asset_paths:
        marker_path = model_root / "ocr" / "PaddleOCR-VL-1.5" / _PADDLEOCR_VL_MANGA_MARKER
        try:
            marker = json.loads(marker_path.read_text(encoding="utf-8"))
        except Exception:
            missing_asset_paths = (str(marker_path),)
        else:
            if marker.get("repo_id") != _PADDLEOCR_VL_MANGA_REPO_ID:
                missing_asset_paths = (str(marker_path),)
    return RuntimeRequirementStatus(
        supported=True,
        model_root=str(model_root),
        storage_path=_runtime_storage_path(model_id, root_dir),
        required_assets=tuple(required_assets),
        required_asset_paths=required_asset_paths,
        missing_asset_paths=missing_asset_paths,
    )


def _build_runtime_wrapper(
    model_id: str,
    root_dir: str | Path | None = None,
    device: str | None = None,
):
    model_id = normalize_model_id(model_id)
    spec = _resolve_runtime_spec(model_id)
    if spec is None:
        raise KeyError(f"Unsupported runtime-backed manga model: {model_id}")

    _ensure_transformers_module_cache(root_dir)
    model_root = _resolve_model_root(root_dir)
    wrapper_model_root = _runtime_root_for_wrapper(model_id, model_root, kind=spec["kind"])
    cache_key = (str(wrapper_model_root), spec["module"], spec["class"])
    wrapper_cls = _RUNTIME_CLASS_CACHE.get(cache_key)
    if wrapper_cls is None:
        _ensure_upstream_import_path()
        module = importlib.import_module(spec["module"])
        base_cls = getattr(module, spec["class"])
        wrapper_cls = type(
            f"MangaCore{spec['class']}",
            (base_cls,),
            {"_MODEL_DIR": str(wrapper_model_root)},
        )
        _RUNTIME_CLASS_CACHE[cache_key] = wrapper_cls

    instance_key = (str(wrapper_model_root), spec["kind"], str(model_id), _select_device(device))
    wrapper = _RUNTIME_INSTANCE_CACHE.get(instance_key)
    if wrapper is None:
        wrapper = wrapper_cls()
        _RUNTIME_INSTANCE_CACHE[instance_key] = wrapper
    return wrapper


def _ensure_loaded(model_id: str, root_dir: str | Path | None = None, device: str | None = None):
    model_id = normalize_model_id(model_id)
    selected_device = _select_device(device)
    wrapper = _build_runtime_wrapper(model_id, root_dir, device=selected_device)
    if not wrapper.is_downloaded():
        raise FileNotFoundError(f"Runtime assets are not ready for manga model: {model_id}")
    loaded_device = str(getattr(wrapper, "device", "") or "")
    if wrapper.is_loaded() and loaded_device and loaded_device != selected_device:
        try:
            _await_sync(wrapper.unload())
        except Exception:
            pass
    if not wrapper.is_loaded():
        _await_sync(wrapper.load(device=selected_device))
    return wrapper


def _load_rgb_image(path: str | Path) -> np.ndarray:
    with Image.open(path) as image:
        return np.array(image.convert("RGB"), dtype=np.uint8)


def _load_mask_image(path: str | Path | None, size: tuple[int, int]) -> Image.Image:
    if not path:
        return Image.new("L", size, 0)

    candidate = Path(path)
    if not candidate.exists():
        return Image.new("L", size, 0)

    with Image.open(candidate) as image:
        mask = image.convert("L")
    if mask.size != size:
        mask = mask.resize(size, resample=Image.Resampling.NEAREST)
    return mask


def _normalize_mask_array(mask: np.ndarray | None) -> np.ndarray | None:
    if mask is None:
        return None
    array = np.asarray(mask)
    if array.ndim == 3:
        array = array[:, :, 0]
    return np.where(array > 0, 255, 0).astype(np.uint8)


def _runtime_backend_suffix(wrapper: Any) -> str:
    backend = getattr(wrapper, "backend", "")
    if backend:
        return str(backend)

    device = str(getattr(wrapper, "device", "cpu"))
    if device.startswith("cuda"):
        return "torch-cuda"
    if device == "mps":
        return "torch-mps"
    if getattr(wrapper, "use_gpu", False):
        return "torch-gpu"
    return "cpu"


def _region_polygon(region: Any) -> np.ndarray:
    polygon = getattr(region, "polygon", None)
    if isinstance(polygon, list) and len(polygon) >= 4:
        return np.asarray(polygon[:4], dtype=np.float32)

    bbox = getattr(region, "bbox", None)
    if isinstance(bbox, list) and len(bbox) == 4:
        x1, y1, x2, y2 = bbox
        return np.asarray(
            [
                [x1, y1],
                [x2, y1],
                [x2, y2],
                [x1, y2],
            ],
            dtype=np.float32,
        )
    raise ValueError(f"Unsupported region payload for OCR runtime bridge: {region!r}")


def _region_id(region: Any, index: int) -> str:
    candidate = getattr(region, "region_id", "")
    return str(candidate) if candidate else f"seed_{index:04d}"


def get_runtime_asset_status(model_id: str, root_dir: str | Path | None = None) -> RuntimeAssetStatus:
    model_id = normalize_model_id(model_id)
    spec = _resolve_runtime_spec(model_id)
    if spec is None:
        return RuntimeAssetStatus(supported=False, available=False)

    extra: dict[str, str] = {}
    try:
        wrapper = _build_runtime_wrapper(model_id, root_dir)
        storage_path = str(Path(wrapper.model_dir))
        wrapper_available = bool(wrapper.is_downloaded())
        local_available = _local_runtime_assets_available(model_id, root_dir)
        if str(model_id) == "paddleocr-vl-1.5":
            available = local_available
            extra["status_source"] = "local-files" if local_available else "manga-snapshot-marker"
            extra["wrapper_downloaded"] = str(wrapper_available).lower()
        else:
            available = wrapper_available or local_available
            extra["status_source"] = "wrapper" if wrapper_available else "local-files"
    except Exception as exc:
        storage_path = _runtime_storage_path(model_id, root_dir)
        available = _local_runtime_assets_available(model_id, root_dir)
        extra["status_source"] = "local-files" if available else "wrapper-error"
        if str(model_id) == "paddleocr-vl-1.5" and not available:
            extra["status_source"] = "manga-snapshot-marker"
        extra["status_error"] = str(exc)
    if spec["kind"] == "detect":
        extra["runtime_segmenter_id"] = "comic-text-bubble-detector/mask"
    return RuntimeAssetStatus(
        supported=True,
        available=available,
        storage_path=storage_path,
        runtime_engine_id=spec["runtime"],
        extra=extra,
    )


DownloadProgressCallback = Callable[[dict[str, object]], None]


def download_runtime_assets(
    model_id: str,
    root_dir: str | Path | None = None,
    *,
    quiet: bool = False,
    progress_callback: DownloadProgressCallback | None = None,
) -> RuntimeAssetStatus | None:
    model_id = normalize_model_id(model_id)
    spec = _resolve_runtime_spec(model_id)
    if spec is None:
        return None
    from ModuleFolders.Service.HttpService.ModelDownload import prepare_models, resolve_assets_for_model_ids

    try:
        resolve_assets_for_model_ids((str(model_id),))
    except KeyError:
        pass
    else:
        prepare_kwargs: dict[str, object] = {
            "root_dir": _resolve_model_root(root_dir),
        }
        if quiet:
            prepare_kwargs["quiet"] = quiet
        if progress_callback is not None:
            prepare_kwargs["progress_callback"] = progress_callback
        prepare_models((str(model_id),), **prepare_kwargs)
        return get_runtime_asset_status(model_id, root_dir)

    wrapper = _build_runtime_wrapper(model_id, root_dir)
    if progress_callback is not None:
        progress_callback(
            {
                "kind": "manga_model_download",
                "stage": "wrapper_download",
                "message": f"Downloading runtime assets: {model_id}",
                "model_id": model_id,
                "item_index": 1,
                "item_count": 1,
            }
        )
    _await_sync(wrapper.download())
    return get_runtime_asset_status(model_id, root_dir)


def get_detect_runtime_ids(
    detector_id: str,
    segmenter_id: str,
    root_dir: str | Path | None = None,
) -> tuple[str, str]:
    detector_status = get_runtime_asset_status(detector_id, root_dir)
    if not detector_status.available:
        return "heuristic-grouping", "pil-mask-rasterizer"
    return (
        detector_status.runtime_engine_id,
        detector_status.extra.get("runtime_segmenter_id", segmenter_id),
    )


def get_ocr_runtime_id(engine_id: str, root_dir: str | Path | None = None) -> str:
    engine_id = normalize_model_id(engine_id or DEFAULT_OCR_MODEL_ID)
    status = get_runtime_asset_status(engine_id, root_dir)
    if not status.available:
        return "rapidocr-onnxruntime"
    return status.runtime_engine_id


def get_inpaint_runtime_id(engine_id: str, root_dir: str | Path | None = None) -> str:
    status = get_runtime_asset_status(engine_id, root_dir)
    if not status.available:
        return "opencv-telea/pil-fallback"
    return status.runtime_engine_id


def run_detect_runtime(
    image_path: str | Path,
    detector_id: str,
    *,
    root_dir: str | Path | None = None,
    device: str | None = None,
) -> RuntimeDetectOutput | None:
    if detector_id not in _DETECT_RUNTIME_SPECS:
        return None

    wrapper = _ensure_loaded(detector_id, root_dir=root_dir, device=device)
    import cv2

    image = cv2.imread(str(image_path), cv2.IMREAD_COLOR)
    if image is None:
        raise FileNotFoundError(f"Unable to read image for detect runtime: {image_path}")

    textlines, raw_mask, refined_mask = _await_sync(
        wrapper.detect(
            image,
            detect_size=1536,
            text_threshold=0.3,
            box_threshold=0.6,
            unclip_ratio=2.0,
            verbose=False,
        )
    )
    segment_mask = refined_mask if isinstance(refined_mask, np.ndarray) else raw_mask
    segment_mask = _normalize_mask_array(segment_mask)

    text_regions: list[dict[str, object]] = []
    for index, quad in enumerate(textlines, start=1):
        pts = np.asarray(getattr(quad, "pts"), dtype=np.float32)
        bbox = [
            int(np.min(pts[:, 0])),
            int(np.min(pts[:, 1])),
            int(np.max(pts[:, 0])),
            int(np.max(pts[:, 1])),
        ]
        text_regions.append(
            {
                "region_id": f"region_{index:04d}",
                "kind": "text",
                "bbox": bbox,
                "polygon": pts.tolist(),
                "score": float(getattr(quad, "prob", 1.0) or 0.0),
            }
        )

    runtime_detector_id = f"comic-text-bubble-detector/{_runtime_backend_suffix(wrapper)}"
    runtime_segmenter_id = "comic-text-bubble-detector/mask"
    return RuntimeDetectOutput(
        runtime_detector_id=runtime_detector_id,
        runtime_segmenter_id=runtime_segmenter_id,
        text_regions=text_regions,
        segment_mask=segment_mask,
    )


def run_region_ocr_runtime(
    image_path: str | Path,
    engine_id: str,
    regions: list[Any],
    *,
    root_dir: str | Path | None = None,
    device: str | None = None,
) -> RuntimeOcrOutput | None:
    engine_id = normalize_model_id(engine_id)
    if engine_id not in _OCR_RUNTIME_SPECS:
        return None
    if not regions:
        return RuntimeOcrOutput(runtime_engine_id=get_ocr_runtime_id(engine_id, root_dir), seeds=[])

    wrapper = _ensure_loaded(engine_id, root_dir=root_dir, device=device)
    _ensure_upstream_import_path()
    config_module = importlib.import_module("manga_translator.config")
    utils_module = importlib.import_module("manga_translator.utils")
    OcrConfig = getattr(config_module, "OcrConfig")
    Quadrilateral = getattr(utils_module, "Quadrilateral")

    image = _load_rgb_image(image_path)
    quads = [Quadrilateral(_region_polygon(region), "", 1.0) for region in regions]
    recognized = _await_sync(wrapper.recognize(image, quads, OcrConfig(ignore_bubble=0), False))

    seeds: list[TextSeed] = []
    for index, region in enumerate(recognized, start=1):
        source_text = str(getattr(region, "text", "") or "").strip()
        if not source_text:
            continue
        pts = np.asarray(getattr(region, "pts"), dtype=np.float32)
        bbox = [
            int(np.min(pts[:, 0])),
            int(np.min(pts[:, 1])),
            int(np.max(pts[:, 0])),
            int(np.max(pts[:, 1])),
        ]
        direction = "vertical" if str(getattr(region, "direction", "h")) == "v" else "horizontal"
        seeds.append(
            TextSeed(
                seed_id=_region_id(regions[index - 1], index),
                bbox=bbox,
                polygon=pts.tolist(),
                source_text=source_text,
                confidence=float(getattr(region, "prob", 1.0) or 0.0),
                direction=direction,
            )
        )

    runtime_engine_id = f"{engine_id}/{_runtime_backend_suffix(wrapper)}"
    return RuntimeOcrOutput(runtime_engine_id=runtime_engine_id, seeds=seeds)


def run_inpaint_runtime(
    *,
    source_path: str | Path,
    segment_mask_path: str | Path,
    output_path: str | Path,
    engine_id: str,
    brush_mask_path: str | Path | None = None,
    root_dir: str | Path | None = None,
    device: str | None = None,
) -> RuntimeInpaintOutput | None:
    if engine_id not in _INPAINT_RUNTIME_SPECS:
        return None

    wrapper = _ensure_loaded(engine_id, root_dir=root_dir, device=device)
    _ensure_upstream_import_path()
    config_module = importlib.import_module("manga_translator.config")
    InpainterConfig = getattr(config_module, "InpainterConfig")

    image = _load_rgb_image(source_path)
    size = (image.shape[1], image.shape[0])
    segment_mask = _load_mask_image(segment_mask_path, size)
    brush_mask = _load_mask_image(brush_mask_path, size)
    merged_mask = ImageChops.lighter(segment_mask, brush_mask)
    merged_mask_array = np.array(merged_mask, dtype=np.uint8)
    mask_pixels = int(np.count_nonzero(merged_mask_array))

    result = _await_sync(
        wrapper.inpaint(
            image,
            merged_mask_array,
            InpainterConfig(),
            inpainting_size=max(size),
            verbose=False,
        )
    )
    output_path = Path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    Image.fromarray(np.asarray(result, dtype=np.uint8)).save(output_path, format="PNG")

    backend_suffix = _runtime_backend_suffix(wrapper)
    if engine_id == "lama-manga" and backend_suffix in {"torch-cpu", "cpu"}:
        runtime_engine_id = "lama-manga/onnx"
    else:
        runtime_engine_id = f"{engine_id}/{backend_suffix}"
    return RuntimeInpaintOutput(
        runtime_engine_id=runtime_engine_id,
        mask_pixels=mask_pixels,
    )
