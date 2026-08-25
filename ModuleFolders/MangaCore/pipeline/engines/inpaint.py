from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import numpy as np
from PIL import Image, ImageChops, ImageFilter

from ModuleFolders.MangaCore.bridge.providerAdapter import (
    get_runtime_asset_status,
    get_runtime_device_status,
    run_inpaint_runtime,
)

DEFAULT_INPAINT_ENGINE_ID = "aot-inpainting"
ALTERNATIVE_INPAINT_ENGINE_IDS = ("lama-manga",)

try:
    import cv2
except Exception:  # pragma: no cover - optional runtime import
    cv2 = None


def _has_opencv_inpaint_support() -> bool:
    """检查 OpenCV 是否具备当前引擎实际需要的 API。"""
    if cv2 is None:
        return False
    return all(
        (
            callable(getattr(cv2, "cvtColor", None)),
            callable(getattr(cv2, "inpaint", None)),
            getattr(cv2, "COLOR_RGB2BGR", None) is not None,
            getattr(cv2, "COLOR_BGR2RGB", None) is not None,
            getattr(cv2, "INPAINT_NS", None) is not None,
            getattr(cv2, "INPAINT_TELEA", None) is not None,
        )
    )


def _load_mask(path: str | Path | None, size: tuple[int, int]) -> Image.Image:
    if not path:
        return Image.new("L", size, 0)

    candidate = Path(path)
    if not candidate.exists():
        return Image.new("L", size, 0)

    with Image.open(candidate) as mask_image:
        mask = mask_image.convert("L")
    if mask.size != size:
        mask = mask.resize(size, resample=Image.Resampling.NEAREST)
    return mask


def _pil_fallback_inpaint(source: Image.Image, mask: Image.Image) -> Image.Image:
    source = source.convert("RGB")
    mask = mask.convert("L")
    mask_array = np.array(mask, dtype=np.uint8) > 0
    if not np.any(mask_array):
        return source.copy()

    source_array = np.array(source, dtype=np.uint8)
    result_array = source_array.copy()
    height, width = mask_array.shape
    visited = np.zeros(mask_array.shape, dtype=bool)
    unmasked_samples = source_array[~mask_array]
    if len(unmasked_samples):
        global_fill = np.median(unmasked_samples, axis=0).astype(np.uint8)
    else:
        global_fill = np.median(source_array.reshape(-1, 3), axis=0).astype(np.uint8)

    for start_y, start_x in np.argwhere(mask_array):
        if visited[start_y, start_x]:
            continue

        stack = [(int(start_y), int(start_x))]
        visited[start_y, start_x] = True
        min_x = max_x = int(start_x)
        min_y = max_y = int(start_y)

        while stack:
            y, x = stack.pop()
            min_x = min(min_x, x)
            max_x = max(max_x, x)
            min_y = min(min_y, y)
            max_y = max(max_y, y)
            for next_y, next_x in ((y - 1, x), (y + 1, x), (y, x - 1), (y, x + 1)):
                if (
                    0 <= next_y < height
                    and 0 <= next_x < width
                    and mask_array[next_y, next_x]
                    and not visited[next_y, next_x]
                ):
                    visited[next_y, next_x] = True
                    stack.append((next_y, next_x))

        component_width = max_x - min_x + 1
        component_height = max_y - min_y + 1
        sample_padding = max(8, min(max(component_width, component_height), 64))
        sample_x0 = max(0, min_x - sample_padding)
        sample_y0 = max(0, min_y - sample_padding)
        sample_x1 = min(width, max_x + sample_padding + 1)
        sample_y1 = min(height, max_y + sample_padding + 1)
        sample_mask = ~mask_array[sample_y0:sample_y1, sample_x0:sample_x1]
        samples = source_array[sample_y0:sample_y1, sample_x0:sample_x1][sample_mask]
        fill_color = np.median(samples, axis=0).astype(np.uint8) if len(samples) else global_fill

        component_mask = mask_array[min_y : max_y + 1, min_x : max_x + 1]
        component_region = result_array[min_y : max_y + 1, min_x : max_x + 1]
        component_region[component_mask] = fill_color

    filled = Image.fromarray(result_array, mode="RGB")
    feathered_mask = mask.filter(ImageFilter.GaussianBlur(radius=1.25))
    return Image.composite(filled, source, feathered_mask)


@dataclass(slots=True)
class InpaintResult:
    ok: bool = True
    configured_engine_id: str = DEFAULT_INPAINT_ENGINE_ID
    runtime_engine_id: str = "opencv-telea"
    mask_pixels: int = 0
    error_message: str = ""

    def to_dict(self) -> dict[str, object]:
        return {
            "ok": self.ok,
            "configured_engine_id": self.configured_engine_id,
            "runtime_engine_id": self.runtime_engine_id,
            "mask_pixels": self.mask_pixels,
            "error_message": self.error_message,
        }


