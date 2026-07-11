from collections import defaultdict

from rich.console import Console
from rich.panel import Panel
from rich.prompt import Prompt
from rich.table import Table
from rich.text import Text


console = Console()


RULE_DEFINITIONS = (
    ("pre_translation_switch", "pre_translation_data", "feature_pre_translation_switch", "Pre-translation", "list"),
    ("post_translation_switch", "post_translation_data", "feature_post_translation_switch", "Post-translation", "list"),
    ("prompt_dictionary_switch", "prompt_dictionary_data", "menu_dict_settings", "Glossary", "list"),
    ("exclusion_list_switch", "exclusion_list_data", "menu_exclusion_settings", "Non-translation", "list"),
    ("characterization_switch", "characterization_data", "banner_character_profile", "Characters", "list"),
    ("world_building_switch", "world_building_content", "banner_world_building", "World", "text"),
    ("writing_style_switch", "writing_style_content", "feature_writing_style_switch", "Writing Style", "text"),
    ("translation_example_switch", "translation_example_data", "feature_translation_example_switch", "Examples", "list"),
)


def i18n_text(i18n, key, fallback):
    value = i18n.get(key)
    if not value or value == key:
        return fallback
    return value


def data_count(value):
    if isinstance(value, (list, tuple, set, dict)):
        return len(value)
    if isinstance(value, str):
        return 1 if value.strip() else 0
    return 1 if value else 0


def short_text(value, limit=120):
    text = str(value or "").replace("\n", " ").strip()
    if len(text) <= limit:
        return text
    return text[: limit - 3] + "..."


def build_rule_issue_panel(issues, i18n):
    if not issues:
        return None

    issue_table = Table(show_header=True, expand=True)
    issue_table.add_column("#", justify="right", no_wrap=True, style="dim", width=4)
    issue_table.add_column(
        i18n_text(i18n, "label_status", "Status"),
        no_wrap=True,
        width=10,
    )
    issue_table.add_column(
        i18n_text(i18n, "label_details", "Details"),
        overflow="fold",
        ratio=1,
    )
    conflict_label = i18n_text(i18n, "label_conflict", "Conflict")
    for index, issue in enumerate(issues, 1):
        level = str(issue.get("level", ""))
        message = str(issue.get("message", ""))
        style = "red" if level == conflict_label else "yellow"
        issue_table.add_row(
            str(index),
            Text(level, style=style),
            Text(message),
        )
    return Panel(
        issue_table,
        title=i18n_text(i18n, "label_rule_issues", "Rule Issues"),
        border_style="yellow",
    )


def _extract_exclusion_markers(item):
    if not isinstance(item, dict):
        return []
    markers = item.get("markers", [])
    if isinstance(markers, str):
        return [markers.strip()] if markers.strip() else []
    if isinstance(markers, (list, tuple, set)):
        return [str(marker).strip() for marker in markers if str(marker).strip()]
    return []


def _is_legacy_reference_item(item):
    if not isinstance(item, dict):
        return False
    origin = str(item.get("_glossary_origin") or "").strip()
    status = str(item.get("_mapping_status") or "").strip()
    return origin == "legacy_translation" or status == "seed"


def _active_rule_items(value):
    if not isinstance(value, list):
        return []
    return [item for item in value if not _is_legacy_reference_item(item)]


def _effective_data_count(data_key, value):
    if data_key in {
        "prompt_dictionary_data",
        "exclusion_list_data",
        "characterization_data",
        "translation_example_data",
    }:
        return len(_active_rule_items(value))
    return data_count(value)


