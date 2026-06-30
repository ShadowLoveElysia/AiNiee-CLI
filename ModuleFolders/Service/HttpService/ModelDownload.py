from __future__ import annotations

import argparse
import hashlib
import inspect
import json
import os
import re
import shutil
import sys
import threading
from collections.abc import Callable
from contextlib import nullcontext
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Iterable

import requests

from ModuleFolders.MangaCore.pipeline.modelCatalog import DEFAULT_OCR_MODEL_ID, normalize_model_id


DEFAULT_MODEL_IDS: tuple[str, ...] = (
    "comic-text-bubble-detector",
    "comic-text-detector",
    DEFAULT_OCR_MODEL_ID,
    "aot-inpainting",
)

REQUEST_TIMEOUT: tuple[int, int] = (10, 60)
CHUNK_SIZE = 1024 * 1024
PROGRESS_INTERVAL_BYTES = 8 * 1024 * 1024
_RICH_CONSOLE: Any | None = None
_RICH_UNAVAILABLE = False

DownloadProgressCallback = Callable[[dict[str, object]], None]


@dataclass(frozen=True, slots=True)
class ArchiveMember:
    source: str
    destination: str


@dataclass(frozen=True, slots=True)
class DownloadAsset:
    asset_id: str
    model_ids: tuple[str, ...]
    urls: tuple[str, ...]
    destination: str = ""
    sha256: str = ""
    filename: str = ""
    archive_members: tuple[ArchiveMember, ...] = ()
    hf_repo_id: str = ""
    hf_destination: str = ""
    hf_required_files: tuple[str, ...] = ()

    @property
    def is_archive(self) -> bool:
        return bool(self.archive_members)

    @property
    def is_hf_snapshot(self) -> bool:
        return bool(self.hf_repo_id)


@dataclass(frozen=True, slots=True)
class DownloadResult:
    asset_id: str
    downloaded: bool = False
    skipped: bool = False
    extracted: bool = False
    snapshot: bool = False


MANGA_TRANSLATOR_RELEASE = "https://github.com/zyddnys/manga-image-translator/releases/download/beta-0.3"
MANGA_TRANSLATOR_MODELSCOPE = "https://www.modelscope.cn/models/hgmzhn/manga-translator-ui/resolve/master"
HF_RESOLVE = "https://huggingface.co"
DEFAULT_HF_ENDPOINT = "https://hf-mirror.com"
PADDLEOCR_VL_MANGA_REPO_ID = "jzhang533/PaddleOCR-VL-For-Manga"
PADDLEOCR_VL_MANGA_DESTINATION = "ocr/PaddleOCR-VL-1.5"
PADDLEOCR_VL_MANGA_REQUIRED_FILES = (
    "config.json",
    "model.safetensors",
    "preprocessor_config.json",
    "processing_paddleocr_vl.py",
    "tokenizer.json",
    "tokenizer_config.json",
)


def _ensure_huggingface_endpoint() -> str:
    endpoint = (os.environ.get("HF_ENDPOINT") or DEFAULT_HF_ENDPOINT).rstrip("/")
    os.environ["HF_ENDPOINT"] = endpoint
    os.environ.setdefault("HF_HUB_ENDPOINT", endpoint)
    os.environ.setdefault("HF_HUB_DISABLE_SYMLINKS_WARNING", "1")
    return endpoint


def _rewrite_huggingface_url(url: str, endpoint: str) -> str:
    endpoint = endpoint.rstrip("/")
    origin = HF_RESOLVE.rstrip("/")
    if url == origin:
        return endpoint
    prefix = f"{origin}/"
    if url.startswith(prefix):
        return f"{endpoint}/{url[len(prefix):]}"
    return url


def _resolve_download_urls(url: str) -> tuple[str, ...]:
    endpoint = _ensure_huggingface_endpoint()
    origin = HF_RESOLVE.rstrip("/")
    if url != origin and not url.startswith(f"{origin}/"):
        return (url,)

    candidates: list[str] = []
    for candidate_endpoint in (endpoint, DEFAULT_HF_ENDPOINT, origin):
        candidate = _rewrite_huggingface_url(url, candidate_endpoint)
        if candidate not in candidates:
            candidates.append(candidate)
    return tuple(candidates)


def _resolve_download_url(url: str) -> str:
    return _resolve_download_urls(url)[0]


