from dataclasses import dataclass, field
import os
import re
from typing import Iterable

from ModuleFolders.Infrastructure.Cache.CacheItem import CacheItem
from ModuleFolders.Domain.PromptBuilder.PromptBuilder import PromptBuilder
from ModuleFolders.Domain.PromptBuilder.PromptBuilderEnum import PromptBuilderEnum


_NUMBERED_SEPARATOR_CHARS = r"\.\uff0e\u3002:\uff1a\u3001\)\uff09"
_NUMBERED_PREFIX = re.compile(
    rf"^\s*(\d+)[{_NUMBERED_SEPARATOR_CHARS}](?!\d+[{_NUMBERED_SEPARATOR_CHARS}])\s*"
)
_TEXTAREA = re.compile(r"<textarea.*?>(.*?)</textarea>", re.DOTALL | re.IGNORECASE)


@dataclass(frozen=True)
class NumberedResponseAnalysis:
    entries: dict[int, str] = field(default_factory=dict)
    missing_numbers: tuple[int, ...] = ()
    duplicate_numbers: tuple[int, ...] = ()
    unexpected_numbers: tuple[int, ...] = ()
    empty_numbers: tuple[int, ...] = ()

    @property
    def has_unsafe_structure(self) -> bool:
        return bool(self.duplicate_numbers or self.unexpected_numbers)


@dataclass(frozen=True)
class SparseCompletionContext:
    previous_items: list[CacheItem] = field(default_factory=list)
    lookahead_items: list[CacheItem] = field(default_factory=list)


@dataclass(frozen=True)
class SparseCompletionRequest:
    messages: list[dict]
    system_prompt: str
    required_numbers: tuple[int, ...]


def analyze_numbered_response(
    response_content: str,
    expected_numbers: Iterable[int],
) -> NumberedResponseAnalysis:
    expected = {int(number) for number in expected_numbers}
    entries: dict[int, str] = {}
    duplicates = set()
    empty = set()

    textarea_matches = _TEXTAREA.findall(str(response_content or ""))
    content = textarea_matches[-1] if textarea_matches else ""
    for raw_block in _extract_numbered_blocks(content):
        block = raw_block.strip()
        if not block:
            continue
        match = _NUMBERED_PREFIX.match(block)
        if not match:
            continue

        number = int(match.group(1))
        body = block[match.end():]
        normalized = f"{number}.{body}"
        if number in entries:
            duplicates.add(number)
            continue
        entries[number] = normalized
        if not _has_translation_body(body):
            empty.add(number)

    unexpected = set(entries) - expected
    usable = set(entries) - empty
    missing = expected - usable
    return NumberedResponseAnalysis(
        entries=entries,
        missing_numbers=tuple(sorted(missing)),
        duplicate_numbers=tuple(sorted(duplicates)),
        unexpected_numbers=tuple(sorted(unexpected)),
        empty_numbers=tuple(sorted(empty)),
    )


def merge_sparse_completion_response(
    original_response: str,
    supplement_response: str,
    expected_numbers: Iterable[int],
    required_numbers: Iterable[int],
) -> str | None:
    expected = {int(number) for number in expected_numbers}
    required = {int(number) for number in required_numbers}
    if not required or not required.issubset(expected):
        return None

    original = analyze_numbered_response(original_response, expected)
    supplement = analyze_numbered_response(supplement_response, required)
    if original.has_unsafe_structure or supplement.has_unsafe_structure:
        return None
    if set(supplement.entries) != required:
        return None
    if supplement.missing_numbers or supplement.empty_numbers:
        return None

    merged_entries = {
        number: text
        for number, text in original.entries.items()
        if number in expected and number not in required and number not in original.empty_numbers
    }
    merged_entries.update(supplement.entries)
    if set(merged_entries) != expected:
        return None

    body = "\n".join(merged_entries[number] for number in sorted(expected))
    return f"<textarea>\n{body}\n</textarea>"


