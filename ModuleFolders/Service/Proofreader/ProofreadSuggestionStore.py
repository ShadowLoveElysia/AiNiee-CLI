"""
Persistent storage for AI proofread suggestions.
"""

from __future__ import annotations

import json
import os
import shutil
import time
import uuid
from pathlib import Path
from typing import Iterable

from ModuleFolders.Infrastructure.Cache.CacheItem import TranslationStatus
from ModuleFolders.Service.Proofreader.ProofreadSuggestion import (
    ProofreadSuggestion,
    ProofreadSuggestionParseResult,
    ProofreadSuggestionStatus,
    find_cache_item,
    line_hash,
    normalize_suggestion_mode,
    translation_for_item,
)


class ProofreadSuggestionStore:
    VERSION = 2
    FILENAME = "ProofreadSuggestions.json"
    REPORT_FILENAME = "AIProofreadSuggestionReport.json"
    ARCHIVE_DIRNAME = "proofread_archives"

    def __init__(self, root_path: str | Path) -> None:
        self.root_path = Path(root_path)
        self.path = self._resolve_store_path(self.root_path)
        self.report_path = self._resolve_report_path(self.root_path)
        self.archive_dir = self.path.parent / self.ARCHIVE_DIRNAME
        self.storage_path = self.path
        self.is_archive = False
        self.suggestions: list[ProofreadSuggestion] = []
        self.closed_batches: list[dict[str, str]] = []
        self.run: dict = self._default_run()
        self.project: dict = {}
        self.review_state: dict = self._default_review_state()
        self.review_history: list[dict] = []
        self.extra: dict = {}

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
        self.storage_path = self.path
        self.is_archive = False
        source_path = self.path if self.path.exists() else self.report_path
        if not source_path.exists():
            self.reset()
            return

        self._load_path(source_path)

    def load_report(self) -> bool:
        self.storage_path = self.path
        self.is_archive = False
        if not self.report_path.exists():
            self.reset()
            return False

        self._load_path(self.report_path)
        return True

    def load_archive(self, archive_path: str | Path) -> None:
        path = self._validate_archive_path(archive_path)
        self._load_path(path)
        self.storage_path = path
        self.is_archive = True

    def reset(
        self,
        *,
        sequence: int = 1,
        provider: str = "",
        model: str = "",
        suggestion_mode: str = "proofread",
    ) -> None:
        self.suggestions = []
        self.closed_batches = []
        self.run = self._default_run(
            sequence=sequence,
            provider=provider,
            model=model,
            suggestion_mode=normalize_suggestion_mode(suggestion_mode),
        )
        self.project = {}
        self.review_state = self._default_review_state()
        self.review_history = []
        self.extra = {}

    def save(self) -> None:
        target_path = self.storage_path
        target_path.parent.mkdir(parents=True, exist_ok=True)
        data = self._build_payload()
        tmp_path = target_path.with_name(f"{target_path.name}.{os.getpid()}.tmp")
        try:
            with tmp_path.open("w", encoding="utf-8") as writer:
                json.dump(data, writer, ensure_ascii=False, indent=2)
            os.replace(tmp_path, target_path)
        finally:
            if tmp_path.exists():
                try:
                    tmp_path.unlink()
                except OSError:
                    pass
        if not self.is_archive:
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

    def prepare_new_run(
        self,
        mode: str = "archive",
        provider: str = "",
        model: str = "",
        archive_limit: int = 20,
        suggestion_mode: str = "proofread",
    ) -> Path | None:
        normalized_mode = str(mode or "archive").strip().lower()
        if normalized_mode not in {"archive", "overwrite"}:
            raise ValueError(f"unsupported proofread report mode: {mode}")

        self.load()

        sequence = max(self._highest_sequence() + 1, int(self.run.get("sequence", 0) or 0) + 1, 1)
        archive_path = None
        if normalized_mode == "archive" and self.has_report_content():
            archive_path = self.archive_current()

        self.reset(
            sequence=sequence,
            provider=provider,
            model=model,
            suggestion_mode=suggestion_mode,
        )
        self.storage_path = self.path
        self.is_archive = False
        self.save()
        if normalized_mode == "archive":
            self.rotate_archives(archive_limit)
        return archive_path

    def archive_current(self) -> Path:
        if not self.path.exists():
            self.save()
        self.archive_dir.mkdir(parents=True, exist_ok=True)
        timestamp = time.strftime("%Y%m%d_%H%M%S")
        sequence = max(1, int(self.run.get("sequence", 1) or 1))
        archive_path = self.archive_dir / (
            f"ProofreadSuggestions_{timestamp}_run_{sequence:03d}_{uuid.uuid4().hex[:6]}.json"
        )
        tmp_path = archive_path.with_name(f"{archive_path.name}.{os.getpid()}.tmp")
        try:
            shutil.copy2(self.path, tmp_path)
            with tmp_path.open("r", encoding="utf-8") as reader:
                data = json.load(reader)
            if not isinstance(data, dict) or not isinstance(data.get("suggestions", []), list):
                raise ValueError("invalid proofread archive")
            os.replace(tmp_path, archive_path)
        finally:
            if tmp_path.exists():
                tmp_path.unlink(missing_ok=True)
        return archive_path

    def list_archives(self) -> list[Path]:
        if not self.archive_dir.is_dir():
            return []
        return sorted(
            (
                path
                for path in self.archive_dir.glob("ProofreadSuggestions_*.json")
                if path.is_file()
            ),
            key=lambda path: path.stat().st_mtime,
            reverse=True,
        )

    def archive_summaries(self) -> list[dict]:
        summaries = []
        for path in self.list_archives():
            try:
                with path.open("r", encoding="utf-8") as reader:
                    data = json.load(reader)
                suggestions = [
                    ProofreadSuggestion.from_dict(item)
                    for item in data.get("suggestions", [])
                    if isinstance(item, dict)
                ]
                summaries.append(
                    {
                        "file": path.name,
                        "path": str(path),
                        "modified_time": time.strftime(
                            "%Y-%m-%d %H:%M:%S",
                            time.localtime(path.stat().st_mtime),
                        ),
                        "size": path.stat().st_size,
                        "run": dict(data.get("run", {})),
                        "summary": self._summary_for(suggestions, data.get("closed_batches", [])),
                    }
                )
            except (OSError, ValueError, TypeError, json.JSONDecodeError):
                continue
        return summaries

    def rotate_archives(self, limit: int) -> None:
        try:
            normalized_limit = int(limit)
        except (TypeError, ValueError):
            normalized_limit = 20
        if normalized_limit <= 0:
            return
        archives = self.list_archives()
        for path in archives[normalized_limit:]:
            try:
                with path.open("r", encoding="utf-8") as reader:
                    data = json.load(reader)
                statuses = {
                    str(item.get("status", "pending"))
                    for item in data.get("suggestions", [])
                    if isinstance(item, dict)
                }
                if statuses.intersection({"pending", "conflict"}) or data.get("review_history"):
                    continue
                path.unlink()
            except (OSError, ValueError, TypeError, json.JSONDecodeError):
                continue

    def has_report_content(self) -> bool:
        return bool(self.suggestions or self.closed_batches or self.review_history)

    def set_current_suggestion(self, suggestion_id: str, client: str = "tui", save: bool = True) -> None:
        self.review_state["current_suggestion_id"] = suggestion_id
        self.review_state["updated_at"] = self._now()
        self.review_state["updated_by"] = client
        if save:
            self.save()

    def set_review_filter(self, status: str, client: str = "tui", save: bool = True) -> None:
        self.review_state["active_filter"] = status
        self.review_state["updated_at"] = self._now()
        self.review_state["updated_by"] = client
        if save:
            self.save()

    def mark_generation_completed(self) -> None:
        self.run["generation_status"] = "completed"
        self.run["completed_at"] = self._now()
        self.save()

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
        completed = self.complete_manually_edited_lines(project, client="sync", save=False)
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
        if completed or marked:
            self.save()
        return marked

    def complete_manually_edited_lines(
        self,
        project,
        *,
        file_path: str | None = None,
        text_index: int | None = None,
        client: str = "web",
        save: bool = True,
    ) -> int:
        grouped: dict[str, list[ProofreadSuggestion]] = {}
        for suggestion in self.suggestions:
            if file_path is not None and suggestion.file_path != file_path:
                continue
            if text_index is not None and int(suggestion.text_index) != int(text_index):
                continue
            grouped.setdefault(suggestion.item_id, []).append(suggestion)

        completed = 0
        completed_ids: set[str] = set()
        timestamp = self._now()
        for suggestions in grouped.values():
            item_id = suggestions[0].item_id
            has_current_decision = any(
                suggestion.status in {
                    ProofreadSuggestionStatus.REJECTED,
                    ProofreadSuggestionStatus.IGNORED,
                }
                for suggestion in suggestions
            )
            has_historical_decision = any(
                entry.get("item_id") == item_id
                and entry.get("action") in {"rejected", "ignored"}
                and not entry.get("undone", False)
                and not entry.get("superseded", False)
                for entry in self.review_history
            )
            if not has_current_decision and not has_historical_decision:
                continue

            first = suggestions[0]
            cache_item = find_cache_item(project, first.file_path, first.text_index)
            if cache_item is None or cache_item.translation_status != TranslationStatus.USER_PROOFREAD:
                continue

            extra = cache_item.extra if isinstance(cache_item.extra, dict) else {}
            has_manual_marker = bool(extra.get("proofread_manual_edit"))
            if extra.get("proofread_suggestion") and not has_manual_marker:
                continue

            line_changed = any(
                line_hash(
                    cache_item.source_text,
                    translation_for_item(cache_item, suggestion.target_field),
                    suggestion.target_field,
                )
                != suggestion.line_hash
                for suggestion in suggestions
            )
            if not line_changed:
                continue

            for suggestion in suggestions:
                if suggestion.status in {
                    ProofreadSuggestionStatus.ACCEPTED,
                    ProofreadSuggestionStatus.COMPLETED,
                }:
                    continue
                previous_status = str(suggestion.status)
                current_hash = line_hash(
                    cache_item.source_text,
                    translation_for_item(cache_item, suggestion.target_field),
                    suggestion.target_field,
                )
                suggestion.status = ProofreadSuggestionStatus.COMPLETED
                suggestion.status_updated_at = timestamp
                suggestion.status_updated_by = client
                suggestion.last_action = "completed"
                suggestion.undo_available = False
                suggestion.extra["completion_reason"] = "manual_edit"
                self.review_history.append(
                    {
                        "operation_id": f"op_{uuid.uuid4().hex}",
                        "suggestion_id": suggestion.suggestion_id,
                        "item_id": suggestion.item_id,
                        "action": "completed",
                        "previous_status": previous_status,
                        "next_status": str(ProofreadSuggestionStatus.COMPLETED),
                        "target_field": suggestion.target_field,
                        "original_translation": suggestion.original_translation,
                        "applied_translation": "",
                        "line_hash_before": suggestion.line_hash,
                        "line_hash_after": current_hash,
                        "related_statuses": {},
                        "related_operation_id": "",
                        "cache_snapshot": {},
                        "completion_reason": "manual_edit",
                        "timestamp": timestamp,
                        "client": client,
                        "undone": False,
                    }
                )
                completed_ids.add(suggestion.suggestion_id)
                completed += 1

        if not completed:
            return 0

        for entry in self.review_history:
            if (
                entry.get("suggestion_id") in completed_ids
                and entry.get("action") != "completed"
                and not entry.get("undone", False)
            ):
                entry["superseded"] = True
                entry["superseded_at"] = timestamp
                entry["superseded_by"] = client
                entry["superseded_reason"] = "manual_edit"

        if self.review_state.get("current_suggestion_id") in completed_ids:
            self.review_state["current_suggestion_id"] = ""
        self.review_state["updated_at"] = timestamp
        self.review_state["updated_by"] = client
        if save:
            self.save()
        return completed

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
        data = dict(self.extra)
        data.update({
            "version": self.VERSION,
            "updated_at": self._now(),
            "project": self.project,
            "run": self.run,
            "review_state": self.review_state,
            "review_summary": self._build_summary(),
            "review_history": self.review_history,
            "closed_batches": self.closed_batches,
            "suggestions": [suggestion.to_dict() for suggestion in self.suggestions],
        })
        return data

    def _build_report_payload(self) -> dict:
        data = self._build_payload()
        data["kind"] = "ai_proofread_suggestion_report"
        data["summary"] = self._build_summary()
        return data

    def _build_summary(self) -> dict:
        return self._summary_for(self.suggestions, self.closed_batches)

    @staticmethod
    def _summary_for(suggestions: list[ProofreadSuggestion], closed_batches: list) -> dict:
        status_counts: dict[str, int] = {}
        severity_counts: dict[str, int] = {}
        item_ids: set[str] = set()
        for suggestion in suggestions:
            status_counts[str(suggestion.status)] = status_counts.get(str(suggestion.status), 0) + 1
            severity_counts[suggestion.severity] = severity_counts.get(suggestion.severity, 0) + 1
            item_ids.add(suggestion.item_id)
        return {
            "total_suggestions": len(suggestions),
            "unique_lines": len(item_ids),
            "closed_batches": len(closed_batches),
            "status_counts": status_counts,
            "severity_counts": severity_counts,
        }

    def _load_path(self, path: Path) -> None:
        with path.open("r", encoding="utf-8") as reader:
            data = json.load(reader)
        if not isinstance(data, dict):
            raise ValueError("invalid proofread suggestion report")

        known_keys = {
            "version",
            "updated_at",
            "kind",
            "summary",
            "project",
            "run",
            "review_state",
            "review_summary",
            "review_history",
            "closed_batches",
            "suggestions",
        }
        self.extra = {key: value for key, value in data.items() if key not in known_keys}
        self.project = dict(data.get("project", {}) or {})
        self.run = self._default_run()
        self.run.update(dict(data.get("run", {}) or {}))
        self.review_state = self._default_review_state()
        self.review_state.update(dict(data.get("review_state", {}) or {}))
        self.review_history = list(data.get("review_history", []) or [])
        self.closed_batches = list(data.get("closed_batches", []) or [])
        self.suggestions = [
            ProofreadSuggestion.from_dict(item)
            for item in data.get("suggestions", [])
            if isinstance(item, dict)
        ]
        run_id = str(self.run.get("run_id", ""))
        for suggestion in self.suggestions:
            suggestion.run_id = suggestion.run_id or run_id

    def _validate_archive_path(self, archive_path: str | Path) -> Path:
        path = Path(archive_path).resolve()
        archive_dir = self.archive_dir.resolve()
        if archive_dir not in path.parents or not path.is_file():
            raise ValueError("invalid proofread archive path")
        return path

    def _highest_sequence(self) -> int:
        sequences = [int(self.run.get("sequence", 0) or 0)]
        for summary in self.archive_summaries():
            try:
                sequences.append(int(summary.get("run", {}).get("sequence", 0) or 0))
            except (TypeError, ValueError):
                continue
        return max(sequences, default=0)

    @classmethod
    def _default_run(
        cls,
        sequence: int = 1,
        provider: str = "",
        model: str = "",
        suggestion_mode: str = "proofread",
    ) -> dict:
        return {
            "run_id": f"proofread_{time.strftime('%Y%m%d_%H%M%S')}_{uuid.uuid4().hex[:6]}",
            "sequence": max(1, int(sequence or 1)),
            "created_at": cls._now(),
            "completed_at": "",
            "generation_status": "running",
            "provider": provider,
            "model": model,
            "suggestion_mode": normalize_suggestion_mode(suggestion_mode),
            "source_cache_hash": "",
        }

    @classmethod
    def _default_review_state(cls) -> dict:
        return {
            "current_suggestion_id": "",
            "active_filter": "pending",
            "sort_mode": "location",
            "updated_at": cls._now(),
            "updated_by": "",
        }

    @staticmethod
    def _now() -> str:
        return time.strftime("%Y-%m-%dT%H:%M:%S%z")