ASSETS: tuple[DownloadAsset, ...] = (
    DownloadAsset(
        asset_id="comic-text-bubble-detector-torch",
        model_ids=("comic-text-bubble-detector",),
        urls=(
            f"{MANGA_TRANSLATOR_RELEASE}/comictextdetector.pt",
            f"{MANGA_TRANSLATOR_MODELSCOPE}/comictextdetector.pt",
        ),
        destination="detection/comictextdetector.pt",
        sha256="1f90fa60aeeb1eb82e2ac1167a66bf139a8a61b8780acd351ead55268540cccb",
    ),
    DownloadAsset(
        asset_id="comic-text-bubble-detector-onnx",
        model_ids=("comic-text-bubble-detector",),
        urls=(
            f"{MANGA_TRANSLATOR_RELEASE}/comictextdetector.pt.onnx",
            f"{MANGA_TRANSLATOR_MODELSCOPE}/comictextdetector.pt.onnx",
        ),
        destination="detection/comictextdetector.pt.onnx",
        sha256="1a86ace74961413cbd650002e7bb4dcec4980ffa21b2f19b86933372071d718f",
    ),
    DownloadAsset(
        asset_id="comic-text-detector-yolo",
        model_ids=("comic-text-detector",),
        urls=(f"{HF_RESOLVE}/mayocream/comic-text-detector/resolve/main/yolo-v5.safetensors",),
        destination="huggingface/mayocream/comic-text-detector/yolo-v5.safetensors",
    ),
    DownloadAsset(
        asset_id="comic-text-detector-unet",
        model_ids=("comic-text-detector",),
        urls=(f"{HF_RESOLVE}/mayocream/comic-text-detector/resolve/main/unet.safetensors",),
        destination="huggingface/mayocream/comic-text-detector/unet.safetensors",
    ),
    DownloadAsset(
        asset_id="comic-text-detector-dbnet",
        model_ids=("comic-text-detector",),
        urls=(f"{HF_RESOLVE}/mayocream/comic-text-detector/resolve/main/dbnet.safetensors",),
        destination="huggingface/mayocream/comic-text-detector/dbnet.safetensors",
    ),
    DownloadAsset(
        asset_id="paddleocr-vl-1.5-model",
        model_ids=("paddleocr-vl-1.5",),
        urls=(),
        hf_repo_id=PADDLEOCR_VL_MANGA_REPO_ID,
        hf_destination=PADDLEOCR_VL_MANGA_DESTINATION,
        hf_required_files=PADDLEOCR_VL_MANGA_REQUIRED_FILES,
    ),
    DownloadAsset(
        asset_id="mit48px-color-model",
        model_ids=(
            "paddleocr-vl-1.5",
            "mit48px-ocr",
            "manga-ocr",
            "paddleocr",
            "paddleocr_korean",
            "paddleocr_latin",
            "paddleocr_thai",
        ),
        urls=(
            f"{MANGA_TRANSLATOR_RELEASE}/ocr_ar_48px.ckpt",
            f"{MANGA_TRANSLATOR_MODELSCOPE}/ocr_ar_48px.ckpt",
        ),
        destination="ocr/ocr_ar_48px.ckpt",
        sha256="29daa46d080818bb4ab239a518a88338cbccff8f901bef8c9db191a7cb97671d",
    ),
    DownloadAsset(
        asset_id="mit48px-color-dict",
        model_ids=(
            "paddleocr-vl-1.5",
            "mit48px-ocr",
            "manga-ocr",
            "paddleocr",
            "paddleocr_korean",
            "paddleocr_latin",
            "paddleocr_thai",
        ),
        urls=(
            f"{MANGA_TRANSLATOR_RELEASE}/alphabet-all-v7.txt",
            f"{MANGA_TRANSLATOR_MODELSCOPE}/alphabet-all-v7.txt",
        ),
        destination="ocr/alphabet-all-v7.txt",
        sha256="f5722368146aa0fbcc9f4726866e4efc3203318ebb66c811d8cbbe915576538a",
    ),
    DownloadAsset(
        asset_id="ocr32px-legacy",
        model_ids=("32px",),
        urls=(
            f"{MANGA_TRANSLATOR_RELEASE}/ocr.zip",
            f"{MANGA_TRANSLATOR_MODELSCOPE}/ocr.zip",
        ),
        filename="ocr.zip",
        sha256="47405638b96fa2540a5ee841a4cd792f25062c09d9458a973362d40785f95d7a",
        archive_members=(
            ArchiveMember("ocr.ckpt", "ocr/ocr.ckpt"),
            ArchiveMember("alphabet-all-v5.txt", "ocr/alphabet-all-v5.txt"),
        ),
    ),
    DownloadAsset(
        asset_id="ocr48px-ctc",
        model_ids=("48px_ctc",),
        urls=(
            f"{MANGA_TRANSLATOR_RELEASE}/ocr-ctc.zip",
            f"{MANGA_TRANSLATOR_MODELSCOPE}/ocr-ctc.zip",
        ),
        filename="ocr-ctc.zip",
        sha256="fc61c52f7a811bc72c54f6be85df814c6b60f63585175db27cb94a08e0c30101",
        archive_members=(
            ArchiveMember("ocr-ctc.ckpt", "ocr/ocr-ctc.ckpt"),
            ArchiveMember("alphabet-all-v5.txt", "ocr/alphabet-all-v5.txt"),
        ),
    ),
    DownloadAsset(
        asset_id="manga-ocr-model",
        model_ids=("manga-ocr",),
        urls=(
            "https://github.com/hgmzhn/manga-translator-ui/releases/download/v1.9.5/manga_ocr_model.7z",
            f"{MANGA_TRANSLATOR_MODELSCOPE}/manga_ocr_model.7z",
        ),
        filename="manga_ocr_model.7z",
        sha256="5dc27bde275ad981818a06de92a77da18383c0db9b915d6faff2b8acbfe35475",
        archive_members=(
            ArchiveMember("manga_ocr_model/config.json", "ocr/manga_ocr/config.json"),
            ArchiveMember("manga_ocr_model/preprocessor_config.json", "ocr/manga_ocr/preprocessor_config.json"),
            ArchiveMember("manga_ocr_model/pytorch_model.bin", "ocr/manga_ocr/pytorch_model.bin"),
            ArchiveMember("manga_ocr_model/special_tokens_map.json", "ocr/manga_ocr/special_tokens_map.json"),
            ArchiveMember("manga_ocr_model/tokenizer_config.json", "ocr/manga_ocr/tokenizer_config.json"),
            ArchiveMember("manga_ocr_model/vocab.txt", "ocr/manga_ocr/vocab.txt"),
        ),
    ),
    DownloadAsset(
        asset_id="ppocrv5-ch-onnx",
        model_ids=("paddleocr",),
        urls=(
            "https://github.com/hgmzhn/manga-translator-ui/releases/download/v1.7.1/ch_PP-OCRv5_rec_server_infer.onnx",
            f"{MANGA_TRANSLATOR_MODELSCOPE}/ch_PP-OCRv5_rec_server_infer.onnx",
        ),
        destination="ocr/ch_PP-OCRv5_rec_server_infer.onnx",
        sha256="e09385400eaaaef34ceff54aeb7c4f0f1fe014c27fa8b9905d4709b65746562a",
    ),
    DownloadAsset(
        asset_id="ppocrv5-ch-dict",
        model_ids=("paddleocr",),
        urls=(
            "https://github.com/hgmzhn/manga-translator-ui/releases/download/v1.7.1/ppocrv5_dict.txt",
            f"{MANGA_TRANSLATOR_MODELSCOPE}/ppocrv5_dict.txt",
        ),
        destination="ocr/ppocrv5_dict.txt",
        sha256="d1979e9f794c464c0d2e0b70a7fe14dd978e9dc644c0e71f14158cdf8342af1b",
    ),
    DownloadAsset(
        asset_id="ppocrv5-korean-onnx",
        model_ids=("paddleocr_korean",),
        urls=(
            "https://github.com/hgmzhn/manga-translator-ui/releases/download/v1.7.1/korean_PP-OCRv5_rec_mobile_infer.onnx",
            f"{MANGA_TRANSLATOR_MODELSCOPE}/korean_PP-OCRv5_rec_mobile_infer.onnx",
        ),
        destination="ocr/korean_PP-OCRv5_rec_mobile_infer.onnx",
        sha256="cd6e2ea50f6943ca7271eb8c56a877a5a90720b7047fe9c41a2e541a25773c9b",
    ),
    DownloadAsset(
        asset_id="ppocrv5-korean-dict",
        model_ids=("paddleocr_korean",),
        urls=(
            "https://github.com/hgmzhn/manga-translator-ui/releases/download/v1.7.1/ppocrv5_korean_dict.txt",
            f"{MANGA_TRANSLATOR_MODELSCOPE}/ppocrv5_korean_dict.txt",
        ),
        destination="ocr/ppocrv5_korean_dict.txt",
        sha256="a88071c68c01707489baa79ebe0405b7beb5cca229f4fc94cc3ef992328802d7",
    ),
    DownloadAsset(
        asset_id="ppocrv5-latin-onnx",
        model_ids=("paddleocr_latin",),
        urls=(
            "https://github.com/hgmzhn/manga-translator-ui/releases/download/v1.8.0/latin_PP-OCRv5_rec_mobile_infer.onnx",
            f"{MANGA_TRANSLATOR_MODELSCOPE}/latin_PP-OCRv5_rec_mobile_infer.onnx",
        ),
        destination="ocr/latin_PP-OCRv5_rec_mobile_infer.onnx",
        sha256="614ffc2d6d3902d360fad7f1b0dd455ee45e877069d14c4e51a99dc4ef144409",
    ),
    DownloadAsset(
        asset_id="ppocrv5-latin-dict",
        model_ids=("paddleocr_latin",),
        urls=(
            "https://github.com/hgmzhn/manga-translator-ui/releases/download/v1.8.0/ppocrv5_latin_dict.txt",
            f"{MANGA_TRANSLATOR_MODELSCOPE}/ppocrv5_latin_dict.txt",
        ),
        destination="ocr/ppocrv5_latin_dict.txt",
        sha256="3c0a8a79b612653c25f765271714f71281e4e955962c153e272b7b8c1d2b13ff",
    ),
    DownloadAsset(
        asset_id="ppocrv5-thai-onnx",
        model_ids=("paddleocr_thai",),
        urls=(
            f"{MANGA_TRANSLATOR_MODELSCOPE}/thai_PP-OCRv5_rec_mobile_infer.onnx",
        ),
        destination="ocr/thai_PP-OCRv5_rec_mobile_infer.onnx",
        sha256="2b6e56b1872200349e227574c25aeb0e0f9af9b8356e9ff5f75ac543a535669a",
    ),
    DownloadAsset(
        asset_id="ppocrv5-thai-dict",
        model_ids=("paddleocr_thai",),
        urls=(
            f"{MANGA_TRANSLATOR_MODELSCOPE}/ppocrv5_thai_dict.txt",
        ),
        destination="ocr/ppocrv5_thai_dict.txt",
        sha256="57f5406f94bb6688fb7077f7be65f08bbd71cecf48c01ea26c522cb5c4836b7a",
    ),
    DownloadAsset(
        asset_id="aot-inpainting",
        model_ids=("aot-inpainting",),
        urls=(
            f"{MANGA_TRANSLATOR_RELEASE}/inpainting.ckpt",
            f"{MANGA_TRANSLATOR_MODELSCOPE}/inpainting.ckpt",
        ),
        destination="inpainting/inpainting.ckpt",
        sha256="878d541c68648969bc1b042a6e997f3a58e49b6c07c5636ad55130736977149f",
    ),
)