class InpaintEngine:
    stage = "inpaint"

    def __init__(self, engine_id: str | None = None, device: str | None = None) -> None:
        self.engine_id = str(engine_id or DEFAULT_INPAINT_ENGINE_ID)
        self.device = str(device or "auto")

    def configure(self, engine_id: str | None = None, device: str | None = None) -> None:
        if engine_id:
            self.engine_id = str(engine_id)
        if device is not None and str(device).strip():
            self.device = str(device).strip()

    def describe(self) -> dict[str, object]:
        runtime_status = get_runtime_asset_status(self.engine_id)
        device_status = get_runtime_device_status(self.device)
        runtime_engine_id = runtime_status.runtime_engine_id if runtime_status.available else "opencv-telea"
        if not runtime_status.available and self.engine_id == "lama-manga":
            runtime_engine_id = "opencv-ns" if _has_opencv_inpaint_support() else "pil-median-fallback"
        elif not runtime_status.available and not _has_opencv_inpaint_support():
            runtime_engine_id = "pil-median-fallback"
        return {
            "configured_engine_id": self.engine_id,
            "configured_device": device_status.configured,
            "resolved_device": device_status.resolved,
            "device": device_status.to_dict(),
            "runtime_engine_id": runtime_engine_id,
            "supported_engine_ids": [DEFAULT_INPAINT_ENGINE_ID, *ALTERNATIVE_INPAINT_ENGINE_IDS],
        }

    def run(
        self,
        *,
        source_path: str | Path,
        segment_mask_path: str | Path,
        output_path: str | Path,
        brush_mask_path: str | Path | None = None,
    ) -> InpaintResult:
        source_path = Path(source_path)
        output_path = Path(output_path)

        with Image.open(source_path) as source_image:
            source = source_image.convert("RGB")

        segment_mask = _load_mask(segment_mask_path, source.size)
        brush_mask = _load_mask(brush_mask_path, source.size)
        cleanup_mask = ImageChops.lighter(segment_mask, brush_mask).filter(ImageFilter.MaxFilter(size=5))
        mask_pixels = int(np.count_nonzero(np.array(cleanup_mask)))

        output_path.parent.mkdir(parents=True, exist_ok=True)
        if mask_pixels == 0:
            source.save(output_path, format="PNG")
            return InpaintResult(
                ok=True,
                configured_engine_id=self.engine_id,
                runtime_engine_id="copy-source",
                mask_pixels=0,
            )

        runtime_status = get_runtime_asset_status(self.engine_id)
        if runtime_status.available:
            try:
                runtime_result = run_inpaint_runtime(
                    source_path=source_path,
                    segment_mask_path=segment_mask_path,
                    brush_mask_path=brush_mask_path,
                    output_path=output_path,
                    engine_id=self.engine_id,
                    device=self.device,
                )
                if runtime_result is not None:
                    return InpaintResult(
                        ok=True,
                        configured_engine_id=self.engine_id,
                        runtime_engine_id=runtime_result.runtime_engine_id,
                        mask_pixels=runtime_result.mask_pixels,
                    )
            except Exception:
                pass

        if _has_opencv_inpaint_support():
            try:
                mask_array = np.array(cleanup_mask, dtype=np.uint8)
                source_array = np.array(source, dtype=np.uint8)
                source_bgr = cv2.cvtColor(source_array, cv2.COLOR_RGB2BGR)
                if self.engine_id == "lama-manga":
                    inpaint_radius = 5
                    inpaint_flags = cv2.INPAINT_NS
                    opencv_runtime_engine_id = "opencv-ns"
                else:
                    inpaint_radius = 3
                    inpaint_flags = cv2.INPAINT_TELEA
                    opencv_runtime_engine_id = "opencv-telea"
                result_bgr = cv2.inpaint(source_bgr, mask_array, inpaint_radius, inpaint_flags)
                result_image = Image.fromarray(cv2.cvtColor(result_bgr, cv2.COLOR_BGR2RGB))
                runtime_engine_id = opencv_runtime_engine_id
            except Exception:
                # OpenCV 可能仅存在 Python 命名空间，或底层 DLL/API 已损坏；此时保留 PIL 结果。
                runtime_engine_id = "pil-median-fallback"
                result_image = _pil_fallback_inpaint(source, cleanup_mask)
        else:
            runtime_engine_id = "pil-median-fallback"
            result_image = _pil_fallback_inpaint(source, cleanup_mask)

        result_image.save(output_path, format="PNG")
        return InpaintResult(
            ok=True,
            configured_engine_id=self.engine_id,
            runtime_engine_id=runtime_engine_id,
            mask_pixels=mask_pixels,
        )