class RuleInspector:
    def __init__(self, config, i18n):
        self.config = config or {}
        self.i18n = i18n

    def inspect(self):
        master_enabled = bool(self.config.get("prompt_dictionary_switch", False))
        summaries = []
        issues = []

        for switch_key, data_key, label_key, fallback, value_type in RULE_DEFINITIONS:
            saved_enabled = bool(self.config.get(switch_key, False))
            effective_enabled = master_enabled and saved_enabled
            if switch_key == "prompt_dictionary_switch":
                effective_enabled = master_enabled
            value = self.config.get(data_key, [] if value_type == "list" else "")
            summaries.append(
                {
                    "switch_key": switch_key,
                    "data_key": data_key,
                    "label": i18n_text(self.i18n, label_key, fallback),
                    "saved_enabled": saved_enabled,
                    "effective_enabled": effective_enabled,
                    "count": _effective_data_count(data_key, value),
                }
            )

        if not master_enabled:
            issues.append(
                {
                    "level": i18n_text(self.i18n, "label_warning", "Warning"),
                    "message": i18n_text(
                        self.i18n,
                        "issue_rule_master_off",
                        "Glossary master switch is off; no rules will be injected.",
                    ),
                }
            )

        glossary = _active_rule_items(self.config.get("prompt_dictionary_data", []))
        exclusions = _active_rule_items(self.config.get("exclusion_list_data", []))

        by_src = defaultdict(list)
        for index, item in enumerate(glossary, 1):
            if not isinstance(item, dict):
                continue
            src = str(item.get("src", "")).strip()
            dst = str(item.get("dst", "")).strip()
            if not src:
                continue
            by_src[src].append((index, dst))
            if not dst:
                issues.append(
                    {
                        "level": i18n_text(self.i18n, "label_warning", "Warning"),
                        "message": i18n_text(
                            self.i18n,
                            "issue_empty_dst",
                            "Glossary item has empty dst: {}",
                        ).format(src),
                    }
                )

        for src, entries in by_src.items():
            dsts = {dst for _, dst in entries if dst}
            if len(entries) > 1 and len(dsts) > 1:
                issues.append(
                    {
                        "level": i18n_text(self.i18n, "label_conflict", "Conflict"),
                        "message": i18n_text(
                            self.i18n,
                            "issue_duplicate_term",
                            "Duplicate glossary source has multiple translations: {} -> {}",
                        ).format(src, ", ".join(sorted(dsts))),
                    }
                )

        exclusion_markers = set()
        for item in exclusions:
            exclusion_markers.update(_extract_exclusion_markers(item))
        for src in sorted(set(by_src).intersection(exclusion_markers)):
            issues.append(
                {
                    "level": i18n_text(self.i18n, "label_conflict", "Conflict"),
                    "message": i18n_text(
                        self.i18n,
                        "issue_glossary_exclusion_conflict",
                        "Term appears in both glossary and non-translation list: {}",
                    ).format(src),
                }
            )

        for item in _active_rule_items(self.config.get("characterization_data", [])):
            if not isinstance(item, dict) or not any(str(value).strip() for value in item.values()):
                continue
            original_name = str(item.get("original_name", "")).strip()
            translated_name = str(item.get("translated_name", "")).strip()
            if not original_name and not translated_name:
                issues.append(
                    {
                        "level": i18n_text(self.i18n, "label_warning", "Warning"),
                        "message": i18n_text(
                            self.i18n,
                            "issue_character_name_missing",
                            "Character entry is missing both original and translated names.",
                        ),
                    }
                )

        world_content = str(self.config.get("world_building_content", "") or "")
        if len(world_content) > 4000:
            issues.append(
                {
                    "level": i18n_text(self.i18n, "label_warning", "Warning"),
                    "message": i18n_text(
                        self.i18n,
                        "issue_world_long",
                        "Worldbuilding content is long and may consume too much context: {} chars",
                    ).format(len(world_content)),
                }
            )

        style_content = str(self.config.get("writing_style_content", "") or "")
        if len(style_content) > 2000:
            issues.append(
                {
                    "level": i18n_text(self.i18n, "label_warning", "Warning"),
                    "message": i18n_text(
                        self.i18n,
                        "issue_style_long",
                        "Writing style content is long and may consume too much context: {} chars",
                    ).format(len(style_content)),
                }
            )

        return {"master_enabled": master_enabled, "summaries": summaries, "issues": issues}