MODEL_STORAGE_DIRS: dict[str, str] = {
    "comic-text-bubble-detector": "detection",
    "comic-text-detector": "huggingface/mayocream/comic-text-detector",
    "32px": "ocr",
    "48px_ctc": "ocr",
    "paddleocr-vl-1.5": "ocr",
    "manga-ocr": "ocr",
    "mit48px-ocr": "ocr",
    "paddleocr": "ocr",
    "paddleocr_korean": "ocr",
    "paddleocr_latin": "ocr",
    "paddleocr_thai": "ocr",
    "aot-inpainting": "inpainting",
}


def _project_root() -> Path:
    return Path(__file__).resolve().parents[3]


def _default_model_root(project_root: Path) -> Path:
    return project_root / "Resource" / "Models" / "MangaCore"


def _safe_name(value: str) -> str:
    return re.sub(r"[^a-z0-9._-]+", "-", value.lower()).strip("-") or "model"


def _now_iso() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")


def _get_rich_console() -> Any | None:
    global _RICH_CONSOLE, _RICH_UNAVAILABLE
    if _RICH_UNAVAILABLE:
        return None
    if _RICH_CONSOLE is not None:
        return _RICH_CONSOLE
    try:
        from rich.console import Console
    except Exception:
        _RICH_UNAVAILABLE = True
        return None
    _RICH_CONSOLE = Console()
    return _RICH_CONSOLE


def _print(message: str, *, style: str = "") -> None:
    console = _get_rich_console()
    if console is None:
        print(message)
    elif style:
        console.print(message, style=style)
    else:
        console.print(message)


def _print_download_panel(root_dir: Path, model_ids: Iterable[str], asset_count: int) -> None:
    console = _get_rich_console()
    if console is None:
        print(f"[MangaCore] Model root: {root_dir}")
        print(f"[MangaCore] Target models: {', '.join(model_ids)}")
        print("[MangaCore] Download method: requests + Hugging Face snapshot")
        return

    from rich.panel import Panel

    body = "\n".join(
        (
            f"[bold]Model root[/bold]: {root_dir}",
            f"[bold]Target models[/bold]: {', '.join(model_ids)}",
            "[bold]Download method[/bold]: requests + Hugging Face snapshot",
            f"[bold]Assets[/bold]: {asset_count}",
        )
    )
    console.print(Panel(body, title="Download", border_style="cyan", expand=False))


def _print_download_summary(results: Iterable[DownloadResult], registered_count: int) -> None:
    result_list = tuple(results)
    total = len(result_list)
    downloaded = sum(1 for result in result_list if result.downloaded)
    skipped = sum(1 for result in result_list if result.skipped)
    extracted = sum(1 for result in result_list if result.extracted)
    snapshots = sum(1 for result in result_list if result.snapshot)
    console = _get_rich_console()
    if console is None:
        print(
            "[MangaCore] Download summary: "
            f"assets={total}, network_downloads={downloaded}, skipped={skipped}, "
            f"extracted={extracted}, snapshots={snapshots}, registered_models={registered_count}"
        )
        return

    from rich.panel import Panel

    body = "\n".join(
        (
            f"[bold]Assets processed[/bold]: {total}",
            f"[bold]Network downloads[/bold]: {downloaded}",
            f"[bold]Skipped existing assets[/bold]: {skipped}",
            f"[bold]Archives extracted[/bold]: {extracted}",
            f"[bold]Snapshots prepared[/bold]: {snapshots}",
            f"[bold]Registered models[/bold]: {registered_count}",
        )
    )
    console.print(Panel(body, title="Download Summary", border_style="green", expand=False))


