"""
Independent TUI for reviewing AI proofread suggestions.
"""

from __future__ import annotations

import os
import time
from dataclasses import dataclass
from typing import Callable

from rich.console import Console
from rich.live import Live
from rich.panel import Panel
from rich.table import Table
from rich.text import Text

from ModuleFolders.Infrastructure.Cache.CacheProject import CacheProject
from ModuleFolders.Service.Proofreader.ProofreadSuggestion import (
    ProofreadSuggestion,
    ProofreadSuggestionStatus,
)
from ModuleFolders.Service.Proofreader.ProofreadReviewService import ProofreadReviewService
from ModuleFolders.Service.Proofreader.ProofreadSuggestionStore import ProofreadSuggestionStore
from ModuleFolders.UserInterface.InputListener import InputListener


PROOFREAD_SUGGESTION_KEYMAP = {
    "w": "prev",
    "up": "prev",
    "s": "next",
    "down": "next",
    "a": "prev_page",
    "left": "prev_page",
    "d": "next_page",
    "right": "next_page",
    "enter": "accept",
    "backspace": "reject",
    " ": "ignore",
    "tab": "toggle_expand",
    "/": "search",
    "f": "filter",
    "g": "goto",
    "u": "undo",
    "r": "restore",
    "?": "help",
    "q": "quit",
}


def validate_keymap(keymap: dict[str, str]) -> list[str]:
    conflicts: list[str] = []
    seen: dict[str, str] = {}
    for key, action in keymap.items():
        normalized = normalize_key(key)
        if normalized in seen:
            conflicts.append(f"{key}:{action} conflicts with {seen[normalized]}")
        else:
            seen[normalized] = f"{key}:{action}"
    return conflicts


def normalize_key(key: str | None) -> str:
    if key is None:
        return ""
    if key in ("\r", "\n"):
        return "enter"
    if key in ("\x08", "\x7f"):
        return "backspace"
    if key == "\t":
        return "tab"
    return key


@dataclass
class SuggestionReviewResult:
    accepted: int = 0
    rejected: int = 0
    ignored: int = 0
    conflicts: int = 0


