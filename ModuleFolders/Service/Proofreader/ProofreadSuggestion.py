"""
Proofread suggestion data model and cache write-back helpers.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
from dataclasses import asdict, dataclass, field, fields
from enum import StrEnum
from typing import Any

from ModuleFolders.Infrastructure.Cache.CacheItem import CacheItem, TranslationStatus
from ModuleFolders.Infrastructure.Cache.CacheProject import CacheProject


PROOFREAD_SUGGESTION_MODES = {"proofread", "annotation"}
ANNOTATION_ISSUE_TYPE = "annotation"
ANNOTATION_SEVERITY = "info"
LINE_BREAK_CHARACTERS = frozenset("\n\r\v\f\x1c\x1d\x1e\x85\u2028\u2029")
INVALID_SUGGESTION_TEXTS = frozenset(
    {
        "<missing>",
        "missing",
        "<缺失>",
        "缺失",
        "translation",
        "<translation>",
        "译文",
        "<译文>",
        "待翻译",
        "未翻译",
        "n/a",
        "na",
        "todo",
        "missing translation",
    }
)


class ProofreadSuggestionStatus(StrEnum):
    PENDING = "pending"
    ACCEPTED = "accepted"
    REJECTED = "rejected"
    IGNORED = "ignored"
    STALE = "stale"
    CONFLICT = "conflict"
    COMPLETED = "completed"


@dataclass
class ProofreadSuggestionLine:
    batch_id: str
    batch_hash: str
    line_no: int
    item_id: str
    file_path: str
    text_index: int
    target_field: str
    source_text: str
    current_translation: str
    line_hash: str
    manually_edited: bool = False
    allow_suggestion: bool = True


@dataclass
class ProofreadBatch:
    batch_id: str
    batch_hash: str
    lines: list[ProofreadSuggestionLine]


@dataclass
class ProofreadSuggestion:
    suggestion_id: str
    batch_id: str
    batch_hash: str
    line_no: int
    item_id: str
    file_path: str
    text_index: int
    target_field: str
    source_text: str
    current_translation: str
    suggested_translation: str
    reason: str
    severity: str
    issue_type: str
    confidence: float
    line_hash: str
    status: ProofreadSuggestionStatus = ProofreadSuggestionStatus.PENDING
    original_translation: str = ""
    applied_translation: str = ""
    run_id: str = ""
    status_updated_at: str = ""
    status_updated_by: str = ""
    last_action: str = ""
    accepted_line_hash: str = ""
    undo_available: bool = False
    extra: dict[str, Any] = field(default_factory=dict)
    annotation_target: str = ""
    annotation_text: str = ""

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        extra = data.pop("extra", {})
        data["status"] = str(self.status)
        for key, value in extra.items():
            if key not in data:
                data[key] = value
        return data

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "ProofreadSuggestion":
        known_fields = {item.name for item in fields(cls)}
        payload = {key: value for key, value in data.items() if key in known_fields}
        extra = dict(payload.pop("extra", {}) or {})
        extra.update({key: value for key, value in data.items() if key not in known_fields})
        try:
            payload["status"] = ProofreadSuggestionStatus(payload.get("status", "pending"))
        except ValueError:
            payload["status"] = ProofreadSuggestionStatus.PENDING
            extra["legacy_status"] = data.get("status")
        payload["extra"] = extra
        return cls(**payload)


@dataclass
class ProofreadSuggestionParseResult:
    batch_id: str
    batch_hash: str
    closed_without_suggestions: bool = False
    suggestions: list[ProofreadSuggestion] = field(default_factory=list)


@dataclass
class ProofreadApplyResult:
    suggestion_id: str
    status: ProofreadSuggestionStatus
    message: str = ""


def normalize_suggestion_text(text: Any) -> str:
    return str(text or "").replace("\r\n", "\n").replace("\r", "\n").strip()


def is_actionable_suggestion_text(value: Any) -> bool:
    text = normalize_suggestion_text(value)
    normalized = text.casefold().strip()
    wrappers = {
        "<": ">",
        "[": "]",
        "(": ")",
        "（": "）",
        "【": "】",
    }
    while len(normalized) >= 2 and wrappers.get(normalized[0]) == normalized[-1]:
        normalized = normalized[1:-1].strip()
    normalized = re.sub(r"\s+", " ", normalized)
    return bool(normalized) and normalized not in INVALID_SUGGESTION_TEXTS


def normalize_suggestion_mode(value: Any) -> str:
    mode = str(value or "proofread").strip().lower()
    if mode not in PROOFREAD_SUGGESTION_MODES:
        raise ValueError(f"unsupported proofread suggestion mode: {value}")
    return mode


def contains_line_break(value: Any) -> bool:
    return any(char in LINE_BREAK_CHARACTERS for char in str(value or ""))


def normalize_annotation_text(value: Any) -> str:
    raw_text = str(value or "")
    if contains_line_break(raw_text):
        return ""
    text = normalize_suggestion_text(raw_text)
    if not text:
        return ""

    wrappers = (
        ("（注：", "）"),
        ("（注:", "）"),
        ("(注：", ")"),
        ("(注:", ")"),
    )
    for prefix, suffix in wrappers:
        if text.startswith(prefix):
            if not text.endswith(suffix):
                return ""
            text = text[len(prefix):-len(suffix)].strip()
            break
    if text.startswith("注：") or text.startswith("注:"):
        text = text[2:].strip()

    if not text or contains_line_break(text) or any(char in text for char in "()（）"):
        return ""
    if any(prefix in text for prefix, _ in wrappers):
        return ""
    return text


def build_annotation_translation(current_translation: str, annotation_text: Any) -> str:
    translation = str(current_translation or "")
    note = normalize_annotation_text(annotation_text)
    if not translation or contains_line_break(translation):
        raise ValueError("annotation target translation must be a single line")
    if not note:
        raise ValueError("invalid annotation text")
    if re.search(r"[（(]注[:：].*[）)]\s*$", translation):
        raise ValueError("translation already contains a trailing annotation")
    return f"{translation} （注：{note}）"


def line_hash(source_text: str, translation: str, target_field: str) -> str:
    payload = {
        "source_text": str(source_text or ""),
        "translation": str(translation or ""),
        "target_field": target_field,
    }
    raw = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def batch_hash(lines: list[ProofreadSuggestionLine]) -> str:
    payload = [
        {
            "line_no": line.line_no,
            "item_id": line.item_id,
            "line_hash": line.line_hash,
            "manually_edited": line.manually_edited,
            "allow_suggestion": line.allow_suggestion,
        }
        for line in lines
    ]
    raw = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def suggestion_id_for(batch_id: str, item_id: str, line_hash_value: str, suggested_translation: str) -> str:
    raw = json.dumps(
        {
            "batch_id": batch_id,
            "item_id": item_id,
            "line_hash": line_hash_value,
            "suggested_translation": normalize_suggestion_text(suggested_translation),
        },
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:16]


def target_field_for_item(item: CacheItem) -> str:
    if item.polished_text and item.translation_status in {
        TranslationStatus.POLISHED,
        TranslationStatus.USER_PROOFREAD,
        TranslationStatus.AI_PROOFREAD,
    }:
        return "polished_text"
    return "translated_text"


def translation_for_item(item: CacheItem, target_field: str) -> str:
    if target_field == "polished_text":
        return item.polished_text or ""
    return item.translated_text or ""


def item_id_for(file_path: str, text_index: int) -> str:
    return f"{file_path}:{text_index}"


def collect_suggestion_items(project: CacheProject) -> list[dict[str, Any]]:
    items: list[dict[str, Any]] = []
    for file_path, cache_file in project.files.items():
        for item in cache_file.items:
            if item.translation_status not in {
                TranslationStatus.UNTRANSLATED,
                TranslationStatus.TRANSLATED,
                TranslationStatus.POLISHED,
                TranslationStatus.USER_PROOFREAD,
                TranslationStatus.AI_PROOFREAD,
            }:
                continue
            target_field = target_field_for_item(item)
            translation = translation_for_item(item, target_field)
            if not item.source_text:
                continue
            manually_edited = bool((item.extra or {}).get("proofread_manual_edit"))
            items.append(
                {
                    "file_path": file_path,
                    "text_index": int(item.text_index),
                    "source_text": item.source_text,
                    "translation": translation,
                    "target_field": target_field,
                    "manually_edited": manually_edited,
                    "allow_suggestion": not manually_edited,
                }
            )
    return items


def build_proofread_batch(items: list[dict[str, Any]], batch_index: int, batch_size: int) -> ProofreadBatch:
    start = batch_index * batch_size
    selected = items[start:start + batch_size]
    batch_id = f"proofread_{batch_index + 1:05d}"
    lines: list[ProofreadSuggestionLine] = []
    for offset, item in enumerate(selected, start=1):
        target_field = str(item.get("target_field") or "translated_text")
        raw_source_text = str(item.get("source_text") or "")
        raw_translation = str(item.get("translation") or "")
        source_text = normalize_suggestion_text(raw_source_text)
        translation = normalize_suggestion_text(raw_translation)
        file_path = str(item.get("file_path", ""))
        text_index = int(item.get("text_index", 0))
        item_id = item_id_for(file_path, text_index)
        line_hash_value = line_hash(raw_source_text, raw_translation, target_field)
        manually_edited = bool(item.get("manually_edited", False))
        allow_suggestion = not manually_edited and bool(item.get("allow_suggestion", True))
        lines.append(
            ProofreadSuggestionLine(
                batch_id=batch_id,
                batch_hash="",
                line_no=offset,
                item_id=item_id,
                file_path=file_path,
                text_index=text_index,
                target_field=target_field,
                source_text=source_text,
                current_translation=translation,
                line_hash=line_hash_value,
                manually_edited=manually_edited,
                allow_suggestion=allow_suggestion,
            )
        )

    digest = batch_hash(lines)
    for line in lines:
        line.batch_hash = digest
    return ProofreadBatch(batch_id=batch_id, batch_hash=digest, lines=lines)


def build_suggestion_prompt(
    batch: ProofreadBatch,
    glossary: list[dict[str, Any]] | None = None,
    context_lines: list[dict[str, Any]] | None = None,
    suggestion_mode: str = "proofread",
) -> str:
    mode = normalize_suggestion_mode(suggestion_mode)
    context_payload = []
    for item in context_lines or []:
        context_payload.append(
            {
                "source": normalize_suggestion_text(item.get("source", "")),
                "translation": normalize_suggestion_text(item.get("translation", "")),
            }
        )

    glossary_payload = []
    for item in glossary or []:
        glossary_payload.append(
            {
                "src": normalize_suggestion_text(item.get("src", "")),
                "dst": normalize_suggestion_text(item.get("dst", "")),
            }
        )

    line_payload = [
        {
            "line_no": line.line_no,
            "item_id": line.item_id,
            "line_hash": line.line_hash,
            "source": line.source_text,
            "current_translation": line.current_translation,
            "manually_edited": line.manually_edited,
            "allow_suggestion": line.allow_suggestion,
        }
        for line in batch.lines
    ]

    replacements = {
        "{{batch_id}}": batch.batch_id,
        "{{batch_hash}}": batch.batch_hash,
        "{{batch_info}}": json.dumps(
            {"batch_id": batch.batch_id, "batch_hash": batch.batch_hash},
            ensure_ascii=False,
            indent=2,
        ),
        "{{context_lines}}": json.dumps(context_payload, ensure_ascii=False, indent=2),
        "{{glossary}}": json.dumps(glossary_payload[:80], ensure_ascii=False, indent=2),
        "{{proofread_lines}}": json.dumps(line_payload, ensure_ascii=False, indent=2),
    }

    prompt = _load_suggestion_prompt_template(mode)
    for placeholder, value in replacements.items():
        prompt = prompt.replace(placeholder, value)
    return prompt


def _load_suggestion_prompt_template(suggestion_mode: str = "proofread") -> str:
    mode = normalize_suggestion_mode(suggestion_mode)
    filename = "proofread_annotation_zh.txt" if mode == "annotation" else "proofread_suggestion_zh.txt"
    prompt_path = os.path.join(
        os.path.dirname(__file__),
        "..",
        "..",
        "..",
        "Resource",
        "Prompt",
        "System",
        filename,
    )
    if os.path.exists(prompt_path):
        with open(prompt_path, "r", encoding="utf-8") as reader:
            return reader.read().strip()
    raise FileNotFoundError(prompt_path)


def parse_suggestion_response(
    response: Any,
    batch: ProofreadBatch,
    suggestion_mode: str = "proofread",
) -> ProofreadSuggestionParseResult:
    mode = normalize_suggestion_mode(suggestion_mode)
    if isinstance(response, str) and response.strip() == "null":
        return ProofreadSuggestionParseResult(batch.batch_id, batch.batch_hash, True, [])

    data = _decode_suggestion_payload(response)

    if data is None:
        return ProofreadSuggestionParseResult(batch.batch_id, batch.batch_hash, False, [])
    if not isinstance(data, dict):
        return ProofreadSuggestionParseResult(batch.batch_id, batch.batch_hash, False, [])
    if data.get("batch_id") != batch.batch_id or data.get("batch_hash") != batch.batch_hash:
        return ProofreadSuggestionParseResult(batch.batch_id, batch.batch_hash, False, [])
    raw_suggestions = data.get("suggestions")
    if not isinstance(raw_suggestions, list) or not raw_suggestions:
        return ProofreadSuggestionParseResult(batch.batch_id, batch.batch_hash, False, [])

    line_by_no = {line.line_no: line for line in batch.lines}
    suggestions: list[ProofreadSuggestion] = []
    for raw in raw_suggestions:
        if not isinstance(raw, dict):
            continue
        try:
            line_no = int(raw.get("line_no"))
        except (TypeError, ValueError):
            continue
        line = line_by_no.get(line_no)
        if line is None:
            continue
        if raw.get("item_id") != line.item_id or raw.get("line_hash") != line.line_hash:
            continue
        # Protection is determined exclusively from the trusted local batch. Model
        # output cannot grant itself permission to modify a manually edited line.
        if line.manually_edited or not line.allow_suggestion:
            continue

        severity = str(raw.get("severity") or "low").strip().lower()
        issue_type = str(raw.get("issue_type") or "translation").strip().lower()
        annotation_target = ""
        annotation_text = ""
        if mode == "annotation":
            if severity != ANNOTATION_SEVERITY or issue_type != ANNOTATION_ISSUE_TYPE:
                continue
            raw_annotation_target = raw.get("annotation_target", "")
            if contains_line_break(raw_annotation_target):
                continue
            annotation_target = normalize_suggestion_text(raw_annotation_target)
            annotation_text = normalize_annotation_text(raw.get("annotation_text", ""))
            if (
                not annotation_target
                or contains_line_break(annotation_target)
                or annotation_target not in line.source_text
                or not annotation_text
            ):
                continue
            try:
                suggested_translation = build_annotation_translation(
                    line.current_translation,
                    annotation_text,
                )
            except ValueError:
                continue
        else:
            if issue_type == ANNOTATION_ISSUE_TYPE or severity not in {"high", "medium"}:
                continue
            raw_suggested_translation = raw.get(
                "suggested_translation",
                raw.get("suggested_text", ""),
            )
            if contains_line_break(raw_suggested_translation):
                continue
            suggested_translation = normalize_suggestion_text(
                raw_suggested_translation
            )
            if not is_actionable_suggestion_text(suggested_translation) or contains_line_break(suggested_translation):
                continue
            if suggested_translation == line.current_translation:
                continue

        confidence = _safe_float(raw.get("confidence", 0.0))
        suggestion = ProofreadSuggestion(
            suggestion_id=suggestion_id_for(batch.batch_id, line.item_id, line.line_hash, suggested_translation),
            batch_id=batch.batch_id,
            batch_hash=batch.batch_hash,
            line_no=line.line_no,
            item_id=line.item_id,
            file_path=line.file_path,
            text_index=line.text_index,
            target_field=line.target_field,
            source_text=line.source_text,
            current_translation=line.current_translation,
            suggested_translation=suggested_translation,
            reason=normalize_suggestion_text(raw.get("reason", "")),
            severity=severity,
            issue_type=issue_type,
            confidence=confidence,
            line_hash=line.line_hash,
            annotation_target=annotation_target,
            annotation_text=annotation_text,
        )
        suggestions.append(suggestion)

    return ProofreadSuggestionParseResult(batch.batch_id, batch.batch_hash, False, suggestions)


def _decode_suggestion_payload(response: Any) -> Any:
    if response is None:
        return None
    if not isinstance(response, str):
        return response

    stripped = response.strip()
    if not stripped:
        return None

    stripped = _strip_markdown_fence(stripped).strip()

    candidates = [stripped]
    if stripped.startswith('"') and stripped.endswith('"'):
        candidates.append(stripped.strip('"').strip())

    json_object = _extract_first_json_object(stripped)
    if json_object and json_object != stripped:
        candidates.append(json_object)

    for candidate in candidates:
        candidate = candidate.strip()
        try:
            return json.loads(candidate)
        except json.JSONDecodeError:
            continue
    return None


def _strip_markdown_fence(text: str) -> str:
    fence_match = re.search(r"```(?:json)?\s*(.*?)```", text, re.IGNORECASE | re.DOTALL)
    if fence_match:
        return fence_match.group(1)
    return text


def _extract_first_json_object(text: str) -> str:
    start = text.find("{")
    if start < 0:
        return ""
    depth = 0
    in_string = False
    escape = False
    for index in range(start, len(text)):
        char = text[index]
        if escape:
            escape = False
            continue
        if char == "\\":
            escape = True
            continue
        if char == '"':
            in_string = not in_string
            continue
        if in_string:
            continue
        if char == "{":
            depth += 1
        elif char == "}":
            depth -= 1
            if depth == 0:
                return text[start:index + 1]
    return ""


def apply_suggestion_to_project(project: CacheProject, suggestion: ProofreadSuggestion) -> ProofreadApplyResult:
    cache_item = find_cache_item(project, suggestion.file_path, suggestion.text_index)
    if cache_item is None:
        suggestion.status = ProofreadSuggestionStatus.CONFLICT
        return ProofreadApplyResult(suggestion.suggestion_id, suggestion.status, "cache item not found")

    current_translation = translation_for_item(cache_item, suggestion.target_field)
    current_hash = line_hash(cache_item.source_text, current_translation, suggestion.target_field)
    if current_hash != suggestion.line_hash:
        suggestion.status = ProofreadSuggestionStatus.CONFLICT
        return ProofreadApplyResult(suggestion.suggestion_id, suggestion.status, "line hash mismatch")

    applied_translation = suggestion.suggested_translation
    if str(suggestion.issue_type).strip().lower() == ANNOTATION_ISSUE_TYPE:
        if contains_line_break(suggestion.annotation_target) or contains_line_break(
            suggestion.annotation_text
        ):
            suggestion.status = ProofreadSuggestionStatus.CONFLICT
            return ProofreadApplyResult(suggestion.suggestion_id, suggestion.status, "invalid annotation data")
        annotation_target = normalize_suggestion_text(suggestion.annotation_target)
        annotation_text = normalize_annotation_text(suggestion.annotation_text)
        if (
            not annotation_target
            or contains_line_break(annotation_target)
            or annotation_target not in cache_item.source_text
            or not annotation_text
        ):
            suggestion.status = ProofreadSuggestionStatus.CONFLICT
            return ProofreadApplyResult(suggestion.suggestion_id, suggestion.status, "invalid annotation data")
        try:
            applied_translation = build_annotation_translation(current_translation, annotation_text)
        except ValueError as exc:
            suggestion.status = ProofreadSuggestionStatus.CONFLICT
            return ProofreadApplyResult(suggestion.suggestion_id, suggestion.status, str(exc))
        suggestion.annotation_target = annotation_target
        suggestion.annotation_text = annotation_text
        suggestion.suggested_translation = applied_translation

    suggestion.original_translation = current_translation
    suggestion.applied_translation = applied_translation
    if suggestion.target_field == "polished_text":
        cache_item.polished_text = applied_translation
    else:
        cache_item.translated_text = applied_translation
        cache_item.polished_text = ""
    cache_item.translation_status = TranslationStatus.USER_PROOFREAD
    if cache_item.extra is None:
        cache_item.extra = {}
    cache_item.extra["proofread_suggestion"] = {
        "suggestion_id": suggestion.suggestion_id,
        "batch_id": suggestion.batch_id,
        "status": "accepted",
        "target_field": suggestion.target_field,
        "previous_line_hash": suggestion.line_hash,
        "issue_type": suggestion.issue_type,
    }
    suggestion.status = ProofreadSuggestionStatus.ACCEPTED
    return ProofreadApplyResult(suggestion.suggestion_id, suggestion.status)


def find_cache_item(project: CacheProject, file_path: str, text_index: int) -> CacheItem | None:
    cache_file = project.files.get(file_path)
    if cache_file is None:
        return None
    for item in cache_file.items:
        if int(item.text_index) == int(text_index):
            return item
    return None


def _safe_float(value: Any) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return 0.0