def build_sparse_completion_prompt(
    config,
    source_text_dict: dict[str, str],
    existing: NumberedResponseAnalysis,
    required_numbers: Iterable[int],
    context: SparseCompletionContext,
) -> str:
    required = tuple(sorted({int(number) for number in required_numbers}))
    required_text = ", ".join(str(number) for number in required)
    labels = _prompt_labels(config)
    previous = _format_context_items(
        context.previous_items,
        include_translation=True,
        source_label=labels["source"],
        translation_label=labels["translation"],
    )
    previous_section = f"{labels['previous_heading']}\n{previous}" if previous else ""

    current_rows = []
    for ordinal, source in enumerate(source_text_dict.values(), start=1):
        translated = existing.entries.get(ordinal)
        translated_body = _strip_numbered_prefix(translated) if translated else labels["missing"]
        current_rows.append(
            f"[{ordinal}]\n{labels['source']} {source}\n{labels['existing_translation']} {translated_body}"
        )

    lookahead = _format_context_items(
        context.lookahead_items,
        include_translation=True,
        source_label=labels["source"],
        translation_label=labels["translation"],
    )
    lookahead_section = f"{labels['lookahead_heading']}\n{lookahead}" if lookahead else ""

    template = _load_sparse_completion_prompt_template(config)
    replacements = {
        "{{previous_context}}": previous_section,
        "{{current_batch}}": "\n\n".join(current_rows),
        "{{lookahead_context}}": lookahead_section,
        "{{required_numbers}}": required_text,
        "{{response_example}}": "\n".join(
            _build_response_example(
                number,
                list(source_text_dict.values())[number - 1],
                labels["translation_placeholder"],
            )
            for number in required
        ),
    }
    for placeholder, value in replacements.items():
        template = template.replace(placeholder, value)
    return template.strip()


def build_sparse_completion_system_prompt(
    config,
    recall_source_text_dict: dict[str, str],
    glossary_source_text_dict: dict[str, str],
    character_recall_previous_text_list: list[str],
    character_recall_lookahead_text_list: list[str],
    source_lang: str = "japanese",
    include_base_prompt: bool = True,
) -> str:
    selected = config.translation_prompt_selection or {}
    selected_id = selected.get("last_selected_id")
    system = ""
    if include_base_prompt:
        if selected_id in (PromptBuilderEnum.COMMON, PromptBuilderEnum.COT, PromptBuilderEnum.THINK):
            system = PromptBuilder.build_system(config, source_lang)
        else:
            custom_prompt = selected.get("prompt_content")
            system = PromptBuilder._replace_language_placeholders(custom_prompt, config, source_lang) if custom_prompt else ""

    if config.prompt_dictionary_switch:
        glossary = PromptBuilder.build_glossary_prompt(config, glossary_source_text_dict)
        if glossary:
            system += glossary

    if config.exclusion_list_switch:
        exclusion = PromptBuilder.build_ntl_prompt(config, glossary_source_text_dict)
        if exclusion:
            system += exclusion

    if config.characterization_switch:
        characterization = PromptBuilder.build_characterization(
            config,
            recall_source_text_dict,
            character_recall_previous_text_list,
            character_recall_lookahead_text_list,
        )
        if characterization:
            system += characterization

    if config.world_building_switch:
        system += PromptBuilder.build_world_building(config)
    if config.writing_style_switch:
        system += PromptBuilder.build_writing_style(config)

    return system


def build_sparse_completion_request(
    config,
    response_content: str,
    source_text_dict: dict[str, str],
    context: SparseCompletionContext,
    base_system_prompt: str,
    character_recall_previous_text_list: list[str],
    character_recall_lookahead_text_list: list[str],
    source_lang: str,
) -> SparseCompletionRequest | None:
    expected_numbers = set(range(1, len(source_text_dict) + 1))
    analysis = analyze_numbered_response(response_content, expected_numbers)
    if analysis.has_unsafe_structure or not analysis.entries:
        return None
    if not analysis.missing_numbers:
        return None
    if len(analysis.missing_numbers) >= len(expected_numbers):
        return None

    context_source_text_dict: dict[str, str] = {}
    for item in context.previous_items:
        context_source_text_dict[str(len(context_source_text_dict))] = item.source_text
    for item in context.lookahead_items:
        context_source_text_dict[str(len(context_source_text_dict))] = item.source_text

    system = base_system_prompt + _build_incremental_context_rules(
        config,
        context_source_text_dict,
        base_system_prompt,
    )
    prompt = build_sparse_completion_prompt(
        config,
        source_text_dict,
        analysis,
        analysis.missing_numbers,
        context,
    )
    return SparseCompletionRequest(
        messages=[{"role": "user", "content": prompt}],
        system_prompt=system,
        required_numbers=analysis.missing_numbers,
    )


def _has_translation_body(body: str) -> bool:
    cleaned = str(body or "").strip()
    if cleaned.startswith("[") and cleaned.endswith("]"):
        cleaned = cleaned[1:-1].strip()
    cleaned = re.sub(r"[\s\"'\u201c\u201d,\uff0c]+", "", cleaned)
    return bool(cleaned)


