"""
Persistent storage for AI proofread suggestions.
"""

from __future__ import annotations

import json
import os
import time
from pathlib import Path
from typing import Iterable

from ModuleFolders.Service.Proofreader.ProofreadSuggestion import (
    ProofreadSuggestion,
    ProofreadSuggestionParseResult,
    ProofreadSuggestionStatus,
    find_cache_item,
    line_hash,
    translation_for_item,
)


class ProofreadSuggestionStore:
    VERSION = 1
    FILENAME = "ProofreadSuggestions.json"
    REPORT_FILENAME = "AIProofreadSuggestionReport.json"

    def __init__(self, root_path: str | Path) -> None:
        self.root_path = Path(root_path)
        self.path = self._resolve_store_path(self.root_path)
        self.report_path = self._resolve_report_path(self.root_path)
        self.suggestions: list[ProofreadSuggestion] = []
        self.closed_batches: list[dict[str, str]] = []

    @classmethod
    def _resolve_store_path(cls, root_path: Path) -> Path:
        if root_path.name == "cache":
            return root_path / cls.FILENAME
        cache_dir = root_path / "cache"
        if cache_dir.exists():
            return cache_dir / cls.FILENAME
        return root_path / cls.FILENAME

    @classmethod
    def _resolve_report_path(cls, root_path: Path) -> Path:
        if root_path.name == "cache":
            output_dir = root_path.parent
        else:
            output_dir = root_path
        return output_dir / "proofread" / cls.REPORT_FILENAME

    def load(self) -> None:
        source_path = self.path if self.path.exists() else self.report_path
        if not source_path.exists():
            self.suggestions = []
            self.closed_batches = []
            return

        with source_path.open("r", encoding="utf-8") as reader:
            data = json.load(reader)

        self.closed_batches = list(data.get("closed_batches", []))
        self.suggestions = [
            ProofreadSuggestion.from_dict(item)
            for item in data.get("suggestions", [])
            if isinstance(item, dict)
        ]

    def load_report(self) -> bool:
        if not self.report_path.exists():
            self.suggestions = []
            self.closed_batches = []
            return False

        with self.report_path.open("r", encoding="utf-8") as reader:
            data = json.load(reader)

        self.closed_batches = list(data.get("closed_batches", []))
        self.suggestions = [
            ProofreadSuggestion.from_dict(item)
            for item in data.get("suggestions", [])
            if isinstance(item, dict)
        ]
        return True

    def save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        data = self._build_payload()
        tmp_path = self.path.with_name(f"{self.path.name}.{os.getpid()}.tmp")
        try:
            with tmp_path.open("w", encoding="utf-8") as writer:
                json.dump(data, writer, ensure_ascii=False, indent=2)
            os.replace(tmp_path, self.path)
        finally:
            if tmp_path.exists():
                try:
                    tmp_path.unlink()
                except OSError:
                    pass
        self.save_report()

    def save_report(self) -> None:
        self.report_path.parent.mkdir(parents=True, exist_ok=True)
        data = self._build_report_payload()
        tmp_path = self.report_path.with_name(f"{self.report_path.name}.{os.getpid()}.tmp")
        try:
            with tmp_path.open("w", encoding="utf-8") as writer:
                json.dump(data, writer, ensure_ascii=False, indent=2)
            os.replace(tmp_path, self.report_path)
        finally:
            if tmp_path.exists():
                try:
                    tmp_path.unlink()
                except OSError:
                    pass

    def add_batch_result(self, result: ProofreadSuggestionParseResult) -> None:
        if not self.path.exists():
            self.load()

        if result.closed_without_suggestions and not result.suggestions:
            self._record_closed_batch(result.batch_id, result.batch_hash)
            self.save()
            return

        current_by_id = {suggestion.suggestion_id: suggestion for suggestion in self.suggestions}
        for suggestion in result.suggestions:
            current_by_id[suggestion.suggestion_id] = suggestion
        self.suggestions = list(current_by_id.values())
        self.save()

    def update_status(self, suggestion_id: str, status: ProofreadSuggestionStatus | str) -> bool:
        normalized_status = ProofreadSuggestionStatus(status)
        for suggestion in self.suggestions:
            if suggestion.suggestion_id == suggestion_id:
                suggestion.status = normalized_status
                self.save()
                return True
        return False

    def update_suggestion(self, updated: ProofreadSuggestion) -> bool:
        for index, suggestion in enumerate(self.suggestions):
            if suggestion.suggestion_id == updated.suggestion_id:
                self.suggestions[index] = updated
                self.save()
                return True
        return False

    def pending(self) -> list[ProofreadSuggestion]:
        return [
            suggestion
            for suggestion in self.suggestions
            if suggestion.status == ProofreadSuggestionStatus.PENDING
        ]

    def refresh_conflicts(self, project) -> int:
        marked = 0
        for suggestion in self.pending():
            cache_item = find_cache_item(project, suggestion.file_path, suggestion.text_index)
            if cache_item is None:
                suggestion.status = ProofreadSuggestionStatus.CONFLICT
                marked += 1
                continue

            current_translation = translation_for_item(cache_item, suggestion.target_field)
            current_hash = line_hash(cache_item.source_text, current_translation, suggestion.target_field)
            if current_hash != suggestion.line_hash:
                suggestion.status = ProofreadSuggestionStatus.CONFLICT
                marked += 1
        if marked:
            self.save()
        return marked

    def mark_many(self, suggestions: Iterable[ProofreadSuggestion], status: ProofreadSuggestionStatus) -> None:
        ids = {suggestion.suggestion_id for suggestion in suggestions}
        for suggestion in self.suggestions:
            if suggestion.suggestion_id in ids:
                suggestion.status = status
        self.save()

    def _record_closed_batch(self, batch_id: str, batch_hash: str) -> None:
        entry = {"batch_id": batch_id, "batch_hash": batch_hash}
        if entry not in self.closed_batches:
            self.closed_batches.append(entry)

    def _build_payload(self) -> dict:
        return {
            "version": self.VERSION,
            "updated_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
            "closed_batches": self.closed_batches,
            "suggestions": [suggestion.to_dict() for suggestion in self.suggestions],
        }

    def _build_report_payload(self) -> dict:
        data = self._build_payload()
        data["kind"] = "ai_proofread_suggestion_report"
        data["summary"] = self._build_summary()
        return data

    def _build_summary(self) -> dict:
        status_counts: dict[str, int] = {}
        severity_counts: dict[str, int] = {}
        for suggestion in self.suggestions:
            status_counts[str(suggestion.status)] = status_counts.get(str(suggestion.status), 0) + 1
            severity_counts[suggestion.severity] = severity_counts.get(suggestion.severity, 0) + 1
        return {
            "total_suggestions": len(self.suggestions),
            "closed_batches": len(self.closed_batches),
            "status_counts": status_counts,
            "severity_counts": severity_counts,
        }
