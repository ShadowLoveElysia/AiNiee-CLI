"""Persistent raw LLM response log for AI proofread suggestion runs."""

from __future__ import annotations

import json
import os
import threading
import time
from pathlib import Path
from typing import Any


class ProofreadRawResponseStore:
    VERSION = 1
    FILENAME = "AIProofreadLLMResponses.json"

    def __init__(self, root_path: str | Path) -> None:
        root = Path(root_path)
        output_dir = root.parent if root.name == "cache" else root
        self.path = output_dir / "proofread" / self.FILENAME
        self._lock = threading.Lock()

    def append(self, entry: dict[str, Any]) -> None:
        normalized = dict(entry)
        normalized.setdefault("timestamp", time.strftime("%Y-%m-%dT%H:%M:%S%z"))
        with self._lock:
            payload = self._load_payload()
            payload["entries"].append(normalized)
            payload["updated_at"] = normalized["timestamp"]
            self._save_payload(payload)

    def _load_payload(self) -> dict[str, Any]:
        if self.path.is_file():
            try:
                with self.path.open("r", encoding="utf-8") as reader:
                    payload = json.load(reader)
                if isinstance(payload, dict) and isinstance(payload.get("entries"), list):
                    return payload
            except (OSError, ValueError, TypeError, json.JSONDecodeError):
                pass
        return {
            "version": self.VERSION,
            "kind": "ai_proofread_llm_response_log",
            "updated_at": "",
            "entries": [],
        }

    def _save_payload(self, payload: dict[str, Any]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp_path = self.path.with_name(f"{self.path.name}.{os.getpid()}.tmp")
        try:
            with tmp_path.open("w", encoding="utf-8") as writer:
                json.dump(payload, writer, ensure_ascii=False, indent=2)
            os.replace(tmp_path, self.path)
        finally:
            if tmp_path.exists():
                tmp_path.unlink(missing_ok=True)