def _emit_progress(callback: DownloadProgressCallback | None, **payload: object) -> None:
    if callback is None:
        return
    try:
        callback({key: value for key, value in payload.items() if value is not None})
    except Exception:
        return


class DownloadDisplay:
    def __init__(
        self,
        root_dir: Path,
        model_ids: Iterable[str],
        assets: Iterable[DownloadAsset],
        *,
        quiet: bool = False,
        progress_callback: DownloadProgressCallback | None = None,
    ) -> None:
        self.root_dir = root_dir
        self.model_ids = tuple(model_ids)
        self.assets = tuple(assets)
        self.quiet = quiet
        self.progress_callback = progress_callback
        self.console = None if quiet else _get_rich_console()
        self.live: Any | None = None
        self.progress: Any | None = None
        self.task_id: Any | None = None
        self.current_index = -1
        self.current_asset: DownloadAsset | None = None
        self.current_url = ""
        self.status = "Waiting to start"
        self.current_downloaded = 0
        self.current_total = 0
        self.current_unit = "bytes"
        self.enabled = self.console is not None
        self.handles_events = self.enabled or self.quiet or self.progress_callback is not None

        if self.enabled:
            from rich.progress import (
                BarColumn,
                DownloadColumn,
                Progress,
                TaskProgressColumn,
                TextColumn,
                TimeRemainingColumn,
                TransferSpeedColumn,
            )

            self.progress = Progress(
                TextColumn("[bold cyan]{task.description}"),
                BarColumn(),
                TaskProgressColumn(),
                DownloadColumn(),
                TransferSpeedColumn(),
                TimeRemainingColumn(),
                console=self.console,
                expand=True,
            )

    def __enter__(self) -> DownloadDisplay:
        self._emit(
            "started",
            message="Preparing MangaCore model assets.",
            item_index=0,
            item_count=len(self.assets),
        )
        if not self.enabled:
            return self
        from rich.live import Live

        self.live = Live(self._render(), console=self.console, refresh_per_second=8)
        self.live.start()
        return self

    def __exit__(self, _exc_type: object, _exc: object, _traceback: object) -> None:
        self._remove_task()
        self.current_index = len(self.assets)
        self.current_asset = None
        self.current_url = ""
        failed = _exc_type is not None
        self.status = "Failed" if failed else "Completed"
        self.current_downloaded = 0
        self.current_total = 0
        self.current_unit = "bytes"
        self._emit(
            "failed" if failed else "completed",
            message=str(_exc) if failed and _exc is not None else "MangaCore model assets are ready.",
            item_index=len(self.assets),
            item_count=len(self.assets),
        )
        if self.live is None:
            return
        self.live.update(self._render())
        self.live.stop()
        if self.console is not None:
            self.console.print()

    def start_asset(self, index: int, asset: DownloadAsset) -> None:
        self.current_index = index
        self.current_asset = asset
        self.current_url = ""
        self.status = "Preparing"
        self.current_downloaded = 0
        self.current_total = 0
        self.current_unit = "bytes"
        self._remove_task()
        self._emit("asset", message=f"Preparing asset: {asset.asset_id}")
        self._refresh()

    def start_download(self, url: str, total: int) -> None:
        if self.current_asset is not None:
            self.current_url = url
            self.status = "Downloading"
            self.current_downloaded = 0
            self.current_total = max(0, int(total or 0))
            self.current_unit = "bytes"
            self._emit("downloading", message=f"Downloading asset: {self.current_asset.asset_id}")
        if not self.enabled or self.progress is None or self.current_asset is None:
            return
        self._remove_task()
        self.task_id = self.progress.add_task(
            self.current_asset.asset_id,
            total=total if total else None,
        )
        self._refresh()

    def start_unit_progress(self, description: str, total: int) -> None:
        self.current_url = ""
        self.status = description
        self.current_downloaded = 0
        self.current_total = max(0, int(total or 0))
        self.current_unit = "files"
        self._emit("snapshot", message=description)
        if not self.enabled or self.progress is None:
            return
        self._remove_task()
        self.task_id = self.progress.add_task(
            description,
            total=total if total else None,
        )
        self._refresh()

    def advance(self, completed_bytes: int) -> None:
        increment = max(0, int(completed_bytes or 0))
        self.current_downloaded += increment
        if self.progress is not None and self.task_id is not None:
            self.progress.update(self.task_id, advance=increment)
        self._emit("downloading" if self.current_url else "snapshot", message=self.status)

    def set_status(self, status: str, *, url: str = "") -> None:
        self.status = status
        if url:
            self.current_url = url
        self._emit("status", message=status)
        self._refresh()

    def finish_current(self, status: str) -> None:
        self._remove_task()
        self.status = status
        self.current_url = ""
        if self.current_total and self.current_downloaded < self.current_total:
            self.current_downloaded = self.current_total
        self._emit("asset_completed", message=status)
        self._refresh()

    def _remove_task(self) -> None:
        if self.progress is not None and self.task_id is not None:
            self.progress.remove_task(self.task_id)
        self.task_id = None

    def _waiting_assets(self) -> tuple[DownloadAsset, ...]:
        next_index = max(self.current_index + 1, 0)
        return self.assets[next_index:]

    def _render(self) -> Any:
        if not self.enabled:
            return ""

        from rich.console import Group
        from rich.panel import Panel
        from rich.text import Text

        lines: list[Any] = [
            f"[bold]Model root[/bold]: {self.root_dir}",
            f"[bold]Target models[/bold]: {', '.join(self.model_ids)}",
            "[bold]Download method[/bold]: requests + Hugging Face snapshot",
            f"[bold]Assets[/bold]: {len(self.assets)}",
        ]

        if self.current_asset is None:
            lines.append(f"[bold]Current[/bold]: {self.status}")
        else:
            lines.extend(
                (
                    "",
                    f"[bold]Current[/bold]: [{self.current_index + 1}/{len(self.assets)}] "
                    f"{self.current_asset.asset_id}",
                    f"[bold]Status[/bold]: {self.status}",
                )
            )
            if self.current_url:
                lines.append(f"[dim]URL[/dim]: {self.current_url}")
            if self.progress is not None and self.task_id is not None:
                lines.append(self.progress)

        waiting_assets = self._waiting_assets()
        lines.append("")
        if waiting_assets:
            visible = waiting_assets[:8]
            waiting_text = "\n".join(f"  - {asset.asset_id}" for asset in visible)
            hidden_count = len(waiting_assets) - len(visible)
            if hidden_count:
                waiting_text += f"\n  ... {hidden_count} more"
            lines.append(Text("Waiting", style="bold"))
            lines.append(Text(waiting_text, style="dim"))
        else:
            lines.append(Text("Waiting: none", style="dim"))

        return Panel(Group(*lines), title="Download", border_style="cyan")

    def _refresh(self) -> None:
        if self.live is not None:
            self.live.update(self._render())

    def _emit(self, stage: str, *, message: str = "", item_index: int | None = None, item_count: int | None = None) -> None:
        asset = self.current_asset
        _emit_progress(
            self.progress_callback,
            kind="manga_model_download",
            stage=stage,
            message=message or self.status,
            root_dir=str(self.root_dir),
            model_ids=list(self.model_ids),
            asset_id=asset.asset_id if asset is not None else "",
            total=item_count if item_count is not None else len(self.assets),
            item_index=item_index if item_index is not None else (self.current_index + 1 if asset is not None else 0),
            item_count=item_count if item_count is not None else len(self.assets),
            url=self.current_url,
            bytes_downloaded=self.current_downloaded,
            bytes_total=self.current_total,
            unit=self.current_unit,
        )