class RulePreviewMenu:
    def __init__(self, host):
        self.host = host

    @property
    def i18n(self):
        return self.host.i18n

    @property
    def config(self):
        return self.host.config

    def show(self):
        self.host.display_banner()
        console.print(Panel(f"[bold]{i18n_text(self.i18n, 'menu_rule_effective_preview', 'Rule Effective Preview')}[/bold]"))

        report = RuleInspector(self.config, self.i18n).inspect()
        profile_name = getattr(self.host, "active_rules_profile_name", None) or self.config.get("active_rules_profile", "")
        master_status = i18n_text(self.i18n, "banner_on", "ON") if report["master_enabled"] else i18n_text(self.i18n, "banner_off", "OFF")
        master_style = "green" if report["master_enabled"] else "red"
        console.print(
            f"[bold]{i18n_text(self.i18n, 'banner_glossary_profile', 'Glossary Master Switch')}:[/bold] "
            f"[{master_style}]{master_status}[/{master_style}]"
        )
        if report["master_enabled"]:
            console.print(
                f"[bold]{i18n_text(self.i18n, 'banner_selected_glossary', 'Selected Glossary')}:[/bold] "
                f"[green]{profile_name or i18n_text(self.i18n, 'label_not_selected', 'Not selected')}[/green]"
            )

        table = Table(show_header=True)
        table.add_column(i18n_text(self.i18n, "label_rule", "Rule"))
        table.add_column(i18n_text(self.i18n, "label_saved", "Saved"))
        table.add_column(i18n_text(self.i18n, "label_effective", "Effective"))
        table.add_column(i18n_text(self.i18n, "label_count", "Count"), justify="right")
        for item in report["summaries"]:
            saved = i18n_text(self.i18n, "banner_on", "ON") if item["saved_enabled"] else i18n_text(self.i18n, "banner_off", "OFF")
            effective = i18n_text(self.i18n, "banner_on", "ON") if item["effective_enabled"] else i18n_text(self.i18n, "banner_off", "OFF")
            saved_style = "green" if item["saved_enabled"] else "red"
            effective_style = "green" if item["effective_enabled"] else "red"
            table.add_row(
                item["label"],
                f"[{saved_style}]{saved}[/{saved_style}]",
                f"[{effective_style}]{effective}[/{effective_style}]",
                str(item["count"]),
            )
        console.print(table)

        self._show_issues(report["issues"])
        self._show_samples()
        Prompt.ask(f"\n{i18n_text(self.i18n, 'msg_press_enter', 'Press Enter to continue')}")

    def _show_issues(self, issues):
        if not issues:
            console.print(f"[green]{i18n_text(self.i18n, 'msg_rule_no_issues', 'No obvious rule conflicts found.')}[/green]")
            return
        console.print(build_rule_issue_panel(issues, self.i18n))

    def _show_samples(self):
        sample_table = Table(show_header=True)
        sample_table.add_column(i18n_text(self.i18n, "label_rule", "Rule"))
        sample_table.add_column(i18n_text(self.i18n, "label_preview", "Preview"))
        has_rows = False

        glossary = _active_rule_items(self.config.get("prompt_dictionary_data", []))
        if glossary:
            preview = []
            for item in glossary[:5]:
                if isinstance(item, dict):
                    preview.append(f"{item.get('src', '')} -> {item.get('dst', '')}")
            if preview:
                sample_table.add_row(i18n_text(self.i18n, "menu_dict_settings", "Glossary"), "\n".join(preview))
                has_rows = True

        characters = _active_rule_items(self.config.get("characterization_data", []))
        if characters:
            preview = []
            for item in characters[:3]:
                if isinstance(item, dict):
                    name = item.get("translated_name") or item.get("original_name") or ""
                    if name:
                        preview.append(short_text(name, 40))
            if preview:
                sample_table.add_row(i18n_text(self.i18n, "banner_character_profile", "Characters"), "\n".join(preview))
                has_rows = True

        world = str(self.config.get("world_building_content", "") or "").strip()
        if world:
            sample_table.add_row(i18n_text(self.i18n, "banner_world_building", "World"), short_text(world, 160))
            has_rows = True

        if has_rows:
            console.print(Panel(sample_table, title=i18n_text(self.i18n, "label_preview", "Preview"), border_style="blue"))
