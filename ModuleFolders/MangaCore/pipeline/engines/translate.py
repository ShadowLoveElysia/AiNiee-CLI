from __future__ import annotations

import json
import re
import socket
from dataclasses import dataclass, field
from pathlib import Path
from urllib.parse import urlparse

from ModuleFolders.Domain.ResponseExtractor.ResponseExtractor import ResponseExtractor
from ModuleFolders.Infrastructure.LLMRequester.LLMRequester import LLMRequester
from ModuleFolders.Infrastructure.TaskConfig.TaskConfig import TaskConfig
from ModuleFolders.Service.TaskExecutor.TranslatorUtil import get_language_display_names
from ModuleFolders.MangaCore.render.textNormalize import normalize_manga_dialogue_for_translation


_LANGUAGE_NORMALIZATION_MAP = {
    "auto": "auto",
    "ja": "japanese",
    "jp": "japanese",
    "japanese": "japanese",
    "zh": "chinese_simplified",
    "zh_cn": "chinese_simplified",
    "zh-cn": "chinese_simplified",
    "zh_hans": "chinese_simplified",
    "zh-hans": "chinese_simplified",
    "chinese": "chinese_simplified",
    "chinese_simplified": "chinese_simplified",
    "zh_tw": "chinese_traditional",
    "zh-tw": "chinese_traditional",
    "zh_hant": "chinese_traditional",
    "zh-hant": "chinese_traditional",
    "chinese_traditional": "chinese_traditional",
    "en": "english",
    "english": "english",
    "ko": "korean",
    "kr": "korean",
    "korean": "korean",
    "ru": "russian",
    "russian": "russian",
    "fr": "french",
    "french": "french",
    "de": "german",
    "german": "german",
    "es": "spanish",
    "spanish": "spanish",
}

_RULE_KEYS = [
    "prompt_dictionary_data",
    "exclusion_list_data",
    "characterization_data",
    "world_building_content",
    "writing_style_content",
    "translation_example_data",
]
_PROJECT_ROOT = Path(__file__).resolve().parents[4]


@dataclass(slots=True)
class TranslationBatchResult:
    ok: bool
    translations: dict[str, str] = field(default_factory=dict)
    prompt_tokens: int = 0
    completion_tokens: int = 0
    error_message: str = ""
    raw_response: str = ""
    missing_block_ids: list[str] = field(default_factory=list)