def _asset_filename(asset: DownloadAsset) -> str:
    if asset.filename:
        return asset.filename
    url_path = asset.urls[0].split("?", 1)[0].rstrip("/")
    return Path(url_path).name or _safe_name(asset.asset_id)


def _download_cache_dir(root_dir: Path) -> Path:
    return root_dir / ".downloads"


def _hf_snapshot_marker_path(root_dir: Path, asset: DownloadAsset) -> Path:
    return root_dir / asset.hf_destination / ".ainiee_hf_snapshot.json"


def _archive_download_path(root_dir: Path, asset: DownloadAsset) -> Path:
    return _download_cache_dir(root_dir) / _asset_filename(asset)


def _asset_download_path(root_dir: Path, asset: DownloadAsset) -> Path:
    if asset.is_hf_snapshot:
        return root_dir / asset.hf_destination
    if asset.is_archive:
        return _archive_download_path(root_dir, asset)
    return root_dir / asset.destination


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        while chunk := handle.read(CHUNK_SIZE):
            digest.update(chunk)
    return digest.hexdigest()


def _verify_hash(path: Path, expected_sha256: str) -> None:
    if not expected_sha256:
        return
    actual = _sha256_file(path).lower()
    expected = expected_sha256.lower()
    if actual != expected:
        raise ValueError(f"sha256 mismatch for {path}: expected {expected}, got {actual}")


def _is_file_ready(path: Path, expected_sha256: str = "") -> bool:
    if not path.is_file():
        return False
    if not expected_sha256:
        return True
    try:
        _verify_hash(path, expected_sha256)
    except Exception:
        return False
    return True


