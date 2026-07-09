"""
Independent TUI for reviewing AI proofread suggestions.
"""

from __future__ import annotations

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
    apply_suggestion_to_project,
)
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
        self.filter_status = ProofreadSuggestionStatus.PENDING
        self.last_message = ""

    def _tr(self, key: str) -> str:
        if self.i18n:
            return self.i18n.get(key)
        return key

    def run(
        self,
        store: ProofreadSuggestionStore,
        project: CacheProject,
        reload_store: bool = True,
    ) -> SuggestionReviewResult:
        result = SuggestionReviewResult()
        if reload_store:
            store.load()
        store.refresh_conflicts(project)
        suggestions = self._visible_suggestions(store)
        if not suggestions:
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
                    self._handle_action(action, store, project, result)
                    live.update(self._render(store))
        finally:
            listener.stop()
        return result

    def _run_prompt_mode(self, store: ProofreadSuggestionStore, project: CacheProject, result: SuggestionReviewResult) -> None:
        from rich.prompt import Prompt

        while True:
            self.console.print(self._render(store))
            action = Prompt.ask(self._tr("proofread_suggestion_action_prompt"), choices=["a", "r", "i", "n", "p", "q"], default="n")
            mapped = {
                "a": "accept",
                "r": "reject",
                "i": "ignore",
                "n": "next",
                "p": "prev",
                "q": "quit",
            }[action]
            if mapped == "quit":
                break
            self._handle_action(mapped, store, project, result)

    def _handle_action(
        self,
        action: str | None,
        store: ProofreadSuggestionStore,
        project: CacheProject,
        result: SuggestionReviewResult,
    ) -> None:
        suggestions = self._visible_suggestions(store)
        if not suggestions:
            self.last_message = self._tr("proofread_suggestion_msg_no_pending")
            return
        self.index = min(max(self.index, 0), len(suggestions) - 1)
        current = suggestions[self.index]

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
            apply_result = apply_suggestion_to_project(project, current)
            store.update_suggestion(current)
            store.refresh_conflicts(project)
            if apply_result.status == ProofreadSuggestionStatus.ACCEPTED:
                result.accepted += 1
                self.last_message = self._tr("proofread_suggestion_msg_accepted")
            else:
                result.conflicts += 1
                self.last_message = self._tr("proofread_suggestion_msg_conflict")
            self._advance_after_status_change(store)
        elif action == "reject":
            current.status = ProofreadSuggestionStatus.REJECTED
            store.update_suggestion(current)
            result.rejected += 1
            self.last_message = self._tr("proofread_suggestion_msg_rejected")
            self._advance_after_status_change(store)
        elif action == "ignore":
            current.status = ProofreadSuggestionStatus.IGNORED
            store.update_suggestion(current)
            result.ignored += 1
            self.last_message = self._tr("proofread_suggestion_msg_ignored")
            self._advance_after_status_change(store)
        elif action in {"help", "search", "filter", "goto", "undo"}:
            self.last_message = self._tr("proofread_suggestion_msg_reserved")

    def _advance_after_status_change(self, store: ProofreadSuggestionStore) -> None:
        suggestions = self._visible_suggestions(store)
        if not suggestions:
            self.index = 0
        else:
            self.index = min(self.index, len(suggestions) - 1)

    def _visible_suggestions(self, store: ProofreadSuggestionStore) -> list[ProofreadSuggestion]:
        return [
            suggestion
            for suggestion in store.suggestions
            if suggestion.status == self.filter_status
        ]

    def _render(self, store: ProofreadSuggestionStore):
        suggestions = self._visible_suggestions(store)
        if not suggestions:
            return Panel(
                f"[yellow]{self._tr('proofread_suggestion_no_pending')}[/yellow]",
                title=self._tr("proofread_suggestion_title"),
            )

        self.index = min(max(self.index, 0), len(suggestions) - 1)
        suggestion = suggestions[self.index]
        text_limit = 500 if self.expanded else 180
        table = Table(show_header=False, expand=True, box=None)
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

        footer = Text()
        footer.append(f"{self._tr('proofread_suggestion_footer_accept')}  ", style="green")
        footer.append(f"{self._tr('proofread_suggestion_footer_reject')}  ", style="red")
        footer.append(f"{self._tr('proofread_suggestion_footer_ignore')}  ", style="yellow")
        footer.append(self._tr("proofread_suggestion_footer_nav"), style="dim")
        if self.last_message:
            footer.append(f"\n{self.last_message}", style="cyan")

        content = Table.grid(expand=True)
        content.add_row(table)
        content.add_row(footer)
        return Panel(content, title=f"{self._tr('proofread_suggestion_title')} {self._progress_text(store, suggestion)}")

    def _progress_text(self, store: ProofreadSuggestionStore, suggestion: ProofreadSuggestion) -> str:
        total = len(store.suggestions)
        if total <= 0:
            return "0/0"
        try:
            position = store.suggestions.index(suggestion) + 1
        except ValueError:
            position = min(self.index + 1, total)
        return f"{position}/{total}"

    @staticmethod
    def _clip(text: str, limit: int = 180) -> str:
        text = text or ""
        if len(text) <= limit:
            return text
        return text[:limit] + "..."