class TranslateEngine:
    stage = "translate"

    def __init__(self, logger=None, requester=None, response_extractor: ResponseExtractor | None = None) -> None:
        self.logger = logger or (lambda *_args, **_kwargs: None)
        self.requester = requester or LLMRequester()
        self.response_extractor = response_extractor or ResponseExtractor()

    def translate_blocks(
        self,
        session,
        blocks,
        *,
        source_lang: str = "",
        target_lang: str = "",
        batch_size: int = 20,
    ) -> TranslationBatchResult:
        prepared_blocks = [
            (block.block_id, normalize_manga_dialogue_for_translation(str(block.source_text or "")))
            for block in blocks
            if normalize_manga_dialogue_for_translation(str(block.source_text or ""))
        ]
        if not prepared_blocks:
            return TranslationBatchResult(ok=True)

        try:
            task_config, platform_config = self._build_runtime_config(
                session,
                source_lang=source_lang,
                target_lang=target_lang,
            )
        except Exception as exc:
            return TranslationBatchResult(ok=False, error_message=str(exc))

        aggregated = TranslationBatchResult(ok=True)
        for index in range(0, len(prepared_blocks), max(1, int(batch_size))):
            chunk = prepared_blocks[index:index + max(1, int(batch_size))]
            chunk_result = self._translate_chunk(task_config, platform_config, chunk)
            aggregated.prompt_tokens += chunk_result.prompt_tokens
            aggregated.completion_tokens += chunk_result.completion_tokens
            if chunk_result.raw_response:
                aggregated.raw_response = chunk_result.raw_response
            if chunk_result.translations:
                aggregated.translations.update(chunk_result.translations)
            if chunk_result.error_message:
                aggregated.ok = False
                aggregated.error_message = chunk_result.error_message if not aggregated.error_message else f"{aggregated.error_message}; {chunk_result.error_message}"

        missing_block_ids = [block_id for block_id, _source_text in prepared_blocks if block_id not in aggregated.translations]
        if missing_block_ids:
            aggregated.ok = False
            aggregated.missing_block_ids = missing_block_ids
            missing_message = f"Missing translations for {len(missing_block_ids)} block(s)."
            aggregated.error_message = missing_message if not aggregated.error_message else f"{aggregated.error_message}; {missing_message}"

        return aggregated

    def _build_runtime_config(self, session, *, source_lang: str = "", target_lang: str = "") -> tuple[TaskConfig, dict[str, object]]:
        config_dict = self._load_effective_config(session)
        task_config = TaskConfig()
        task_config.initialize(config_dict)

        snapshot = session.config_snapshot if isinstance(getattr(session, "config_snapshot", None), dict) else {}
        runtime_source = self._normalize_language_token(
            source_lang
            or getattr(session.manifest, "source_lang", "")
            or snapshot.get("source_lang")
            or config_dict.get("source_language", "auto")
        )
        runtime_target = self._normalize_language_token(
            target_lang
            or getattr(session.manifest, "target_lang", "")
            or snapshot.get("target_lang")
            or config_dict.get("target_language", "chinese_simplified")
        )

        task_config.source_language = runtime_source
        task_config.target_language = runtime_target
        task_config.label_input_path = str(snapshot.get("input_path") or config_dict.get("label_input_path", ""))
        task_config.label_output_path = str(snapshot.get("output_path") or config_dict.get("label_output_path", ""))

        platform_tag = str(
            snapshot.get("platform")
            or config_dict.get("api_settings", {}).get("translate")
            or config_dict.get("target_platform", "")
        ).strip()
        if not platform_tag:
            raise ValueError("No translation platform configured for MangaCore.")

        platform_conf = dict(task_config.platforms.get(platform_tag) or {})
        if not platform_conf:
            raise ValueError(f"Translation platform is not available: {platform_tag}")

        task_config.target_platform = platform_tag
        task_config.api_settings = dict(getattr(task_config, "api_settings", {}) or {})
        task_config.api_settings["translate"] = platform_tag

        raw_api_key = str(snapshot.get("api_key") or config_dict.get("api_key") or platform_conf.get("api_key", "")).strip()
        api_key = re.sub(r"\s+", "", raw_api_key).split(",")[0] if raw_api_key else "no_key_required"

        model_name = str(snapshot.get("model") or config_dict.get("model") or platform_conf.get("model", "")).strip()
        if not model_name:
            raise ValueError(f"No translation model configured for platform: {platform_tag}")

        raw_api_url = str(snapshot.get("api_url") or config_dict.get("base_url") or platform_conf.get("api_url", "")).strip()
        auto_complete = bool(platform_conf.get("auto_complete", False))
        api_url = task_config.process_api_url(raw_api_url, platform_tag, auto_complete) if raw_api_url else ""

        platform_config = {
            "target_platform": platform_tag,
            "api_url": api_url,
            "api_key": api_key,
            "api_format": platform_conf.get("api_format"),
            "model_name": model_name,
            "region": platform_conf.get("region", ""),
            "access_key": platform_conf.get("access_key", ""),
            "secret_key": platform_conf.get("secret_key", ""),
            "request_timeout": task_config.request_timeout,
            "temperature": platform_conf.get("temperature"),
            "top_p": platform_conf.get("top_p"),
            "presence_penalty": platform_conf.get("presence_penalty"),
            "frequency_penalty": platform_conf.get("frequency_penalty"),
            "extra_body": platform_conf.get("extra_body", {}),
            "think_switch": platform_conf.get("think_switch"),
            "think_depth": platform_conf.get("think_depth"),
            "thinking_budget": platform_conf.get("thinking_budget", -1),
            "structured_output_mode": platform_conf.get("structured_output_mode", 0),
            "auto_complete": auto_complete,
            "enable_stream_api": getattr(task_config, "enable_stream_api", True),
            "enable_prompt_caching": getattr(task_config, "enable_prompt_caching", False),
            "sdk_request_mode": getattr(task_config, "sdk_request_mode", "httpx"),
            "use_openai_sdk": getattr(task_config, "use_openai_sdk", False),
        }
        return task_config, platform_config

    def _translate_chunk(
        self,
        task_config: TaskConfig,
        platform_config: dict[str, object],
        chunk: list[tuple[str, str]],
    ) -> TranslationBatchResult:
        source_text_dict = {str(index): source_text for index, (_block_id, source_text) in enumerate(chunk)}
        block_ids = [block_id for block_id, _source_text in chunk]
        messages = [
            {
                "role": "user",
                "content": self._build_manga_source_text(source_text_dict),
            }
        ]
        system_prompt = self._build_system_prompt(task_config.source_language, task_config.target_language)

        if isinstance(self.requester, LLMRequester) and not self._is_endpoint_ready(platform_config):
            return TranslationBatchResult(
                ok=False,
                error_message=f"Translation API endpoint is not reachable: {platform_config.get('api_url')}",
            )

        skip, response_think, response_content, prompt_tokens, completion_tokens = self._send_request(
            messages,
            system_prompt,
            platform_config,
        )
        if skip:
            error_message = str(response_content or response_think or "Translation request failed.")
            return TranslationBatchResult(
                ok=False,
                prompt_tokens=prompt_tokens,
                completion_tokens=completion_tokens,
                error_message=error_message,
                raw_response=str(response_content or ""),
            )

        parsed = self._parse_translations(source_text_dict, str(response_content or ""))
        translations: dict[str, str] = {}
        for key, text in parsed.items():
            try:
                position = int(key)
            except (TypeError, ValueError):
                continue
            if 0 <= position < len(block_ids) and str(text).strip():
                translations[block_ids[position]] = str(text).strip()

        return TranslationBatchResult(
            ok=len(translations) == len(block_ids),
            translations=translations,
            prompt_tokens=prompt_tokens,
            completion_tokens=completion_tokens,
            raw_response=str(response_content or ""),
        )

    def _build_system_prompt(self, source_lang: str, target_lang: str) -> str:
        normalized_source = self._normalize_language_token(source_lang)
        normalized_target = self._normalize_language_token(target_lang)
        en_source, zh_source, en_target, zh_target = get_language_display_names(normalized_source, normalized_target)

        if normalized_target in ("chinese_simplified", "chinese_traditional"):
            return (
                "你是一位专业的漫画翻译助手。\n"
                f"请把用户提供的文本块从{zh_source}翻译成{zh_target}。\n"
                "必须逐条翻译，保持编号顺序，不得遗漏、合并或解释。\n"
                "漫画 OCR 的换行通常只是竖排列或检测碎片，不代表中文断句；请先理解为同一个气泡中的一句或一段话。\n"
                "译文必须使用自然、流畅的中文语序，除非语义上确实需要分段，否则不要保留 OCR 换行或逐碎片翻译。\n"
                "不要为了排版强制换行，最终断行会由嵌字排版器处理。\n"
                "只输出被 <textarea> 包裹的结果，格式如下：\n"
                "<textarea>\n"
                "1.译文\n"
                "2.译文\n"
                "</textarea>"
            )

        return (
            "You are a professional manga translation assistant.\n"
            f"Translate each numbered text block from {en_source} into {en_target}.\n"
            "Do not omit, merge, reorder, or explain any item.\n"
            "Manga OCR line breaks often come from vertical columns or detector fragments, not semantic sentence breaks.\n"
            "Translate each block into natural target-language word order; do not preserve OCR line breaks unless they are semantically necessary.\n"
            "Do not insert layout-driven line breaks; the typesetting engine will wrap the text.\n"
            "Return only the result inside a <textarea> block using this format:\n"
            "<textarea>\n"
            "1.translation\n"
            "2.translation\n"
            "</textarea>"
        )

    def _parse_translations(self, source_text_dict: dict[str, str], response_content: str) -> dict[str, str]:
        extracted = self.response_extractor.text_extraction(source_text_dict, response_content)
        extracted = self.response_extractor.normalize_numbered_prefixes(extracted, source_text_dict)
        cleaned = {
            str(key): value
            for key, value in extracted.items()
            if str(value).strip()
        }
        if cleaned:
            return self.response_extractor.remove_numbered_prefix(cleaned, source_text_dict)

        textarea_matches = re.findall(r"<textarea.*?>(.*?)</textarea>", response_content, flags=re.DOTALL | re.IGNORECASE)
        content = textarea_matches[-1] if textarea_matches else response_content
        matches = re.findall(
            rf"^\s*(\d+)[{ResponseExtractor.numbered_separator_chars}](.*?)(?=^\s*\d+[{ResponseExtractor.numbered_separator_chars}]|\Z)",
            content,
            flags=re.MULTILINE | re.DOTALL,
        )
        fallback: dict[str, str] = {}
        for index_text, text in matches:
            try:
                fallback[str(max(0, int(index_text) - 1))] = self._clean_translation_text(text)
            except ValueError:
                continue
        if fallback:
            return fallback

        plain = str(content or "").strip()
        if plain and len(source_text_dict) == 1:
            return {"0": self._clean_translation_text(plain)}
        return {}

    @staticmethod
    def _build_manga_source_text(source_text_dict: dict[str, str]) -> str:
        return "\n".join(f"{int(key) + 1}.{value}" for key, value in source_text_dict.items())

    @staticmethod
    def _normalize_language_token(value: str) -> str:
        token = str(value or "").strip()
        if not token:
            return "auto"
        lowered = token.lower().replace("-", "_")
        return _LANGUAGE_NORMALIZATION_MAP.get(lowered, lowered)

    @staticmethod
    def _clean_translation_text(value: object) -> str:
        text = str(value or "").strip().strip('"').strip()
        return re.sub(r"(?m)^\s*\d+(?:\.\d+)*(?:[.．][,，、]?|[,，、])\s*", "", text).strip()

    def _load_effective_config(self, session) -> dict[str, object]:
        root_config_path = _PROJECT_ROOT / "Resource" / "config.json"
        preset_path = _PROJECT_ROOT / "Resource" / "platforms" / "preset.json"
        profiles_dir = _PROJECT_ROOT / "Resource" / "profiles"
        rules_profiles_dir = _PROJECT_ROOT / "Resource" / "rules_profiles"

        root_config = self._read_json(root_config_path)
        snapshot = session.config_snapshot if isinstance(getattr(session, "config_snapshot", None), dict) else {}
        profile_name = str(
            getattr(session.manifest, "profile_name", "")
            or snapshot.get("profile_name")
            or root_config.get("active_profile", "default")
        )
        rules_profile_name = str(
            getattr(session.manifest, "rules_profile_name", "")
            or snapshot.get("rules_profile_name")
            or root_config.get("active_rules_profile", "default")
        )

        merged = self._read_json(preset_path)
        profile_path = profiles_dir / f"{profile_name}.json"
        profile_config = self._read_json(profile_path)
        if isinstance(profile_config, dict):
            for key, value in profile_config.items():
                if isinstance(value, dict) and isinstance(merged.get(key), dict):
                    merged[key].update(value)
                else:
                    merged[key] = value

        if rules_profile_name and rules_profile_name != "None":
            rules_path = rules_profiles_dir / f"{rules_profile_name}.json"
            rules_config = self._read_json(rules_path)
            if isinstance(rules_config, dict):
                for key in _RULE_KEYS:
                    if key in rules_config:
                        merged[key] = rules_config[key]

        return merged

    @staticmethod
    def _read_json(path: Path) -> dict[str, object]:
        if not path.exists():
            return {}
        try:
            with open(path, "r", encoding="utf-8-sig") as handle:
                payload = json.load(handle)
            return payload if isinstance(payload, dict) else {}
        except Exception:
            return {}

    @staticmethod
    def _is_endpoint_ready(platform_config: dict[str, object]) -> bool:
        api_url = str(platform_config.get("api_url") or "").strip()
        if not api_url:
            return False

        parsed = urlparse(api_url)
        hostname = (parsed.hostname or "").lower()
        if hostname not in {"127.0.0.1", "localhost", "0.0.0.0", "::1"}:
            return True

        port = parsed.port or (443 if parsed.scheme == "https" else 80)
        try:
            with socket.create_connection((parsed.hostname or "127.0.0.1", port), timeout=0.3):
                return True
        except OSError:
            return False

    def _send_request(self, messages, system_prompt: str, platform_config: dict[str, object]):
        if not isinstance(self.requester, LLMRequester):
            return self.requester.sent_request(messages, system_prompt, platform_config)

        target_platform = platform_config.get("target_platform")
        api_format = platform_config.get("api_format")
        request_messages = [message.copy() if isinstance(message, dict) else message for message in messages]

        if target_platform == "sakura":
            from ModuleFolders.Infrastructure.LLMRequester.SakuraRequester import SakuraRequester

            return SakuraRequester().request_sakura(request_messages, system_prompt, platform_config)
        if target_platform == "murasaki":
            from ModuleFolders.Infrastructure.LLMRequester.MurasakiRequester import MurasakiRequester

            return MurasakiRequester().request_murasaki(request_messages, system_prompt, platform_config)
        if target_platform == "LocalLLM":
            from ModuleFolders.Infrastructure.LLMRequester.LocalLLMRequester import LocalLLMRequester

            return LocalLLMRequester().request_LocalLLM(request_messages, system_prompt, platform_config)
        if target_platform == "cohere":
            from ModuleFolders.Infrastructure.LLMRequester.CohereRequester import CohereRequester

            return CohereRequester().request_cohere(request_messages, system_prompt, platform_config)
        if target_platform == "google" or (str(target_platform).startswith("custom_platform_") and api_format == "Google"):
            from ModuleFolders.Infrastructure.LLMRequester.GoogleRequester import GoogleRequester

            return GoogleRequester().request_google(request_messages, system_prompt, platform_config)
        if target_platform == "anthropic" or (str(target_platform).startswith("custom_platform_") and api_format == "Anthropic"):
            from ModuleFolders.Infrastructure.LLMRequester.AnthropicRequester import AnthropicRequester

            return AnthropicRequester().request_anthropic(request_messages, system_prompt, platform_config)
        if target_platform == "amazonbedrock":
            from ModuleFolders.Infrastructure.LLMRequester.AmazonbedrockRequester import AmazonbedrockRequester

            return AmazonbedrockRequester().request_amazonbedrock(request_messages, system_prompt, platform_config)
        if target_platform == "dashscope":
            from ModuleFolders.Infrastructure.LLMRequester.DashscopeRequester import DashscopeRequester

            return DashscopeRequester().request_openai(request_messages, system_prompt, platform_config)

        from ModuleFolders.Infrastructure.LLMRequester.OpenaiRequester import OpenaiRequester

        return OpenaiRequester().request_openai(request_messages, system_prompt, platform_config)