def _atomic_write_json(path: Path, payload: dict[str, object]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp_path = path.with_name(f"{path.name}.tmp")
    with open(temp_path, "w", encoding="utf-8") as handle:
        json.dump(payload, handle, ensure_ascii=False, indent=2)
        handle.write("\n")
        handle.flush()
        os.fsync(handle.fileno())
    os.replace(temp_path, path)


def _archive_targets(root_dir: Path, asset: DownloadAsset) -> list[Path]:
    return [root_dir / member.destination for member in asset.archive_members]


def _hf_snapshot_ready(asset: DownloadAsset, root_dir: Path) -> bool:
    destination = root_dir / asset.hf_destination
    if not asset.is_hf_snapshot or not destination.is_dir():
        return False
    if any(not (destination / filename).is_file() for filename in asset.hf_required_files):
        return False

    marker_path = _hf_snapshot_marker_path(root_dir, asset)
    try:
        marker = json.loads(marker_path.read_text(encoding="utf-8"))
    except Exception:
        return False
    return marker.get("repo_id") == asset.hf_repo_id


def is_asset_ready(asset: DownloadAsset, root_dir: str | Path) -> bool:
    root = Path(root_dir)
    if asset.is_hf_snapshot:
        return _hf_snapshot_ready(asset, root)
    if asset.is_archive:
        return all(target.exists() for target in _archive_targets(root, asset))
    return _is_file_ready(root / asset.destination, asset.sha256)


def resolve_assets_for_model_ids(model_ids: Iterable[str]) -> list[DownloadAsset]:
    requested = tuple(dict.fromkeys(normalize_model_id(str(model_id)) for model_id in model_ids))
    known_model_ids = {model_id for asset in ASSETS for model_id in asset.model_ids}
    unknown = [model_id for model_id in requested if model_id not in known_model_ids]
    if unknown:
        raise KeyError(f"Unknown MangaCore downloadable model ids: {', '.join(unknown)}")

    resolved: list[DownloadAsset] = []
    seen_asset_ids: set[str] = set()
    for model_id in requested:
        for asset in ASSETS:
            if model_id in asset.model_ids and asset.asset_id not in seen_asset_ids:
                resolved.append(asset)
                seen_asset_ids.add(asset.asset_id)
    return resolved


def _cleanup_partial(path: Path) -> None:
    partial_path = path.with_name(f"{path.name}.part")
    for candidate in (partial_path, path):
        if candidate.exists() and candidate.is_file():
            candidate.unlink()


def _stream_response_plain(response: requests.Response, partial_path: Path, total: int) -> int:
    downloaded = 0
    next_progress = PROGRESS_INTERVAL_BYTES
    with open(partial_path, "wb") as handle:
        for chunk in response.iter_content(chunk_size=CHUNK_SIZE):
            if not chunk:
                continue
            handle.write(chunk)
            downloaded += len(chunk)
            if total and downloaded >= next_progress:
                percent = min(downloaded / total * 100, 100)
                print(
                    f"      {downloaded / 1024 / 1024:.1f}MB / "
                    f"{total / 1024 / 1024:.1f}MB ({percent:.1f}%)",
                    end="\r",
                    flush=True,
                )
                next_progress += PROGRESS_INTERVAL_BYTES
    if total:
        print(" " * 79, end="\r")
    return downloaded


def _stream_response_rich(response: requests.Response, partial_path: Path, total: int, asset_id: str) -> int:
    console = _get_rich_console()
    if console is None:
        return _stream_response_plain(response, partial_path, total)

    from rich.progress import (
        BarColumn,
        DownloadColumn,
        Progress,
        TaskProgressColumn,
        TextColumn,
        TimeRemainingColumn,
        TransferSpeedColumn,
    )

    downloaded = 0
    progress = Progress(
        TextColumn("[bold cyan]{task.description}"),
        BarColumn(),
        TaskProgressColumn(),
        DownloadColumn(),
        TransferSpeedColumn(),
        TimeRemainingColumn(),
        console=console,
    )
    with progress:
        task_id = progress.add_task(asset_id, total=total if total else None)
        with open(partial_path, "wb") as handle:
            for chunk in response.iter_content(chunk_size=CHUNK_SIZE):
                if not chunk:
                    continue
                handle.write(chunk)
                downloaded += len(chunk)
                progress.update(task_id, advance=len(chunk))
    return downloaded


def _stream_response_display(response: requests.Response, partial_path: Path, display: DownloadDisplay) -> int:
    downloaded = 0
    with open(partial_path, "wb") as handle:
        for chunk in response.iter_content(chunk_size=CHUNK_SIZE):
            if not chunk:
                continue
            handle.write(chunk)
            downloaded += len(chunk)
            display.advance(len(chunk))
    return downloaded


def _download_file(
    *,
    session: requests.Session,
    asset: DownloadAsset,
    destination: Path,
    force: bool = False,
    timeout: tuple[int, int] = REQUEST_TIMEOUT,
    display: DownloadDisplay | None = None,
) -> bool:
    if _is_file_ready(destination, asset.sha256) and not force:
        if display is not None and display.handles_events:
            display.finish_current(f"Skipped: {destination}")
        else:
            _print(f"[SKIP] {asset.asset_id} -> {destination}", style="dim")
        return False

    destination.parent.mkdir(parents=True, exist_ok=True)
    partial_path = destination.with_name(f"{destination.name}.part")
    last_error: Exception | None = None

    download_urls = tuple(
        candidate
        for raw_url in asset.urls
        for candidate in _resolve_download_urls(raw_url)
    )

    for index, url in enumerate(download_urls, start=1):
        try:
            if index > 1:
                if display is not None and display.handles_events:
                    display.set_status(f"Retrying fallback URL {index}/{len(download_urls)}", url=url)
                else:
                    _print(f"[RETRY] {asset.asset_id}: fallback URL {index}/{len(download_urls)}", style="yellow")
            _cleanup_partial(destination)
            if display is None or not display.handles_events:
                _print(f"[GET] {asset.asset_id}: {url}", style="cyan")

            with session.get(url, stream=True, timeout=timeout) as response:
                response.raise_for_status()
                total = int(response.headers.get("content-length") or 0)
                if display is not None and display.handles_events:
                    display.start_download(url, total)
                    downloaded = _stream_response_display(response, partial_path, display)
                else:
                    downloaded = _stream_response_rich(response, partial_path, total, asset.asset_id)

            if total:
                if downloaded != total:
                    raise IOError(f"incomplete download: expected {total} bytes, got {downloaded}")
            _verify_hash(partial_path, asset.sha256)
            shutil.move(str(partial_path), str(destination))
            if display is not None and display.handles_events:
                display.finish_current(f"Ready: {destination}")
            else:
                _print(f"[OK] {asset.asset_id} -> {destination}", style="green")
            return True
        except Exception as exc:
            last_error = exc
            _cleanup_partial(destination)
            if index < len(download_urls):
                if display is not None and display.handles_events:
                    display.set_status(f"Download failed, switching URL: {exc}")
                else:
                    _print(f"[WARN] {asset.asset_id}: {exc}", style="yellow")
                continue
            break

    raise RuntimeError(f"Failed to download {asset.asset_id}: {last_error}") from last_error


def _extract_7z(archive_path: Path, extract_dir: Path) -> None:
    try:
        import py7zr
    except ImportError as exc:
        raise RuntimeError("py7zr is required to extract MangaCore .7z model archives") from exc

    with py7zr.SevenZipFile(archive_path, mode="r") as archive:
        archive.extractall(path=extract_dir)


def _copy_or_move_member(source: Path, destination: Path, *, force: bool) -> None:
    if destination.exists():
        if not force:
            return
        if destination.is_dir():
            shutil.rmtree(destination)
        else:
            destination.unlink()

    destination.parent.mkdir(parents=True, exist_ok=True)
    shutil.move(str(source), str(destination))


def _extract_archive(
    asset: DownloadAsset,
    archive_path: Path,
    root_dir: Path,
    *,
    force: bool = False,
    display: DownloadDisplay | None = None,
) -> bool:
    targets = _archive_targets(root_dir, asset)
    if all(target.exists() for target in targets) and not force:
        if display is not None and display.handles_events:
            display.finish_current("Skipped: extracted files already exist")
        else:
            _print(f"[SKIP] {asset.asset_id}: extracted files already exist", style="dim")
        return False

    extract_root = _download_cache_dir(root_dir) / "extracted"
    extract_dir = extract_root / _safe_name(asset.asset_id)
    if extract_dir.exists():
        shutil.rmtree(extract_dir)
    extract_dir.mkdir(parents=True, exist_ok=True)

    if display is not None and display.handles_events:
        display.set_status(f"Extracting: {archive_path}")
    else:
        _print(f"[EXTRACT] {asset.asset_id}: {archive_path}", style="cyan")
    try:
        if archive_path.suffix.lower() == ".7z":
            _extract_7z(archive_path, extract_dir)
        else:
            shutil.unpack_archive(str(archive_path), str(extract_dir))

        for member in asset.archive_members:
            source = extract_dir / member.source
            if not source.exists():
                available = "\n".join(str(path.relative_to(extract_dir)) for path in extract_dir.rglob("*"))
                raise FileNotFoundError(
                    f"Archive member {member.source!r} not found in {archive_path}. Available:\n{available}"
                )
            _copy_or_move_member(source, root_dir / member.destination, force=force)
    finally:
        if extract_dir.exists():
            shutil.rmtree(extract_dir)

    if display is not None and display.handles_events:
        display.finish_current("Extracted")
    else:
        _print(f"[OK] {asset.asset_id}: extracted", style="green")
    return True


def _copy_hf_snapshot(snapshot_path: Path, destination: Path, *, force: bool = False) -> None:
    if destination.exists():
        if force:
            if destination.is_dir():
                shutil.rmtree(destination)
            else:
                destination.unlink()
        else:
            return
    destination.parent.mkdir(parents=True, exist_ok=True)
    shutil.copytree(
        snapshot_path,
        destination,
        ignore=shutil.ignore_patterns(".git", ".cache", "*.lock"),
    )


def _write_hf_snapshot_marker(root_dir: Path, asset: DownloadAsset, snapshot_path: Path) -> None:
    marker_path = _hf_snapshot_marker_path(root_dir, asset)
    marker_path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "repo_id": asset.hf_repo_id,
        "snapshot_path": str(snapshot_path.resolve()),
        "downloaded_at": _now_iso(),
        "asset_id": asset.asset_id,
    }
    with open(marker_path, "w", encoding="utf-8") as handle:
        json.dump(payload, handle, ensure_ascii=False, indent=2)
        handle.write("\n")