class ProofreadSuggestionTUI:
    PAGE_SIZE = 10
    FILTERS = [
        ProofreadSuggestionStatus.PENDING,
        ProofreadSuggestionStatus.IGNORED,
        ProofreadSuggestionStatus.CONFLICT,
        ProofreadSuggestionStatus.ACCEPTED,
        ProofreadSuggestionStatus.REJECTED,
        ProofreadSuggestionStatus.COMPLETED,
        None,
    ]

    def __init__(
        self,
        console: Console | None = None,
        i18n=None,
        input_listener_factory: Callable[[], InputListener] = InputListener,
    ):
        self.console = console or Console()
        self.i18n = i18n
        self.input_listener_factory = input_listener_factory
        self.index = 0
        self.expanded = False
        self.filter_status: ProofreadSuggestionStatus | None = ProofreadSuggestionStatus.PENDING
        self.last_message = ""
        self.review_service: ProofreadReviewService | None = None

    def _tr(self, key: str) -> str:
        if self.i18n:
            return self.i18n.get(key)
        return key

    def run(
        self,
        store: ProofreadSuggestionStore,
        project: CacheProject,
        reload_store: bool = True,
        save_project: Callable[[], None] | None = None,
    ) -> SuggestionReviewResult:
        result = SuggestionReviewResult()
        if reload_store:
            store.load()
        store.refresh_conflicts(project)
        self.review_service = ProofreadReviewService(store, project, save_project=save_project)
        self._restore_review_state(store)
        suggestions = self._visible_suggestions(store)
        if not suggestions:
            if store.suggestions:
                self.filter_status = None
                self.index = 0
                suggestions = self._visible_suggestions(store)
            else:
                self.console.print(f"[yellow]{self._tr('proofread_suggestion_no_pending')}[/yellow]")
                return result

        listener = self.input_listener_factory()
        if listener.disabled:
            self._run_prompt_mode(store, project, result)
            return result

        listener.start()
        try:
            with Live(self._render(store), console=self.console, refresh_per_second=8, screen=True) as live:
                while True:
                    key = normalize_key(listener.get_key())
                    if not key:
                        time.sleep(0.05)
                        continue
                    action = PROOFREAD_SUGGESTION_KEYMAP.get(key)
                    if action == "quit":
                        break
                    if action == "goto":
                        listener.stop()
                        live.stop()
                        self._prompt_goto(store)
                        listener.clear()
                        listener.start()
                        live.start(refresh=True)
                    else:
                        self._handle_action(action, store, project, result)
                    live.update(self._render(store))
        finally:
            listener.stop()
            self._persist_review_state(store)
        return result

    def _run_prompt_mode(self, store: ProofreadSuggestionStore, project: CacheProject, result: SuggestionReviewResult) -> None:
        from rich.prompt import Prompt

        while True:
            self.console.print(self._render(store))
            action = Prompt.ask(
                self._tr("proofread_suggestion_action_prompt"),
                choices=["a", "x", "i", "n", "p", "g", "f", "u", "r", "q"],
                default="n",
            )
            mapped = {
                "a": "accept",
                "x": "reject",
                "i": "ignore",
                "n": "next",
                "p": "prev",
                "g": "goto",
                "f": "filter",
                "u": "undo",
                "r": "restore",
                "q": "quit",
            }[action]
            if mapped == "quit":
                break
            if mapped == "goto":
                self._prompt_goto(store)
            else:
                self._handle_action(mapped, store, project, result)
        self._persist_review_state(store)

    def _handle_action(
        self,
        action: str | None,
        store: ProofreadSuggestionStore,
        project: CacheProject,
        result: SuggestionReviewResult,
    ) -> None:
        service = self.review_service or ProofreadReviewService(store, project)
        if action == "filter":
            self._cycle_filter(store)
            return
        if action == "goto":
            self._prompt_goto(store)
            return
        if action == "undo":
            action_result = service.undo_last(client="tui")
            self.last_message = (
                self._tr("proofread_suggestion_msg_undone")
                if action_result.success
                else self._tr("proofread_suggestion_msg_undo_failed").format(action_result.message)
            )
            self._restore_cursor_for_suggestion(store, action_result.suggestion_id)
            return
        if action in {"help", "search"}:
            self.last_message = self._tr("proofread_suggestion_msg_reserved")
            return

        suggestions = self._visible_suggestions(store)
        if not suggestions:
            self.last_message = self._tr("proofread_suggestion_msg_no_pending")
            return
        self.index = min(max(self.index, 0), len(suggestions) - 1)
        current = suggestions[self.index]

        if current.status == ProofreadSuggestionStatus.COMPLETED and action in {
            "accept",
            "reject",
            "ignore",
            "restore",
        }:
            self.last_message = self._tr("proofread_suggestion_status_completed")
            return

        if action == "prev":
            self.index = max(0, self.index - 1)
        elif action == "next":
            self.index = min(len(suggestions) - 1, self.index + 1)
        elif action == "prev_page":
            self.index = max(0, self.index - self.PAGE_SIZE)
        elif action == "next_page":
            self.index = min(len(suggestions) - 1, self.index + self.PAGE_SIZE)
        elif action == "toggle_expand":
            self.expanded = not self.expanded
        elif action == "accept":
            apply_result = service.accept(current.suggestion_id, client="tui")
            if apply_result.success:
                result.accepted += 1
                self.last_message = self._tr("proofread_suggestion_msg_accepted")
            else:
                result.conflicts += 1
                self.last_message = self._tr("proofread_suggestion_msg_conflict")
            self._advance_after_status_change(store)
        elif action == "reject":
            action_result = service.reject(current.suggestion_id, client="tui")
            if action_result.success:
                result.rejected += 1
                self.last_message = self._tr("proofread_suggestion_msg_rejected")
            self._advance_after_status_change(store)
        elif action == "ignore":
            action_result = service.ignore(current.suggestion_id, client="tui")
            if action_result.success:
                result.ignored += 1
                self.last_message = self._tr("proofread_suggestion_msg_ignored")
            self._advance_after_status_change(store)
        elif action == "restore":
            action_result = service.restore(current.suggestion_id, client="tui")
            self.last_message = (
                self._tr("proofread_suggestion_msg_restored")
                if action_result.success
                else self._tr("proofread_suggestion_msg_restore_failed").format(action_result.message)
            )
            self._advance_after_status_change(store)

        visible = self._visible_suggestions(store)
        if visible:
            self.index = min(self.index, len(visible) - 1)
            store.set_current_suggestion(visible[self.index].suggestion_id, client="tui", save=False)

    def _advance_after_status_change(self, store: ProofreadSuggestionStore) -> None:
        suggestions = self._visible_suggestions(store)
        if not suggestions:
            self.index = 0
        else:
            self.index = min(self.index, len(suggestions) - 1)

    def _visible_suggestions(self, store: ProofreadSuggestionStore) -> list[ProofreadSuggestion]:
        if self.filter_status is None:
            return list(store.suggestions)
        return [
            suggestion
            for suggestion in store.suggestions
            if suggestion.status == self.filter_status
        ]

    def _render(self, store: ProofreadSuggestionStore):
        suggestions = self._visible_suggestions(store)
        if not suggestions:
            content = Table.grid(expand=True)
            content.add_row(self._build_report_table(store))
            content.add_row(Text("─"))
            content.add_row(f"[yellow]{self._tr('proofread_suggestion_no_pending')}[/yellow]")
            content.add_row(self._build_footer(include_item_actions=False))
            template = self._tr("proofread_suggestion_progress_value")
            progress = template.format(0, 0, 0, len(store.suggestions)) if "{" in template else f"0/{len(store.suggestions)}"
            return Panel(content, title=f"{self._tr('proofread_suggestion_title')} {progress}")

        self.index = min(max(self.index, 0), len(suggestions) - 1)
        suggestion = suggestions[self.index]
        text_limit = 500 if self.expanded else 180
        report_table = self._build_report_table(store)
        table = Table(show_header=False, expand=True, box=None)
        table.add_row(
            f"[bold]{self._tr('proofread_suggestion_label_status')}[/bold]",
            self._status_text(suggestion),
        )
        table.add_row(f"[bold]{self._tr('proofread_suggestion_label_source')}[/bold]", self._clip(suggestion.source_text, text_limit))
        table.add_row(f"[bold cyan]{self._tr('proofread_suggestion_label_current_translation')}[/bold cyan]", self._clip(suggestion.current_translation, text_limit))
        table.add_row(f"[bold green]{self._tr('proofread_suggestion_label_suggested_translation')}[/bold green]", self._clip(suggestion.suggested_translation, text_limit))
        table.add_row(f"[bold yellow]{self._tr('proofread_suggestion_label_reason')}[/bold yellow]", self._clip(suggestion.reason, text_limit))
        table.add_row(
            f"[dim]{self._tr('proofread_suggestion_label_location')}[/dim]",
            self._tr("proofread_suggestion_location_value").format(
                suggestion.file_path,
                suggestion.text_index,
                suggestion.line_no,
            ),
        )
        table.add_row(f"[dim]{self._tr('proofread_suggestion_label_severity')}[/dim]", f"{suggestion.severity} / {suggestion.issue_type} / {suggestion.confidence:.2f}")
        if self.expanded:
            table.add_row(
                f"[dim]{self._tr('proofread_suggestion_label_metadata')}[/dim]",
                self._tr("proofread_suggestion_metadata_value").format(
                    suggestion.batch_id,
                    suggestion.line_hash[:8],
                    suggestion.status_updated_by or "-",
                    suggestion.status_updated_at or "-",
                    self._related_count(store, suggestion),
                ),
            )

        content = Table.grid(expand=True)
        content.add_row(report_table)
        content.add_row(Text("─"))
        content.add_row(table)
        content.add_row(
            self._build_footer(
                include_item_actions=suggestion.status != ProofreadSuggestionStatus.COMPLETED
            )
        )
        return Panel(content, title=f"{self._tr('proofread_suggestion_title')} {self._progress_text(store, suggestion)}")

    def _build_report_table(self, store: ProofreadSuggestionStore) -> Table:
        summary = store._build_summary()
        counts = summary.get("status_counts", {})
        table = Table(show_header=False, expand=True, box=None, padding=(0, 1))
        table.add_row(
            f"[bold]{self._tr('proofread_suggestion_report_label')}[/bold]",
            self._tr("proofread_suggestion_report_value").format(
                store.run.get("sequence", 1),
                store.run.get("provider", "") or "-",
                store.run.get("model", "") or "-",
                self._tr(f"proofread_suggestion_generation_{store.run.get('generation_status', 'completed')}")
            ),
        )
        table.add_row(
            f"[bold]{self._tr('proofread_suggestion_summary_label')}[/bold]",
            self._tr("proofread_suggestion_summary_value").format(
                counts.get("pending", 0),
                counts.get("accepted", 0),
                counts.get("rejected", 0),
                counts.get("ignored", 0),
                counts.get("conflict", 0),
                len(store.suggestions),
            ),
        )
        table.add_row(
            f"[bold]{self._tr('proofread_suggestion_filter_completed')}[/bold]",
            str(counts.get("completed", 0)),
        )
        suggestion_mode = str(store.run.get("suggestion_mode", "proofread") or "proofread")
        table.add_row(
            f"[bold]{self._tr('setting_proofread_suggestion_mode')}[/bold]",
            self._tr(f"setting_proofread_suggestion_mode_{suggestion_mode}"),
        )
        table.add_row(
            f"[bold]{self._tr('proofread_suggestion_filter_label')}[/bold]",
            self._tr(self._filter_label_key()),
        )
        return table

    def _build_footer(self, include_item_actions: bool = True) -> Text:
        footer = Text()
        if include_item_actions:
            footer.append(f"{self._tr('proofread_suggestion_footer_accept')}  ", style="green")
            footer.append(f"{self._tr('proofread_suggestion_footer_reject')}  ", style="red")
            footer.append(f"{self._tr('proofread_suggestion_footer_ignore')}  ", style="yellow")
        footer.append(self._tr("proofread_suggestion_footer_nav"), style="dim")
        if self.last_message:
            footer.append(f"\n{self.last_message}", style="cyan")
        return footer

    def _progress_text(self, store: ProofreadSuggestionStore, suggestion: ProofreadSuggestion) -> str:
        total = len(store.suggestions)
        if total <= 0:
            return "0/0"
        try:
            position = store.suggestions.index(suggestion) + 1
        except ValueError:
            position = min(self.index + 1, total)
        visible = self._visible_suggestions(store)
        try:
            filtered_position = visible.index(suggestion) + 1
        except ValueError:
            filtered_position = 0
        template = self._tr("proofread_suggestion_progress_value")
        if "{" not in template:
            return f"{position}/{total}"
        return template.format(filtered_position, len(visible), position, total)

    def _restore_review_state(self, store: ProofreadSuggestionStore) -> None:
        filter_value = str(store.review_state.get("active_filter", "pending"))
        if filter_value == "all":
            self.filter_status = None
        else:
            try:
                self.filter_status = ProofreadSuggestionStatus(filter_value)
            except ValueError:
                self.filter_status = ProofreadSuggestionStatus.PENDING
        current_id = str(store.review_state.get("current_suggestion_id", ""))
        self._restore_cursor_for_suggestion(store, current_id)

    def _restore_cursor_for_suggestion(self, store: ProofreadSuggestionStore, suggestion_id: str) -> None:
        if not suggestion_id:
            return
        visible = self._visible_suggestions(store)
        for index, suggestion in enumerate(visible):
            if suggestion.suggestion_id == suggestion_id:
                self.index = index
                return

    def _persist_review_state(self, store: ProofreadSuggestionStore) -> None:
        visible = self._visible_suggestions(store)
        if visible:
            self.index = min(max(self.index, 0), len(visible) - 1)
            store.review_state["current_suggestion_id"] = visible[self.index].suggestion_id
        store.review_state["active_filter"] = (
            "all" if self.filter_status is None else str(self.filter_status)
        )
        store.review_state["updated_at"] = time.strftime("%Y-%m-%dT%H:%M:%S%z")
        store.review_state["updated_by"] = "tui"
        store.save()

    def _cycle_filter(self, store: ProofreadSuggestionStore) -> None:
        try:
            current_index = self.FILTERS.index(self.filter_status)
        except ValueError:
            current_index = 0
        self.filter_status = self.FILTERS[(current_index + 1) % len(self.FILTERS)]
        self.index = 0
        store.set_review_filter(
            "all" if self.filter_status is None else str(self.filter_status),
            client="tui",
            save=False,
        )
        self.last_message = self._tr("proofread_suggestion_msg_filter_changed").format(
            self._tr(self._filter_label_key())
        )

    def _prompt_goto(self, store: ProofreadSuggestionStore) -> None:
        from rich.prompt import Prompt

        target = Prompt.ask(
            self._tr("proofread_suggestion_msg_goto_input"),
            default="",
            show_default=False,
        ).strip()
        if not target:
            self.last_message = self._tr("proofread_suggestion_msg_goto_hint")
            return
        if self._goto_target(store, target):
            suggestion = self._visible_suggestions(store)[self.index]
            self.last_message = self._tr("proofread_suggestion_msg_goto_found").format(
                suggestion.file_path,
                suggestion.text_index,
            )
        else:
            self.last_message = self._tr("proofread_suggestion_msg_goto_not_found").format(target)

    def _goto_target(self, store: ProofreadSuggestionStore, target: str) -> bool:
        normalized = str(target or "").strip()
        if not normalized:
            return False

        selected: ProofreadSuggestion | None = None
        if normalized.isdigit():
            position = int(normalized)
            if 1 <= position <= len(store.suggestions):
                selected = store.suggestions[position - 1]
        else:
            file_hint = ""
            index_text = normalized
            if "#" in normalized:
                file_hint, _, index_text = normalized.rpartition("#")
            try:
                text_index = int(index_text.strip())
            except ValueError:
                return False

            matches = [
                suggestion
                for suggestion in store.suggestions
                if int(suggestion.text_index) == text_index
                and (
                    not file_hint.strip()
                    or suggestion.file_path == file_hint.strip()
                    or os.path.basename(suggestion.file_path) == os.path.basename(file_hint.strip())
                )
            ]
            if matches:
                status_priority = {
                    ProofreadSuggestionStatus.CONFLICT: 0,
                    ProofreadSuggestionStatus.PENDING: 1,
                    ProofreadSuggestionStatus.IGNORED: 2,
                    ProofreadSuggestionStatus.ACCEPTED: 3,
                    ProofreadSuggestionStatus.REJECTED: 4,
                    ProofreadSuggestionStatus.COMPLETED: 5,
                    ProofreadSuggestionStatus.STALE: 6,
                }
                selected = min(matches, key=lambda item: status_priority.get(item.status, 99))

        if selected is None:
            return False

        visible = self._visible_suggestions(store)
        if selected not in visible:
            self.filter_status = None
            store.set_review_filter("all", client="tui", save=False)
            visible = self._visible_suggestions(store)
        self.index = visible.index(selected)
        store.set_current_suggestion(selected.suggestion_id, client="tui", save=False)
        return True

    def _filter_label_key(self) -> str:
        suffix = "all" if self.filter_status is None else str(self.filter_status)
        return f"proofread_suggestion_filter_{suffix}"

    def _status_text(self, suggestion: ProofreadSuggestion) -> str:
        markers = {
            ProofreadSuggestionStatus.PENDING: ("#", "yellow"),
            ProofreadSuggestionStatus.ACCEPTED: ("*", "green"),
            ProofreadSuggestionStatus.REJECTED: ("-", "dim"),
            ProofreadSuggestionStatus.IGNORED: ("~", "cyan"),
            ProofreadSuggestionStatus.CONFLICT: ("!", "red"),
            ProofreadSuggestionStatus.COMPLETED: ("✓", "green"),
            ProofreadSuggestionStatus.STALE: ("x", "dim"),
        }
        marker, color = markers.get(suggestion.status, ("?", "white"))
        return f"[{color}]{marker} {self._tr(f'proofread_suggestion_status_{suggestion.status}')}[/{color}]"

    @staticmethod
    def _related_count(store: ProofreadSuggestionStore, suggestion: ProofreadSuggestion) -> int:
        return sum(1 for item in store.suggestions if item.item_id == suggestion.item_id)

    @staticmethod
    def _clip(text: str, limit: int = 180) -> str:
        text = text or ""
        if len(text) <= limit:
            return text
        return text[:limit] + "..."