def _extract_numbered_blocks(content: str) -> list[str]:
    blocks = []
    current = []
    in_multiline_block = False

    for line in str(content or "").strip().splitlines():
        main_match = _NUMBERED_PREFIX.match(line)
        if main_match and not in_multiline_block:
            if current:
                blocks.append("\n".join(current))
            current = [line]
            remainder = line[main_match.end():].lstrip()
            in_multiline_block = remainder.startswith("[") and not remainder.rstrip().endswith("]")
            continue

        if not current:
            continue
        current.append(line)
        if in_multiline_block and line.strip() == "]":
            in_multiline_block = False

    if current:
        blocks.append("\n".join(current))
    return blocks


def _build_response_example(number: int, source_text: str, translation_placeholder: str) -> str:
    lines = str(source_text or "").split("\n")
    if len(lines) <= 1:
        return f"{number}.{translation_placeholder}"

    rows = [f"{number}.["]
    total = len(lines)
    for index in range(total):
        suffix = "," if index < total - 1 else ""
        rows.append(f'"{number}.{total - index}.,{translation_placeholder}"{suffix}')
    rows.append("]")
    return "\n".join(rows)


def _strip_numbered_prefix(numbered_text: str | None) -> str:
    if not numbered_text:
        return ""
    match = _NUMBERED_PREFIX.match(numbered_text)
    return numbered_text[match.end():] if match else numbered_text


def _format_context_items(
    items: list[CacheItem],
    include_translation: bool,
    source_label: str,
    translation_label: str,
) -> str:
    rows = []
    for item in items or []:
        rows.append(f"{source_label} {item.source_text}")
        if include_translation:
            translated = str(item.final_text or "").strip()
            if translated and translated != str(item.source_text or "").strip():
                rows.append(f"{translation_label} {translated}")
    return "\n".join(rows)


def _prompt_labels(config) -> dict[str, str]:
    if _uses_chinese_prompt(config):
        return {
            "previous_heading": "### 上文原译对照（只读）",
            "lookahead_heading": "### 下文原译对照（只读）",
            "source": "原文：",
            "translation": "译文：",
            "existing_translation": "现有译文：",
            "missing": "<缺失>",
            "translation_placeholder": "译文",
        }
    return {
        "previous_heading": "### Previous source/translation context (read-only)",
        "lookahead_heading": "### Following source/translation context (read-only)",
        "source": "Source:",
        "translation": "Translation:",
        "existing_translation": "Existing translation:",
        "missing": "<missing>",
        "translation_placeholder": "Translation",
    }


def _build_incremental_context_rules(config, context_source_text_dict: dict[str, str], base_system_prompt: str) -> str:
    if not context_source_text_dict:
        return ""

    sections = []
    if config.prompt_dictionary_switch:
        glossary = PromptBuilder.build_glossary_prompt(config, context_source_text_dict)
        incremental = _filter_incremental_rule_rows(
            glossary,
            base_system_prompt,
            "###补全上下文新增术语" if _uses_chinese_prompt(config) else "###Additional Glossary Terms from Completion Context",
        )
        if incremental:
            sections.append(incremental)

    if config.exclusion_list_switch:
        exclusion = PromptBuilder.build_ntl_prompt(config, context_source_text_dict)
        incremental = _filter_incremental_rule_rows(
            exclusion,
            base_system_prompt,
            "###补全上下文新增禁翻项" if _uses_chinese_prompt(config) else "###Additional Non-Translation Items from Completion Context",
        )
        if incremental:
            sections.append(incremental)

    return "".join(sections)


def _filter_incremental_rule_rows(section: str, base_system_prompt: str, heading: str) -> str:
    lines = [line.strip() for line in str(section or "").splitlines() if line.strip()]
    if len(lines) <= 2:
        return ""

    base_lines = {line.strip() for line in str(base_system_prompt or "").splitlines() if line.strip()}
    new_rows = [line for line in lines[2:] if line not in base_lines]
    if not new_rows:
        return ""
    return "\n" + heading + "\n" + lines[1] + "\n" + "\n".join(new_rows)


def _uses_chinese_prompt(config) -> bool:
    return str(getattr(config, "target_language", "") or "").lower() in (
        "chinese_simplified",
        "chinese_traditional",
        "chinese",
        "zh",
    )


def _load_sparse_completion_prompt_template(config) -> str:
    suffix = "zh" if _uses_chinese_prompt(config) else "en"
    prompt_path = os.path.abspath(
        os.path.join(
            os.path.dirname(__file__),
            "..",
            "..",
            "..",
            "Resource",
            "Prompt",
            "System",
            f"translation_sparse_completion_{suffix}.txt",
        )
    )
    with open(prompt_path, "r", encoding="utf-8") as reader:
        return reader.read()