def _snapshot_progress_class(display: DownloadDisplay | None):
    if display is None or not display.handles_events:
        return None

    class SnapshotProgress:
        _lock = threading.RLock()

        def __init__(self, iterable=None, *args, **kwargs) -> None:
            self.iterable = iterable
            self.total = int(kwargs.get("total") or 0)
            self.n = int(kwargs.get("initial") or 0)
            self.description = str(kwargs.get("desc") or "HF snapshot files")
            self.started = False

        def __enter__(self):
            self._start()
            return self

        def __exit__(self, _exc_type: object, _exc: object, _traceback: object) -> None:
            if self.total and self.n < self.total:
                display.advance(self.total - self.n)
                self.n = self.total

        def __iter__(self):
            if self.iterable is None:
                return iter(())
            for item in self.iterable:
                yield item
                self.update(1)

        def update(self, n: int = 1, *args, **kwargs) -> None:
            self._start()
            increment = int(n or 0)
            if increment > 0:
                self.n += increment
                display.advance(increment)

        def close(self) -> None:
            return None

        def refresh(self, *args, **kwargs) -> None:
            return None

        def reset(self, total: int | None = None, *args, **kwargs) -> None:
            if total is not None:
                self.total = int(total or 0)
            self.n = 0
            self.started = False
            self._start()

        def set_description(self, desc: str | None = None, *args, **kwargs) -> None:
            if desc:
                self.description = str(desc)
                display.set_status(self.description)

        def _start(self) -> None:
            if not self.started:
                display.start_unit_progress(self.description, self.total)
                self.started = True

        @classmethod
        def get_lock(cls):
            return cls._lock

        @classmethod
        def set_lock(cls, lock) -> None:
            cls._lock = lock

    return SnapshotProgress


def _snapshot_download(repo_id: str, cache_dir: Path, local_dir: Path, display: DownloadDisplay | None = None) -> str:
    endpoint = _ensure_huggingface_endpoint()
    try:
        from huggingface_hub import snapshot_download
    except ModuleNotFoundError as exc:
        raise RuntimeError(
            "huggingface-hub is required to download the MangaCore PaddleOCR-VL manga snapshot."
        ) from exc

    kwargs: dict[str, object] = {
        "repo_id": repo_id,
        "cache_dir": str(cache_dir),
        "local_dir": str(local_dir),
        "local_files_only": False,
    }
    try:
        signature = inspect.signature(snapshot_download)
    except (TypeError, ValueError):
        pass
    else:
        supports_kwargs = any(
            parameter.kind == inspect.Parameter.VAR_KEYWORD
            for parameter in signature.parameters.values()
        )
        if "endpoint" in signature.parameters or supports_kwargs:
            kwargs["endpoint"] = endpoint
        progress_class = _snapshot_progress_class(display)
        if progress_class is not None and ("tqdm_class" in signature.parameters or supports_kwargs):
            kwargs["tqdm_class"] = progress_class
    return str(snapshot_download(**kwargs))


def _download_hf_snapshot_asset(
    asset: DownloadAsset,
    root_dir: Path,
    *,
    force: bool = False,
    display: DownloadDisplay | None = None,
) -> DownloadResult:
    if _hf_snapshot_ready(asset, root_dir) and not force:
        if display is not None and display.handles_events:
            display.finish_current(f"Skipped: {root_dir / asset.hf_destination}")
        else:
            _print(f"[SKIP] {asset.asset_id} -> {root_dir / asset.hf_destination}", style="dim")
        return DownloadResult(asset_id=asset.asset_id, skipped=True, snapshot=True)

    if display is not None and display.handles_events:
        display.set_status(f"Downloading HF snapshot: {asset.hf_repo_id}")
    else:
        _print(f"[HF] {asset.asset_id}: {asset.hf_repo_id}", style="cyan")

    snapshot_path = Path(
        _snapshot_download(
            asset.hf_repo_id,
            _download_cache_dir(root_dir) / "huggingface-cache",
            _download_cache_dir(root_dir) / "huggingface" / _safe_name(asset.asset_id),
            display=display,
        )
    )
    destination = root_dir / asset.hf_destination
    _copy_hf_snapshot(snapshot_path, destination, force=True)

    missing = [filename for filename in asset.hf_required_files if not (destination / filename).is_file()]
    if missing:
        raise FileNotFoundError(
            f"Hugging Face snapshot {asset.hf_repo_id} is missing required files: {', '.join(missing)}"
        )

    _write_hf_snapshot_marker(root_dir, asset, snapshot_path)
    if display is not None and display.handles_events:
        display.finish_current(f"Ready: {destination}")
    else:
        _print(f"[OK] {asset.asset_id} -> {destination}", style="green")
    return DownloadResult(asset_id=asset.asset_id, downloaded=True, snapshot=True)


def download_asset(
    asset: DownloadAsset,
    root_dir: str | Path,
    *,
    force: bool = False,
    session: requests.Session | None = None,
    timeout: tuple[int, int] = REQUEST_TIMEOUT,
    display: DownloadDisplay | None = None,
    quiet: bool = False,
    progress_callback: DownloadProgressCallback | None = None,
) -> DownloadResult:
    root = Path(root_dir)
    root.mkdir(parents=True, exist_ok=True)
    local_display = display
    owns_display = False
    if local_display is None and (quiet or progress_callback is not None):
        local_display = DownloadDisplay(root, (), (asset,), quiet=quiet, progress_callback=progress_callback)
        local_display.start_asset(0, asset)
        owns_display = True

    owns_session = session is None
    active_session = session or requests.Session()
    succeeded = False
    try:
        if asset.is_hf_snapshot:
            result = _download_hf_snapshot_asset(asset, root, force=force, display=local_display)
            succeeded = True
            return result

        download_path = _asset_download_path(root, asset)
        if asset.is_archive:
            downloaded = False
            if not _is_file_ready(download_path, asset.sha256) or force:
                downloaded = _download_file(
                    session=active_session,
                    asset=asset,
                    destination=download_path,
                    force=force,
                    timeout=timeout,
                    display=local_display,
                )
            else:
                if local_display is not None and local_display.handles_events:
                    local_display.set_status(f"Skipped archive download: {download_path}")
                else:
                    _print(f"[SKIP] {asset.asset_id} archive -> {download_path}", style="dim")
            extracted = _extract_archive(asset, download_path, root, force=force, display=local_display)
            result = DownloadResult(
                asset_id=asset.asset_id,
                downloaded=downloaded,
                skipped=not downloaded and not extracted,
                extracted=extracted,
            )
            succeeded = True
            return result

        downloaded = _download_file(
            session=active_session,
            asset=asset,
            destination=download_path,
            force=force,
            timeout=timeout,
            display=local_display,
        )
        result = DownloadResult(
            asset_id=asset.asset_id,
            downloaded=downloaded,
            skipped=not downloaded,
        )
        succeeded = True
        return result
    finally:
        if owns_display and local_display is not None:
            local_display.finish_current("Completed" if succeeded else "Failed")
        if owns_session:
            active_session.close()


