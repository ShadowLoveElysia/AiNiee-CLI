"""
Shared review actions for TUI, CLI, and Web proofread suggestions.
"""

from __future__ import annotations

import copy
import time
import uuid
from dataclasses import dataclass
from typing import Callable

from ModuleFolders.Infrastructure.Cache.CacheItem import TranslationStatus
from ModuleFolders.Infrastructure.Cache.CacheProject import CacheProject
from ModuleFolders.Service.Proofreader.ProofreadSuggestion import (
    ProofreadSuggestion,
    ProofreadSuggestionStatus,
    apply_suggestion_to_project,
    find_cache_item,
    line_hash,
    translation_for_item,
)
from ModuleFolders.Service.Proofreader.ProofreadSuggestionStore import ProofreadSuggestionStore


@dataclass
class ProofreadReviewActionResult:
    success: bool
    suggestion_id: str = ""
    status: ProofreadSuggestionStatus | None = None
    message: str = ""


class ProofreadReviewService:
    def __init__(
        self,
        store: ProofreadSuggestionStore,
        project: CacheProject,
        save_project: Callable[[], None] | None = None,
    ) -> None:
        self.store = store
        self.project = project
        self.save_project = save_project or (lambda: None)

    def accept(
        self,
        suggestion_id: str,
        client: str = "tui",
        allow_manual_edit_override: bool = False,
    ) -> ProofreadReviewActionResult:
        suggestion = self._require_suggestion(suggestion_id)
        if suggestion.status != ProofreadSuggestionStatus.PENDING:
            return ProofreadReviewActionResult(
                False, suggestion_id, suggestion.status, "suggestion is not pending"
            )
        if self.requires_manual_edit_confirmation(suggestion_id) and not allow_manual_edit_override:
            return ProofreadReviewActionResult(
                False,
                suggestion_id,
                suggestion.status,
                "manual edit confirmation required",
            )
        previous_suggestion = copy.deepcopy(suggestion)
        cache_item = find_cache_item(self.project, suggestion.file_path, suggestion.text_index)
        if cache_item is None:
            return self._mark_conflict(suggestion, client, "cache item not found")
        previous_item = copy.deepcopy(cache_item)
        previous_history = copy.deepcopy(self.store.review_history)
        related_statuses = self._related_statuses(suggestion)

        apply_result = apply_suggestion_to_project(self.project, suggestion)
        if apply_result.status != ProofreadSuggestionStatus.ACCEPTED:
            self._stamp(suggestion, "conflict", client)
            self.store.save()
            return ProofreadReviewActionResult(False, suggestion_id, suggestion.status, apply_result.message)

        now = self._now()
        suggestion.run_id = suggestion.run_id or self.store.run.get("run_id", "")
        suggestion.status_updated_at = now
        suggestion.status_updated_by = client
        suggestion.last_action = "accepted"
        suggestion.accepted_line_hash = line_hash(
            cache_item.source_text,
            translation_for_item(cache_item, suggestion.target_field),
            suggestion.target_field,
        )
        suggestion.undo_available = True
        self._mark_alternative_suggestions_stale(suggestion, client, now)
        history_entry = self._history_entry(
            suggestion,
            action="accepted",
            previous_status=str(previous_suggestion.status),
            next_status=str(suggestion.status),
            client=client,
            related_statuses=related_statuses,
            cache_snapshot=self._cache_snapshot(previous_item),
        )
        self.store.review_history.append(history_entry)
        self.store.set_current_suggestion(suggestion_id, client=client, save=False)

        try:
            self.save_project()
            self.store.save()
        except Exception:
            self._restore_cache_item(cache_item, previous_item)
            self._restore_suggestion(suggestion, previous_suggestion)
            self._restore_related_statuses(related_statuses)
            self.store.review_history = previous_history
            try:
                self.save_project()
            except Exception:
                pass
            raise

        return ProofreadReviewActionResult(True, suggestion_id, suggestion.status)

    def reject(self, suggestion_id: str, client: str = "tui") -> ProofreadReviewActionResult:
        suggestion = self._require_suggestion(suggestion_id)
        if suggestion.status in {
            ProofreadSuggestionStatus.DISCARDED,
            ProofreadSuggestionStatus.ACCEPTED,
            ProofreadSuggestionStatus.STALE,
            ProofreadSuggestionStatus.COMPLETED,
        }:
            return ProofreadReviewActionResult(
                False, suggestion_id, suggestion.status, "suggestion is not reviewable"
            )
        return self._change_status(suggestion_id, ProofreadSuggestionStatus.REJECTED, "rejected", client)

    def ignore(self, suggestion_id: str, client: str = "tui") -> ProofreadReviewActionResult:
        suggestion = self._require_suggestion(suggestion_id)
        if suggestion.status in {
            ProofreadSuggestionStatus.DISCARDED,
            ProofreadSuggestionStatus.ACCEPTED,
            ProofreadSuggestionStatus.STALE,
            ProofreadSuggestionStatus.COMPLETED,
        }:
            return ProofreadReviewActionResult(
                False, suggestion_id, suggestion.status, "suggestion is not reviewable"
            )
        return self._change_status(suggestion_id, ProofreadSuggestionStatus.IGNORED, "ignored", client)

    def restore(self, suggestion_id: str, client: str = "tui") -> ProofreadReviewActionResult:
        suggestion = self._require_suggestion(suggestion_id)
        if suggestion.status == ProofreadSuggestionStatus.ACCEPTED:
            return ProofreadReviewActionResult(False, suggestion_id, suggestion.status, "accepted suggestion must be undone")
        if suggestion.status == ProofreadSuggestionStatus.STALE:
            return ProofreadReviewActionResult(False, suggestion_id, suggestion.status, "stale suggestion cannot be restored")
        if suggestion.status == ProofreadSuggestionStatus.COMPLETED:
            return ProofreadReviewActionResult(False, suggestion_id, suggestion.status, "completed suggestion cannot be restored")
        conflict = self._conflict_if_original_line_changed(suggestion, client)
        if conflict is not None:
            return conflict
        return self._change_status(suggestion_id, ProofreadSuggestionStatus.PENDING, "restored", client)

    def requires_manual_edit_confirmation(self, suggestion_id: str) -> bool:
        suggestion = self._require_suggestion(suggestion_id)
        return (
            suggestion.status == ProofreadSuggestionStatus.PENDING
            and bool(suggestion.extra.get("was_discarded"))
        )

    def delete(self, suggestion_id: str, client: str = "tui") -> ProofreadReviewActionResult:
        suggestion = self._require_suggestion(suggestion_id)
        if self.store.is_archive:
            return ProofreadReviewActionResult(
                False, suggestion_id, suggestion.status, "archived report is read-only"
            )
        if suggestion.status == ProofreadSuggestionStatus.ACCEPTED:
            return ProofreadReviewActionResult(
                False,
                suggestion_id,
                suggestion.status,
                "accepted suggestion must be undone",
            )

        previous_suggestions = copy.deepcopy(self.store.suggestions)
        previous_history = copy.deepcopy(self.store.review_history)
        previous_review_state = copy.deepcopy(self.store.review_state)
        self.store.suggestions = [
            item for item in self.store.suggestions if item.suggestion_id != suggestion_id
        ]
        self.store.review_history = [
            entry
            for entry in self.store.review_history
            if entry.get("suggestion_id") != suggestion_id
        ]
        for entry in self.store.review_history:
            related_statuses = entry.get("related_statuses")
            if isinstance(related_statuses, dict):
                related_statuses.pop(suggestion_id, None)
        if self.store.review_state.get("current_suggestion_id") == suggestion_id:
            self.store.review_state["current_suggestion_id"] = ""
        self.store.review_state["updated_at"] = self._now()
        self.store.review_state["updated_by"] = client
        try:
            self.store.save()
        except Exception:
            self.store.suggestions = previous_suggestions
            self.store.review_history = previous_history
            self.store.review_state = previous_review_state
            raise
        return ProofreadReviewActionResult(True, suggestion_id, suggestion.status)

    def undo_last(self, client: str = "tui") -> ProofreadReviewActionResult:
        entry = next(
            (
                item
                for item in reversed(self.store.review_history)
                if item.get("action") in {"accepted", "rejected", "ignored", "restored"}
                and not item.get("undone", False)
                and not item.get("superseded", False)
            ),
            None,
        )
        if entry is None:
            return ProofreadReviewActionResult(False, message="nothing to undo")

        suggestion = self._find_suggestion(str(entry.get("suggestion_id", "")))
        if suggestion is None:
            return ProofreadReviewActionResult(False, message="suggestion not found")
        if entry.get("action") != "accepted":
            previous_status = ProofreadSuggestionStatus(entry.get("previous_status", "pending"))
            if previous_status == ProofreadSuggestionStatus.PENDING:
                conflict = self._conflict_if_original_line_changed(suggestion, client)
                if conflict is not None:
                    return conflict
            suggestion.status = previous_status
            self._stamp(suggestion, "undone", client)
            entry["undone"] = True
            entry["undone_at"] = self._now()
            entry["undone_by"] = client
            self.store.review_history.append(
                self._history_entry(
                    suggestion,
                    action="undone",
                    previous_status=str(entry.get("next_status", suggestion.status)),
                    next_status=str(previous_status),
                    client=client,
                    related_operation_id=str(entry.get("operation_id", "")),
                )
            )
            self.store.save()
            return ProofreadReviewActionResult(True, suggestion.suggestion_id, suggestion.status)

        return self._undo_accepted(entry, suggestion, client)

    def undo_suggestion(self, suggestion_id: str, client: str = "tui") -> ProofreadReviewActionResult:
        entry = next(
            (
                item
                for item in reversed(self.store.review_history)
                if item.get("suggestion_id") == suggestion_id
                and item.get("action") == "accepted"
                and not item.get("undone", False)
            ),
            None,
        )
        if entry is None:
            return ProofreadReviewActionResult(False, suggestion_id, message="accepted operation not found")
        suggestion = self._require_suggestion(suggestion_id)
        return self._undo_accepted(entry, suggestion, client)

    def _undo_accepted(
        self,
        entry: dict,
        suggestion: ProofreadSuggestion,
        client: str,
    ) -> ProofreadReviewActionResult:
        cache_item = find_cache_item(self.project, suggestion.file_path, suggestion.text_index)
        if cache_item is None:
            return self._mark_conflict(suggestion, client, "cache item not found")

        current_translation = translation_for_item(cache_item, suggestion.target_field)
        current_hash = line_hash(cache_item.source_text, current_translation, suggestion.target_field)
        if current_translation != suggestion.applied_translation or current_hash != suggestion.accepted_line_hash:
            return self._mark_conflict(suggestion, client, "accepted translation changed")

        previous_item = copy.deepcopy(cache_item)
        previous_suggestion = copy.deepcopy(suggestion)
        previous_history = copy.deepcopy(self.store.review_history)
        cache_snapshot = entry.get("cache_snapshot")
        if isinstance(cache_snapshot, dict) and cache_snapshot:
            self._restore_cache_snapshot(cache_item, cache_snapshot)
        elif suggestion.target_field == "polished_text":
            cache_item.polished_text = suggestion.original_translation
        else:
            cache_item.translated_text = suggestion.original_translation
            cache_item.translation_status = TranslationStatus.TRANSLATED
        previous_status = ProofreadSuggestionStatus(entry.get("previous_status", "pending"))
        suggestion.status = previous_status
        suggestion.applied_translation = ""
        suggestion.accepted_line_hash = ""
        suggestion.undo_available = False
        self._stamp(suggestion, "undone", client)
        self._restore_related_statuses(entry.get("related_statuses", {}))
        entry["undone"] = True
        entry["undone_at"] = self._now()
        entry["undone_by"] = client
        self.store.review_history.append(
            self._history_entry(
                suggestion,
                action="undone",
                previous_status="accepted",
                next_status=str(previous_status),
                client=client,
                related_operation_id=str(entry.get("operation_id", "")),
            )
        )
        try:
            self.save_project()
            self.store.save()
        except Exception:
            self._restore_cache_item(cache_item, previous_item)
            self._restore_suggestion(suggestion, previous_suggestion)
            self.store.review_history = previous_history
            try:
                self.save_project()
            except Exception:
                pass
            raise
        return ProofreadReviewActionResult(True, suggestion.suggestion_id, suggestion.status)

    def _change_status(
        self,
        suggestion_id: str,
        status: ProofreadSuggestionStatus,
        action: str,
        client: str,
    ) -> ProofreadReviewActionResult:
        suggestion = self._require_suggestion(suggestion_id)
        previous_status = suggestion.status
        suggestion.status = status
        suggestion.undo_available = False
        self._stamp(suggestion, action, client)
        self.store.review_history.append(
            self._history_entry(
                suggestion,
                action=action,
                previous_status=str(previous_status),
                next_status=str(status),
                client=client,
            )
        )
        self.store.set_current_suggestion(suggestion_id, client=client, save=False)
        self.store.save()
        return ProofreadReviewActionResult(True, suggestion_id, status)

    def _mark_conflict(
        self,
        suggestion: ProofreadSuggestion,
        client: str,
        message: str,
    ) -> ProofreadReviewActionResult:
        suggestion.status = ProofreadSuggestionStatus.CONFLICT
        suggestion.undo_available = False
        self._stamp(suggestion, "conflict", client)
        self.store.save()
        return ProofreadReviewActionResult(False, suggestion.suggestion_id, suggestion.status, message)

    def _conflict_if_original_line_changed(
        self,
        suggestion: ProofreadSuggestion,
        client: str,
    ) -> ProofreadReviewActionResult | None:
        cache_item = find_cache_item(self.project, suggestion.file_path, suggestion.text_index)
        if cache_item is None:
            return self._mark_conflict(suggestion, client, "cache item not found")
        current_translation = translation_for_item(cache_item, suggestion.target_field)
        current_hash = line_hash(cache_item.source_text, current_translation, suggestion.target_field)
        if current_hash != suggestion.line_hash:
            return self._mark_conflict(suggestion, client, "original translation changed")
        return None

    def _find_suggestion(self, suggestion_id: str) -> ProofreadSuggestion | None:
        return next((item for item in self.store.suggestions if item.suggestion_id == suggestion_id), None)

    def _require_suggestion(self, suggestion_id: str) -> ProofreadSuggestion:
        suggestion = self._find_suggestion(suggestion_id)
        if suggestion is None:
            raise KeyError(f"proofread suggestion not found: {suggestion_id}")
        return suggestion

    def _related_statuses(self, suggestion: ProofreadSuggestion) -> dict[str, str]:
        return {
            item.suggestion_id: str(item.status)
            for item in self.store.suggestions
            if item.item_id == suggestion.item_id and item.suggestion_id != suggestion.suggestion_id
        }

    def _mark_alternative_suggestions_stale(
        self,
        accepted: ProofreadSuggestion,
        client: str,
        timestamp: str,
    ) -> None:
        for suggestion in self.store.suggestions:
            if suggestion.item_id != accepted.item_id or suggestion.suggestion_id == accepted.suggestion_id:
                continue
            if suggestion.status not in {ProofreadSuggestionStatus.PENDING, ProofreadSuggestionStatus.CONFLICT}:
                continue
            suggestion.status = ProofreadSuggestionStatus.STALE
            suggestion.status_updated_at = timestamp
            suggestion.status_updated_by = client
            suggestion.last_action = "superseded"

    def _restore_related_statuses(self, statuses: dict[str, str]) -> None:
        for suggestion_id, status in statuses.items():
            suggestion = self._find_suggestion(suggestion_id)
            if suggestion is not None:
                suggestion.status = ProofreadSuggestionStatus(status)

    def _history_entry(
        self,
        suggestion: ProofreadSuggestion,
        action: str,
        previous_status: str,
        next_status: str,
        client: str,
        related_statuses: dict[str, str] | None = None,
        related_operation_id: str = "",
        cache_snapshot: dict | None = None,
    ) -> dict:
        return {
            "operation_id": f"op_{uuid.uuid4().hex}",
            "suggestion_id": suggestion.suggestion_id,
            "item_id": suggestion.item_id,
            "action": action,
            "previous_status": previous_status,
            "next_status": next_status,
            "target_field": suggestion.target_field,
            "original_translation": suggestion.original_translation,
            "applied_translation": suggestion.applied_translation,
            "line_hash_before": suggestion.line_hash,
            "line_hash_after": suggestion.accepted_line_hash,
            "related_statuses": related_statuses or {},
            "related_operation_id": related_operation_id,
            "cache_snapshot": cache_snapshot or {},
            "timestamp": self._now(),
            "client": client,
            "undone": False,
        }

    @staticmethod
    def _stamp(suggestion: ProofreadSuggestion, action: str, client: str) -> None:
        suggestion.status_updated_at = ProofreadReviewService._now()
        suggestion.status_updated_by = client
        suggestion.last_action = action

    @staticmethod
    def _restore_cache_item(target, source) -> None:
        for key in ("translated_text", "polished_text", "translation_status", "extra"):
            setattr(target, key, copy.deepcopy(getattr(source, key)))

    @staticmethod
    def _cache_snapshot(item) -> dict:
        return {
            "translated_text": item.translated_text,
            "polished_text": item.polished_text,
            "translation_status": item.translation_status,
            "extra": copy.deepcopy(item.extra),
        }

    @staticmethod
    def _restore_cache_snapshot(item, snapshot: dict) -> None:
        for key in ("translated_text", "polished_text", "translation_status", "extra"):
            if key in snapshot:
                setattr(item, key, copy.deepcopy(snapshot[key]))

    @staticmethod
    def _restore_suggestion(target: ProofreadSuggestion, source: ProofreadSuggestion) -> None:
        for key, value in source.__dict__.items():
            setattr(target, key, copy.deepcopy(value))

    @staticmethod
    def _now() -> str:
        return time.strftime("%Y-%m-%dT%H:%M:%S%z")