def _ensure_project_on_path() -> None:
    project_root = _project_root()
    project_root_str = str(project_root)
    if project_root_str not in sys.path:
        sys.path.insert(0, project_root_str)


def _registry_path(root_dir: Path, model_id: str) -> Path:
    return root_dir / "registry" / f"{_safe_name(model_id)}.json"


def _register_model(root_dir: Path, model_id: str, asset_ids: Iterable[str], *, quiet: bool = False) -> None:
    model_id = normalize_model_id(model_id)
    _ensure_project_on_path()
    from ModuleFolders.MangaCore.pipeline.modelCatalog import get_model_package

    package = get_model_package(model_id)
    storage_rel = MODEL_STORAGE_DIRS.get(model_id, "")
    storage_path = (root_dir / storage_rel) if storage_rel else root_dir
    registry_path = _registry_path(root_dir, model_id)
    payload = {
        "model_id": package.model_id,
        "repo_id": package.repo_id,
        "repo_url": package.repo_url,
        "snapshot_path": str(storage_path.resolve()),
        "downloaded_at": _now_iso(),
        "revision": f"requests:{','.join(asset_ids)}",
    }
    _atomic_write_json(registry_path, payload)
    if not quiet:
        _print(f"[REGISTRY] {model_id} -> {registry_path}", style="green")


def register_downloaded_models(
    root_dir: str | Path,
    model_ids: Iterable[str],
    assets: Iterable[DownloadAsset],
    *,
    quiet: bool = False,
    progress_callback: DownloadProgressCallback | None = None,
) -> None:
    root = Path(root_dir)
    requested_model_ids = tuple(dict.fromkeys(normalize_model_id(str(model_id)) for model_id in model_ids))
    assets_by_model: dict[str, list[str]] = {str(model_id): [] for model_id in requested_model_ids}
    for asset in assets:
        for model_id in asset.model_ids:
            if model_id in assets_by_model:
                assets_by_model[model_id].append(asset.asset_id)

    for model_id, asset_ids in assets_by_model.items():
        if asset_ids:
            _emit_progress(
                progress_callback,
                kind="manga_model_download",
                stage="registering",
                message=f"Registering model package: {model_id}",
                model_id=model_id,
                asset_ids=list(asset_ids),
            )
            _register_model(root, model_id, asset_ids, quiet=quiet)


def prepare_models(
    model_ids: Iterable[str] = DEFAULT_MODEL_IDS,
    *,
    root_dir: str | Path | None = None,
    force: bool = False,
    quiet: bool = False,
    progress_callback: DownloadProgressCallback | None = None,
) -> None:
    project_root = _project_root()
    root = Path(root_dir).resolve() if root_dir is not None else _default_model_root(project_root)
    requested_model_ids = tuple(dict.fromkeys(normalize_model_id(str(model_id)) for model_id in model_ids))
    assets = resolve_assets_for_model_ids(requested_model_ids)

    display = DownloadDisplay(root, requested_model_ids, assets, quiet=quiet, progress_callback=progress_callback)
    if not display.handles_events:
        _print_download_panel(root, requested_model_ids, len(assets))
    else:
        _emit_progress(
            progress_callback,
            kind="manga_model_download",
            stage="queued",
            message="Queued MangaCore model asset preparation.",
            root_dir=str(root),
            model_ids=list(requested_model_ids),
            item_index=0,
            item_count=len(assets),
        )

    results: list[DownloadResult] = []
    display_context = display if display.handles_events else nullcontext(display)
    with display_context, requests.Session() as session:
        for index, asset in enumerate(assets):
            if display.handles_events:
                display.start_asset(index, asset)
            else:
                _print(f"[{index + 1}/{len(assets)}] {asset.asset_id}", style="bold")
            results.append(download_asset(asset, root, force=force, session=session, display=display))

    register_downloaded_models(
        root,
        requested_model_ids,
        assets,
        quiet=quiet,
        progress_callback=progress_callback,
    )
    _emit_progress(
        progress_callback,
        kind="manga_model_download",
        stage="summary",
        message="MangaCore model asset preparation finished.",
        root_dir=str(root),
        model_ids=list(requested_model_ids),
        item_index=len(assets),
        item_count=len(assets),
        downloaded=sum(1 for result in results if result.downloaded),
        skipped=sum(1 for result in results if result.skipped),
        extracted=sum(1 for result in results if result.extracted),
        snapshots=sum(1 for result in results if result.snapshot),
    )
    if not quiet:
        _print_download_summary(results, registered_count=len(requested_model_ids))
        _print("[MangaCore] Default comic runtime assets are ready.", style="green")


def main(argv: list[str] | None = None) -> int:
    project_root = _project_root()
    parser = argparse.ArgumentParser(
        description="Prepare MangaCore model assets with requests downloads and Hugging Face snapshots.",
    )
    parser.add_argument(
        "model_ids",
        nargs="*",
        help="Optional model ids to download. Defaults to the MangaCore stack.",
    )
    parser.add_argument(
        "--root-dir",
        default=str(_default_model_root(project_root)),
        help="Override the MangaCore model root directory.",
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="Redownload assets and re-extract archives even when they already exist locally.",
    )
    args = parser.parse_args(argv)

    try:
        prepare_models(
            args.model_ids or DEFAULT_MODEL_IDS,
            root_dir=args.root_dir,
            force=bool(args.force),
        )
    except Exception as exc:
        print(f"[MangaCore] Failed to prepare manga models: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
