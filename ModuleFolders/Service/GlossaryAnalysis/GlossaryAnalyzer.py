"""
术语分析核心服务 - 从 ainiee_cli.py 分离
负责AI自动分析术语表的核心逻辑
"""
import os
import re
import threading
import concurrent.futures
from datetime import datetime
import rapidjson as json

from rich.console import Console

from ModuleFolders.Infrastructure.TaskConfig.ConfigProfileService import (
    atomic_write_json,
    normalize_rules_payload,
    resolve_profile_path,
    sanitize_profile_name,
    save_root_config,
)

console = Console()


STRUCTURED_RULE_KEYS = (
    "prompt_dictionary_data",
    "exclusion_list_data",
    "characterization_data",
    "world_building_content",
    "world_building_history",
    "writing_style_content",
    "writing_style_history",
    "translation_example_data",
)

LEGACY_ANALYSIS_MODES = ("normal_extract", "legacy_translation_extract", "legacy_mapping_extract")
LEGACY_GLOSSARY_ORIGIN = "legacy_translation"
LEGACY_MAPPED_ORIGIN = "mapped_from_legacy_translation"
LEGACY_METADATA_FIELDS = (
    "_glossary_origin",
    "_mapping_status",
    "_legacy_id",
    "_mapped_from",
    "_mapping_confidence",
    "_mapping_note",
)
GLOSSARY_TIMELINE_FIELDS = ("dst", "info", *LEGACY_METADATA_FIELDS)
CHARACTER_TIMELINE_FIELDS = (
    "translated_name",
    "aliases",
    "gender",
    "age",
    "personality",
    "speech_style",
    "pronouns",
    "speech_quirks",
    "additional_info",
    *LEGACY_METADATA_FIELDS,
)
DEFAULT_GLOSSARY_TOKEN_WARNING_THRESHOLD = 256_000
DEFAULT_INCREMENTAL_SPLIT_TARGET_TOKENS = 200_000
MAX_INCREMENTAL_SPLIT_TARGET_TOKENS = 256_000
GLOSSARY_ANALYSIS_MODES = ("full", "split", "incremental_split")


class GlossaryAnalyzer:
    """术语分析器，处理AI自动分析术语表的核心逻辑"""

    def __init__(self, cli_menu):
        """
        初始化术语分析器

        Args:
            cli_menu: CLIMenu实例，用于访问配置和其他依赖
        """
        self.cli = cli_menu

    @property
    def config(self):
        return self.cli.config

    @property
    def i18n(self):
        return self.cli.i18n

    def _tr(self, key, default=None, *args):
        value = self.i18n.get(key) if self.i18n else None
        if not value or value == key:
            value = default if default is not None else key
        if args:
            try:
                return value.format(*args)
            except Exception:
                return value
        return value

    @property
    def PROJECT_ROOT(self):
        return self.cli.PROJECT_ROOT

    @property
    def file_reader(self):
        return self.cli.file_reader

    def save_config(self):
        self.cli.save_config()

    def get_token_warning_threshold(self):
        try:
            threshold = int(self.config.get("glossary_analysis_token_warning_threshold") or 0)
        except (TypeError, ValueError):
            threshold = 0
        return threshold if threshold > 0 else DEFAULT_GLOSSARY_TOKEN_WARNING_THRESHOLD

    def get_incremental_split_target_tokens(self):
        return self._resolve_incremental_split_target_tokens(None)

    def get_incremental_split_target_token_limit(self):
        return MAX_INCREMENTAL_SPLIT_TARGET_TOKENS

    def normalize_legacy_mode(self, value):
        mode = self._normalize_glossary_text(value or "normal_extract")
        return mode if mode in LEGACY_ANALYSIS_MODES else "normal_extract"

    def has_legacy_translation_references(self):
        for item in self.config.get("prompt_dictionary_data", []) or []:
            if self._is_legacy_reference_item(item):
                return True
        for item in self.config.get("characterization_data", []) or []:
            if self._is_legacy_reference_item(item):
                return True
        for history_key in ("world_building_history", "writing_style_history"):
            for entry in self.config.get(history_key, []) or []:
                if self._is_legacy_reference_item(entry):
                    return True
        return False

    def scan_is_target_language(self, scan_result, threshold=0.65):
        profile = (scan_result or {}).get("language_profile") or {}
        target_codes = self._target_language_codes()
        if not target_codes:
            return False
        total = max(int(profile.get("total") or 0), 0)
        if total <= 0:
            return False
        target_count = sum(
            int(count or 0)
            for lang, count in (profile.get("counts") or {}).items()
            if self._normalize_language_code(lang) in target_codes
        )
        return (target_count / total) >= threshold

    def _build_analysis_language_profile(self, items):
        counts = {}
        confidence = {}
        total = 0
        for item in items or []:
            lang_code = getattr(item, "lang_code", None)
            if not lang_code or len(lang_code) < 1:
                continue
            lang = self._normalize_language_code(lang_code[0])
            if not lang or lang == "un":
                continue
            total += 1
            counts[lang] = counts.get(lang, 0) + 1
            try:
                confidence[lang] = confidence.get(lang, 0.0) + float(lang_code[1])
            except (TypeError, ValueError, IndexError):
                pass
        ranked = sorted(
            (
                {
                    "lang": lang,
                    "count": count,
                    "ratio": count / total if total else 0.0,
                    "avg_confidence": confidence.get(lang, 0.0) / count if count else 0.0,
                }
                for lang, count in counts.items()
            ),
            key=lambda row: (-row["count"], -row["avg_confidence"], row["lang"]),
        )
        return {"total": total, "counts": counts, "ranked": ranked}

    def _target_language_codes(self):
        from ModuleFolders.Service.TaskExecutor.TranslatorUtil import map_language_name_to_code

        target = self._normalize_glossary_text(self.config.get("target_language"))
        if not target:
            return set()
        candidates = {target, map_language_name_to_code(target)}
        normalized = {self._normalize_language_code(item) for item in candidates if item}
        if "zh" in normalized or "zh-hans" in normalized:
            normalized.update({"zh", "zh-cn", "zh-hans"})
        if "zh-hant" in normalized or "zh-tw" in normalized or "yue" in normalized:
            normalized.update({"zh-hant", "zh-tw", "yue"})
        return normalized

    def _normalize_language_code(self, value):
        text = self._normalize_glossary_text(value).replace("_", "-").lower()
        aliases = {
            "chinese": "zh",
            "chinese-simplified": "zh",
            "simplified-chinese": "zh",
            "chinese-traditional": "zh-hant",
            "traditional-chinese": "zh-hant",
            "japanese": "ja",
            "english": "en",
            "korean": "ko",
            "russian": "ru",
            "spanish": "es",
            "french": "fr",
            "german": "de",
        }
        return aliases.get(text, text)

    def prepare_analysis_scan(self, input_path, analysis_percent=100, analysis_lines=None):
        console.print(f"[cyan]{self.i18n.get('msg_reading_file') or '正在读取文件...'}[/cyan]")

        project_type = self.config.get("translation_project", "auto")
        cache_data = self.file_reader.read_files(project_type, input_path, "")

        if not cache_data:
            console.print(f"[red]{self.i18n.get('msg_no_content') or '无法读取文件内容'}[/red]")
            return None

        all_items = list(cache_data.items_iter())
        total_lines = len(all_items)

        if total_lines == 0:
            console.print(f"[red]{self.i18n.get('msg_no_text_found') or '未找到可分析的文本'}[/red]")
            return None

        if analysis_lines:
            lines_to_analyze = min(analysis_lines, total_lines)
        else:
            lines_to_analyze = int(total_lines * analysis_percent / 100)

        lines_to_analyze = max(1, lines_to_analyze)
        items_to_analyze = all_items[:lines_to_analyze]
        selected_text = "\n".join([item.source_text for item in items_to_analyze])
        estimated_tokens = self._estimate_token_count(selected_text)
        language_profile = self._build_analysis_language_profile(items_to_analyze)

        return {
            "all_items": all_items,
            "total_lines": total_lines,
            "lines_to_analyze": lines_to_analyze,
            "items_to_analyze": items_to_analyze,
            "selected_text": selected_text,
            "estimated_tokens": estimated_tokens,
            "language_profile": language_profile,
        }

    def execute_analysis(
        self,
        input_path,
        analysis_percent,
        analysis_lines,
        temp_config=None,
        analysis_mode="full",
        prompt_file=None,
        translate_during_analysis=False,
        new=False,
        replace=False,
        source_label=None,
        source_volume=None,
        existing_rules_context=None,
        output_dir=None,
        incremental_split_target_tokens=None,
        legacy_mode="normal_extract",
    ):
        """
        执行术语表分析的核心逻辑

        Args:
            input_path: 输入文件路径
            analysis_percent: 分析百分比
            analysis_lines: 分析行数（优先于百分比）
            temp_config: 临时API配置（可选）
            analysis_mode: full=全本/按比例单次提取，split=按行拆分提取
            prompt_file: 自定义术语分析提示词路径（可选）
            translate_during_analysis: 分析时让 LLM 同时输出目标语言译名和注释
            new: 增量模式下允许新增当前文本中出现的新术语/规则
            replace: 增量模式下允许补全或替换现有术语/角色/规则描述
            source_label: 本次提取来源标签，例如 第2卷
            source_volume: 本次提取卷号，用于动态术语表时间线

        Returns:
            tuple: (filtered_terms, glossary_data) 或 None（如果失败）
        """
        from ModuleFolders.Infrastructure.LLMRequester.LLMRequester import LLMRequester
        from ModuleFolders.Infrastructure.TaskConfig.TaskConfig import TaskConfig
        from ModuleFolders.Infrastructure.TaskConfig.TaskType import TaskType

        scan_result = self.prepare_analysis_scan(input_path, analysis_percent, analysis_lines)
        if scan_result is None:
            return None

        total_lines = scan_result["total_lines"]
        lines_to_analyze = scan_result["lines_to_analyze"]
        items_to_analyze = scan_result["items_to_analyze"]
        selected_text = scan_result["selected_text"]
        estimated_tokens = scan_result["estimated_tokens"]
        normalized_mode = self._normalize_analysis_mode(analysis_mode)
        legacy_mode = self.normalize_legacy_mode(legacy_mode)

        console.print(f"[green]{self.i18n.get('msg_total_lines') or '总行数'}: {total_lines}[/green]")
        console.print(f"[green]{self.i18n.get('msg_lines_to_analyze') or '将分析行数'}: {lines_to_analyze}[/green]")
        console.print(
            f"[green]{self.i18n.get('msg_estimated_tokens') or '预估Token'}: "
            f"{estimated_tokens:,}[/green]"
        )
        console.print(
            f"[dim]{self.i18n.get('msg_token_reference_note') or 'Token仅用于参考；实际范围仍按行数/比例截取。'}[/dim]"
        )

        if normalized_mode == "full":
            console.print(
                f"[cyan]{self.i18n.get('msg_single_request_analysis') or '全本/按比例提取：将所选文本一次性发送给LLM。'}[/cyan]"
            )
        elif normalized_mode == "incremental_split":
            console.print(
                f"[yellow]{self._tr('msg_incremental_split_request_analysis', '超长增量分批：将所选文本顺序拆分，每批只输出新增或变化，并逐批合并。')}[/yellow]"
            )
        else:
            console.print(
                f"[yellow]{self.i18n.get('msg_split_request_analysis') or '拆分提取：将所选文本按行数拆分成多个批次。'}[/yellow]"
            )

        # 准备提示词
        prompt_path = self._resolve_prompt_file(prompt_file)
        console.print(
            f"[cyan]{self.i18n.get('msg_selected_prompt') or '已选提示词'}: "
            f"{prompt_path}[/cyan]"
        )

        with open(prompt_path, 'r', encoding='utf-8') as f:
            system_prompt = f.read()

        incremental_options = self._build_incremental_options(
            new=new,
            replace=replace,
            source_label=source_label,
            source_volume=source_volume,
        )

        # 配置请求
        task_config = TaskConfig()
        task_config.load_config_from_dict(self.config)
        task_config.prepare_for_translation(TaskType.TRANSLATION)

        # 使用临时配置或当前配置
        if temp_config:
            platform_config = temp_config
            console.print(f"[cyan]{self.i18n.get('msg_using_temp_config') or '使用临时API配置'}: {temp_config.get('target_platform')}[/cyan]")
        else:
            platform_config = task_config.get_platform_configuration("translationReq")
            console.print(f"[cyan]{self.i18n.get('msg_using_current_config') or '使用当前配置'}: {platform_config.get('target_platform')}[/cyan]")

        target_language = getattr(task_config, "target_language", self.config.get("target_language", "Chinese"))
        if translate_during_analysis:
            system_prompt = self._append_analysis_translation_instruction(system_prompt, target_language)
            console.print(
                f"[cyan]{self._tr('msg_glossary_analysis_translate_enabled', '已启用分析阶段直译：LLM 将同时输出译名和注释。')}[/cyan]"
            )

        if incremental_options.get("enabled"):
            existing_context = existing_rules_context or self._build_incremental_existing_rules_context()
            system_prompt = self._append_incremental_analysis_instruction(
                system_prompt,
                existing_context,
                incremental_options,
            )
            mode_bits = []
            if incremental_options.get("new"):
                mode_bits.append("new")
            if incremental_options.get("replace"):
                mode_bits.append("replace")
            console.print(
                f"[cyan]{self._tr('msg_glossary_incremental_enabled', '已启用增量术语提取')}: "
                f"{'/'.join(mode_bits) or 'metadata'} | {incremental_options.get('source_label') or '-'}[/cyan]"
            )

        if legacy_mode == "legacy_translation_extract":
            system_prompt = self._append_legacy_translation_extract_instruction(system_prompt, target_language)
            console.print(
                f"[cyan]{self._tr('msg_legacy_translation_extract_enabled')}[/cyan]"
            )
        elif legacy_mode == "legacy_mapping_extract":
            legacy_context = self._build_legacy_mapping_context()
            system_prompt = self._append_legacy_mapping_instruction(system_prompt, legacy_context, target_language)
            console.print(
                f"[cyan]{self._tr('msg_legacy_mapping_enabled')}[/cyan]"
            )

        base_system_prompt = system_prompt
        all_terms = []
        structured_analysis = self._empty_analysis_payload()
        raw_response_diagnostics = []
        completed_count = 0
        error_count = 0

        if normalized_mode == "full":
            messages = [{"role": "user", "content": selected_text}]
            try:
                requester = LLMRequester()
                skip, _, response, prompt_tokens, completion_tokens = requester.sent_request(
                    messages, system_prompt, platform_config
                )
                if not skip and response:
                    parsed = self._mark_legacy_payload(self._parse_glossary_response(response), legacy_mode)
                    terms = parsed.get("terms", [])
                    all_terms.extend(terms)
                    self._merge_analysis_payload(structured_analysis, parsed)
                    if not terms and not self._has_non_glossary_analysis(parsed):
                        raw_response_diagnostics.append(
                            self._build_raw_response_diagnostic(
                                "full",
                                response,
                                prompt_tokens,
                                completion_tokens,
                            )
                        )
                    completed_count = 1
                    console.print(
                        f"[green]√ {self._tr('msg_analysis_complete', '分析完成!')} "
                        f"| {self._tr('msg_found_terms', '发现专有名词')} {len(terms)} "
                        f"| {prompt_tokens}+{completion_tokens}T[/green]"
                    )
                else:
                    error_count = 1
                    console.print(f"[red]✗ {self.i18n.get('msg_analysis_error') or '分析出错'}[/red]")
            except Exception as e:
                error_count = 1
                console.print(f"[red]✗ {self.i18n.get('msg_analysis_error') or '分析出错'}: {e}[/red]")
        elif normalized_mode == "split":
            batch_size = self._get_split_batch_size()
            batches = [items_to_analyze[i:i+batch_size] for i in range(0, len(items_to_analyze), batch_size)]

            console.print(f"[cyan]{self.i18n.get('msg_batch_count') or '批次数量'}: {len(batches)}[/cyan]")

            # 获取用户配置的线程数 (临时配置优先)
            if temp_config and temp_config.get("thread_counts"):
                thread_count = temp_config.get("thread_counts")
            else:
                thread_count = task_config.actual_thread_counts
            console.print(f"[cyan]{self.i18n.get('msg_thread_count') or '并发线程数'}: {thread_count}[/cyan]")

            # 收集所有结果 (线程安全)
            terms_lock = threading.Lock()
            completed_counter = [0]  # 使用列表以便在闭包中修改
            failed_batches = []
            failed_lock = threading.Lock()

            def analyze_batch(batch_info, is_last_round=False):
                """单个批次的分析任务"""
                batch_idx, batch = batch_info
                text_content = "\n".join([item.source_text for item in batch])
                messages = [{"role": "user", "content": text_content}]

                try:
                    requester = LLMRequester()
                    skip, _, response, prompt_tokens, completion_tokens = requester.sent_request(
                        messages, system_prompt, platform_config
                    )

                    if not skip and response:
                        parsed = self._mark_legacy_payload(self._parse_glossary_response(response), legacy_mode)
                        terms = parsed.get("terms", [])
                        with terms_lock:
                            all_terms.extend(terms)
                            self._merge_analysis_payload(structured_analysis, parsed)
                            if not terms and not self._has_non_glossary_analysis(parsed):
                                raw_response_diagnostics.append(
                                    self._build_raw_response_diagnostic(
                                        batch_idx + 1,
                                        response,
                                        prompt_tokens,
                                        completion_tokens,
                                    )
                                )
                            completed_counter[0] += 1
                        console.print(
                            f"[green]√ [{batch_idx+1:03d}] "
                            f"{self._tr('glossary_log_batch_completed', '完成')} | "
                            f"{self._tr('msg_found_terms', '发现专有名词')} {len(terms)} | "
                            f"{prompt_tokens}+{completion_tokens}T[/green]"
                        )
                        return
                    else:
                        with failed_lock:
                            failed_batches.append(batch_info)
                        hint = self._tr('glossary_log_retry_suffix', '，将在下一轮重试') if not is_last_round else ""
                        console.print(f"[red]✗ [{batch_idx+1:03d}] {self._tr('glossary_log_batch_failed', '失败')}{hint}[/red]")
                except Exception as e:
                    with failed_lock:
                        failed_batches.append(batch_info)
                    hint = self._tr('glossary_log_retry_suffix', '，将在下一轮重试') if not is_last_round else ""
                    console.print(f"[red]✗ [{batch_idx+1:03d}] {self._tr('glossary_log_error', '错误')}: {e}{hint}[/red]")

            # 使用线程池并发执行
            console.print(f"\n[bold cyan]{self.i18n.get('msg_starting_concurrent') or '开始并发分析...'}[/bold cyan]\n")

            max_rounds = 3
            batch_infos = list(enumerate(batches))

            for round_num in range(max_rounds):
                is_last = (round_num == max_rounds - 1)
                if round_num > 0:
                    batch_infos = failed_batches[:]
                    failed_batches.clear()
                    console.print(
                        f"\n[yellow]⟳ "
                        f"{self._tr('glossary_log_retry_round_remaining', '第{}轮重试，剩余 {} 个失败批次...', round_num + 1, len(batch_infos))}"
                        f"[/yellow]\n"
                    )

                with concurrent.futures.ThreadPoolExecutor(max_workers=thread_count) as executor:
                    list(executor.map(lambda b: analyze_batch(b, is_last), batch_infos))

                if not failed_batches:
                    break

            completed_count = completed_counter[0]
            error_count = len(failed_batches)
            console.print(
                f"\n[cyan]{self._tr('glossary_log_batch_summary', '完成: {}/{}, 失败: {}', completed_count, len(batches), error_count)}[/cyan]"
            )
        else:
            target_tokens = self._resolve_incremental_split_target_tokens(incremental_split_target_tokens)
            batches = self._split_items_by_estimated_tokens(items_to_analyze, target_tokens)
            console.print(
                f"[cyan]{self.i18n.get('msg_batch_count') or '批次数量'}: {len(batches)} "
                f"| {self._tr('msg_incremental_split_target_tokens', '目标每批约 {} Token', target_tokens)}[/cyan]"
            )
            console.print(
                f"[dim]{self._tr('msg_incremental_split_sequential_note', '该模式会顺序执行；后一批会参考此前批次累计出的术语/角色/世界观快照，只在原始文本条目或句末边界分批，不会截断句子。每批目标可自定，但最大不会超过 256000 Token；也不会写入 Vol_2/Vol_3 这类卷号历史。')}[/dim]"
            )

            for batch_idx, batch in enumerate(batches):
                text_content = "\n".join([item.source_text for item in batch])
                batch_context = self._build_incremental_split_context(structured_analysis, all_terms)
                batch_system_prompt = self._append_incremental_split_instruction(
                    base_system_prompt,
                    batch_context,
                    batch_idx,
                    len(batches),
                )
                messages = [{"role": "user", "content": text_content}]
                try:
                    requester = LLMRequester()
                    skip, _, response, prompt_tokens, completion_tokens = requester.sent_request(
                        messages, batch_system_prompt, platform_config
                    )
                    if not skip and response:
                        parsed = self._mark_legacy_payload(self._parse_glossary_response(response), legacy_mode)
                        terms = parsed.get("terms", [])
                        all_terms.extend(terms)
                        self._merge_analysis_payload(
                            structured_analysis,
                            parsed,
                            fill_existing=True,
                            replace_existing=True,
                        )
                        if not terms and not self._has_non_glossary_analysis(parsed):
                            raw_response_diagnostics.append(
                                self._build_raw_response_diagnostic(
                                    batch_idx + 1,
                                    response,
                                    prompt_tokens,
                                    completion_tokens,
                                )
                            )
                        completed_count += 1
                        console.print(
                            f"[green]√ [{batch_idx+1:03d}/{len(batches):03d}] "
                            f"{self._tr('glossary_log_batch_completed', '完成')} | "
                            f"{self._tr('msg_found_terms', '发现专有名词')} {len(terms)} | "
                            f"{prompt_tokens}+{completion_tokens}T[/green]"
                        )
                    else:
                        error_count += 1
                        console.print(f"[red]✗ [{batch_idx+1:03d}] {self._tr('glossary_log_batch_failed', '失败')}[/red]")
                except Exception as e:
                    error_count += 1
                    console.print(f"[red]✗ [{batch_idx+1:03d}] {self._tr('glossary_log_error', '错误')}: {e}[/red]")

            console.print(
                f"\n[cyan]{self._tr('glossary_log_batch_summary', '完成: {}/{}, 失败: {}', completed_count, len(batches), error_count)}[/cyan]"
            )

        if normalized_mode == "incremental_split":
            all_terms = self._dedupe_terms_for_context(all_terms)

        structured_analysis = self._mark_legacy_payload(
            self._finalize_analysis_payload(structured_analysis, all_terms),
            legacy_mode,
        )

        # 统计词频
        term_freq = self._calculate_term_frequency(all_terms, selected_text)

        if not term_freq:
            if not self._has_non_glossary_analysis(structured_analysis):
                console.print(f"[yellow]{self.i18n.get('msg_no_terms_found') or '未找到专有名词'}[/yellow]")
                diagnostic_path = self._save_raw_analysis_response_log(input_path, raw_response_diagnostics, output_dir=output_dir)
                if diagnostic_path:
                    console.print(
                        f"[yellow]{self._tr('msg_glossary_parse_empty_with_response', '模型返回了内容，但未解析到术语/分类 JSON；已保存原始响应诊断: {}', diagnostic_path)}[/yellow]"
                    )
                return None
            console.print(f"[yellow]{self.i18n.get('msg_no_terms_found') or '未找到专有名词'}[/yellow]")
            console.print(f"[cyan]{self._tr('msg_structured_analysis_found', '已提取到角色/世界观/禁翻表等分类设定，将继续保存分类结果。')}[/cyan]")

        # 返回结果供菜单层处理
        return {
            'term_freq': term_freq,
            'input_path': input_path,
            'analysis_percent': analysis_percent,
            'analysis_lines': analysis_lines,
            'analysis_mode': normalized_mode,
            'estimated_tokens': estimated_tokens,
            'prompt_file': prompt_path,
            'structured_analysis': structured_analysis,
            'translate_during_analysis': translate_during_analysis,
            'incremental_options': incremental_options,
            'legacy_mode': legacy_mode,
            'raw_response_diagnostics': raw_response_diagnostics,
            'output_dir': output_dir,
        }

    def filter_and_save(self, analysis_result, min_freq, output_dir=None):
        """
        过滤低频词并保存结果

        Args:
            analysis_result: execute_analysis 返回的结果
            min_freq: 最低词频阈值

        Returns:
            tuple: (filtered_terms, glossary_data, glossary_path)
        """
        term_freq = analysis_result['term_freq']
        input_path = analysis_result['input_path']
        analysis_percent = analysis_result['analysis_percent']
        analysis_lines = analysis_result['analysis_lines']
        analysis_mode = analysis_result.get('analysis_mode', 'full')
        estimated_tokens = analysis_result.get('estimated_tokens', 0)
        prompt_file = analysis_result.get('prompt_file', '')
        structured_analysis = analysis_result.get('structured_analysis') or self._empty_analysis_payload()
        incremental_options = analysis_result.get('incremental_options') or {}
        legacy_mode = self.normalize_legacy_mode(analysis_result.get('legacy_mode'))
        raw_response_diagnostics = analysis_result.get('raw_response_diagnostics') or []
        output_dir = output_dir or analysis_result.get("output_dir") or None

        # 过滤低频词
        filtered_terms = {k: v for k, v in term_freq.items() if v['count'] >= min_freq}

        console.print(f"[green]{self.i18n.get('msg_before_filter') or '过滤前'}: {len(term_freq)}[/green]")
        console.print(f"[green]{self.i18n.get('msg_after_filter') or '过滤后'}: {len(filtered_terms)}[/green]")

        if not filtered_terms and not self._has_non_glossary_analysis(structured_analysis):
            console.print(f"[yellow]{self.i18n.get('msg_no_terms_after_filter') or '过滤后无剩余词条'}[/yellow]")
            diagnostic_path = self._save_raw_analysis_response_log(input_path, raw_response_diagnostics, output_dir=output_dir)
            if diagnostic_path:
                console.print(
                    f"[yellow]{self._tr('msg_glossary_parse_empty_with_response', '模型返回了内容，但未解析到术语/分类 JSON；已保存原始响应诊断: {}', diagnostic_path)}[/yellow]"
                )
            return None

        # 生成术语表文件
        input_basename = os.path.splitext(os.path.basename(input_path))[0]
        input_dir = output_dir or os.path.dirname(input_path) or "."
        os.makedirs(input_dir, exist_ok=True)

        glossary_path = os.path.join(input_dir, f"{input_basename}_自动术语.json")
        structured_path = os.path.join(input_dir, f"{input_basename}_分类规则配置.json")
        log_path = os.path.join(input_dir, f"{input_basename}_分析日志.txt")

        # 保存术语表
        glossary_data = self._generate_glossary_json(filtered_terms)
        if incremental_options.get("enabled"):
            glossary_data = [
                self._with_source_metadata(item, incremental_options.get("source_label"), incremental_options.get("source_volume"))
                for item in glossary_data
            ]
        if glossary_data:
            with open(glossary_path, 'w', encoding='utf-8') as f:
                json.dump(glossary_data, f, indent=2, ensure_ascii=False)

            console.print(f"[bold green]{self.i18n.get('msg_glossary_saved') or '术语表已保存'}: {glossary_path}[/bold green]")

        structured_rules = self._build_structured_rules_config(filtered_terms, structured_analysis)
        structured_rules = self._annotate_text_history_legacy(structured_rules, incremental_options, legacy_mode)
        if incremental_options.get("enabled"):
            self._annotate_structured_rules_source(structured_rules, incremental_options)
        if self._has_structured_rules(structured_rules):
            with open(structured_path, 'w', encoding='utf-8') as f:
                json.dump(structured_rules, f, indent=2, ensure_ascii=False)
            console.print(f"[bold green]{self._tr('msg_structured_rules_saved', '分类规则配置已保存: {}', structured_path)}[/bold green]")

        legacy_mapping_stats = {}
        if legacy_mode == "legacy_mapping_extract":
            legacy_mapping_stats = self.summarize_legacy_mapping_result(structured_rules, glossary_data)
            if legacy_mapping_stats.get("mapped_total", 0) <= 0:
                console.print(
                    "[yellow]"
                    + self._tr(
                        "msg_legacy_mapping_no_reliable_results",
                        "旧译文映射结果没有检测到可靠 mapped 条目（术语 {}，角色 {}）。本次结果更像普通抽取；导入当前配置前会要求再次确认。",
                        legacy_mapping_stats.get("glossary_total", 0),
                        legacy_mapping_stats.get("character_total", 0),
                    )
                    + "[/yellow]"
                )

        # 保存分析日志
        self._save_glossary_analysis_log(
            log_path, input_path, analysis_percent, analysis_lines,
            term_freq, filtered_terms, min_freq,
            analysis_mode=analysis_mode,
            estimated_tokens=estimated_tokens,
            prompt_file=prompt_file,
            structured_rules=structured_rules,
            incremental_options=incremental_options,
            legacy_mode=legacy_mode,
            raw_response_diagnostics=raw_response_diagnostics,
        )

        console.print(f"[green]{self.i18n.get('msg_log_saved') or '分析日志已保存'}: {log_path}[/green]")

        return {
            'filtered_terms': filtered_terms,
            'glossary_data': glossary_data,
            'glossary_path': glossary_path,
            'structured_rules': structured_rules,
            'structured_path': structured_path,
            'incremental_options': incremental_options,
            'legacy_mode': legacy_mode,
            'legacy_mapping_stats': legacy_mapping_stats,
        }

    def save_glossary_directly(self, glossary_data, save_mode="import", base_glossary_path=None, merge_options=None):
        """直接保存术语表（无翻译）"""
        options = merge_options or {}
        incremental_enabled = bool(options.get("enabled"))
        allow_new = bool(options.get("new")) if incremental_enabled else True
        allow_replace = bool(options.get("replace"))
        source_label = self._normalize_glossary_text(options.get("source_label"))
        source_volume = self._normalize_volume_number(options.get("source_volume"))
        track_history = incremental_enabled and source_volume is not None

        if save_mode in ("import", "both"):
            existing_data = self.config.get("prompt_dictionary_data", [])
            existing_by_src = {
                self._normalize_glossary_text(item.get("src")): item
                for item in existing_data
                if isinstance(item, dict) and self._normalize_glossary_text(item.get("src"))
            }
            for item in glossary_data:
                if not isinstance(item, dict) or not item.get("src"):
                    continue
                prepared = self._with_source_metadata(item, source_label, source_volume)
                src = self._normalize_glossary_text(prepared.get("src"))
                existing = existing_by_src.get(src)
                if existing:
                    if allow_replace:
                        self._merge_timeline_item(
                            existing,
                            prepared,
                            source_label,
                            source_volume,
                            GLOSSARY_TIMELINE_FIELDS,
                            key_field="src",
                            track_history=track_history,
                        )
                    continue
                if not allow_new:
                    continue
                existing_data.append(prepared)
                existing_by_src[src] = prepared
                if track_history:
                    self._ensure_timeline_history(prepared, source_label, source_volume, GLOSSARY_TIMELINE_FIELDS, key_field="src")
            self.config["prompt_dictionary_data"] = existing_data
            self.config["prompt_dictionary_switch"] = True
            self.save_config()
            console.print(f"[bold green]{self.i18n.get('msg_glossary_imported') or '术语表已导入!'}[/bold green]")

        if save_mode in ("standalone", "both"):
            save_path = self._build_output_glossary_path(base_glossary_path, "_独立术语表")
            self._save_glossary_json_to_path(glossary_data, save_path)
            console.print(f"[bold green]{self.i18n.get('msg_glossary_saved') or '术语表已保存'}: {save_path}[/bold green]")

    def save_structured_rules_directly(
        self,
        structured_rules,
        save_mode="import",
        base_glossary_path=None,
        merge_options=None,
    ):
        """保存分类规则配置：术语表、禁翻表、角色设定、世界观、文风和翻译示例。"""
        if not structured_rules:
            console.print(f"[yellow]{self._tr('msg_structured_rules_empty', '没有可保存的分类规则。')}[/yellow]")
            return None

        summary = {}

        if save_mode in ("import", "both"):
            summary = self._merge_structured_rules_into_config(structured_rules, merge_options=merge_options)
            self.save_config()
            world_status = self._tr("label_updated", "已更新") if summary.get('world_building_content', 0) else self._tr("label_none", "无")
            style_status = self._tr("label_updated", "已更新") if summary.get('writing_style_content', 0) else self._tr("label_none", "无")
            console.print(
                "[bold green]"
                + self._tr(
                    "msg_structured_rules_imported",
                    "分类规则已导入当前配置: 术语 {}，禁翻 {}，角色 {}，世界观 {}，文风 {}",
                    summary.get('prompt_dictionary_data', 0),
                    summary.get('exclusion_list_data', 0),
                    summary.get('characterization_data', 0),
                    world_status,
                    style_status,
                )
                + "[/bold green]"
            )

        save_path = None
        if save_mode in ("standalone", "both"):
            save_path = self._build_output_glossary_path(base_glossary_path, "_分类规则配置")
            self._save_glossary_json_to_path(structured_rules, save_path)
            console.print(f"[bold green]{self._tr('msg_structured_rules_saved', '分类规则配置已保存: {}', save_path)}[/bold green]")

        return {"summary": summary, "path": save_path}

    def create_rules_profile_from_analysis(self, profile_name, structured_rules):
        """把本次分析结果保存为新的 rules profile，并切换到该 profile。"""
        profile_name = self._sanitize_rules_profile_name(profile_name)
        if not profile_name:
            raise ValueError(self._tr("msg_rules_profile_name_required", "规则配置名不能为空"))

        if profile_name == "None":
            raise ValueError(self._tr("msg_rules_profile_reserved", "规则配置名不能使用保留名称 None"))

        os.makedirs(self.cli.rules_profiles_dir, exist_ok=True)
        profile_path, profile_name = resolve_profile_path(self.cli.rules_profiles_dir, profile_name)
        if os.path.exists(profile_path):
            raise FileExistsError(self._tr("msg_rules_profile_exists", "规则配置已存在: {}", profile_name))

        rules_payload = {
            key: structured_rules.get(key, [] if key.endswith("_data") or key.endswith("_history") else "")
            for key in STRUCTURED_RULE_KEYS
        }
        rules_payload = normalize_rules_payload(rules_payload)
        atomic_write_json(profile_path, rules_payload)

        self.cli.active_rules_profile_name = profile_name
        self.cli.root_config["active_rules_profile"] = profile_name
        save_root_config(self.cli.root_config)
        self.cli.load_config()

        console.print(f"[bold green]{self._tr('msg_rules_profile_created_selected', '已新建并切换到规则配置: {}', profile_name)}[/bold green]")
        console.print(f"[green]{self._tr('msg_rules_profile_file', '配置文件: {}', profile_path)}[/green]")
        return {"profile": profile_name, "path": profile_path}

    def summarize_legacy_mapping_result(self, structured_rules=None, glossary_data=None):
        structured_rules = structured_rules if isinstance(structured_rules, dict) else {}
        glossary_items = []
        if isinstance(glossary_data, list):
            glossary_items.extend(item for item in glossary_data if isinstance(item, dict))
        glossary_items.extend(
            item for item in structured_rules.get("prompt_dictionary_data", []) or []
            if isinstance(item, dict)
        )
        character_items = [
            item for item in structured_rules.get("characterization_data", []) or []
            if isinstance(item, dict)
        ]

        glossary_items = self._dedupe_mapping_summary_items(glossary_items, "src")
        character_items = self._dedupe_mapping_summary_items(character_items, "original_name")
        mapped_glossary = [
            item for item in glossary_items
            if self._is_reliable_legacy_mapping_result(item, "glossary")
        ]
        mapped_characters = [
            item for item in character_items
            if self._is_reliable_legacy_mapping_result(item, "character")
        ]
        return {
            "glossary_total": len(glossary_items),
            "character_total": len(character_items),
            "mapped_glossary": len(mapped_glossary),
            "mapped_characters": len(mapped_characters),
            "mapped_total": len(mapped_glossary) + len(mapped_characters),
        }

    def _dedupe_mapping_summary_items(self, items, key_field):
        result = []
        seen = set()
        for item in items or []:
            if not isinstance(item, dict):
                continue
            marker = (
                self._normalize_glossary_text(item.get(key_field)),
                self._normalize_glossary_text(item.get("source")),
                self._normalize_volume_number(item.get("volume")),
            )
            if marker in seen:
                continue
            seen.add(marker)
            result.append(item)
        return result

    def _sanitize_rules_profile_name(self, profile_name):
        try:
            return sanitize_profile_name(profile_name, allow_none=True)
        except ValueError:
            return ""

    def _empty_analysis_payload(self):
        return {
            "terms": [],
            "exclusion_list_data": [],
            "characterization_data": [],
            "world_building_content": "",
            "writing_style_content": "",
            "translation_example_data": [],
        }

    def _normalize_analysis_mode(self, analysis_mode):
        mode = self._normalize_glossary_text(analysis_mode).lower()
        return mode if mode in GLOSSARY_ANALYSIS_MODES else "full"

    def _build_incremental_options(self, new=False, replace=False, source_label=None, source_volume=None):
        label = self._normalize_glossary_text(source_label)
        volume = self._normalize_volume_number(source_volume)
        if not label and volume is not None:
            label = self._format_volume_label(volume)

        enabled = bool(new or replace)
        return {
            "enabled": enabled,
            "new": bool(new),
            "replace": bool(replace),
            "source_label": label,
            "source_volume": volume,
        }

    def _append_incremental_analysis_instruction(self, system_prompt, existing_context, options):
        allow_new = bool(options.get("new"))
        allow_replace = bool(options.get("replace"))
        source_label = options.get("source_label") or "Current_Volume"
        source_volume = options.get("source_volume")
        volume_text = source_volume if source_volume is not None else "unknown"
        new_rule = "允许输出当前文本中确有必要加入的新术语、角色、设定和示例。" if allow_new else "禁止输出当前规则中不存在的新术语、角色、设定和示例。"
        replace_rule = (
            "允许输出对既有术语、角色、世界观、文风在当前卷视角下的补全或修正；"
            "这只会写入当前卷历史版本，不代表全局覆盖旧卷。"
            if allow_replace
            else "禁止改写既有术语、角色、世界观、文风描述。"
        )
        delta_rule = (
            "输出必须是增量 JSON：只包含新增项或当前卷有证据需要补全/修正的当前卷版本，不要把旧规则原样全量复制出来。"
            "不要输出删除指令，不要要求删除旧卷视角；旧卷术语和角色描述必须保留给旧卷翻译使用。"
            "如果没有必要新增或替换，对应字段返回空数组或空字符串。"
        )
        metadata_rule = (
            f"本次来源标签为 {source_label}，卷号为 {volume_text}。"
            "replace 只表示为当前卷写入新的历史版本，不是全局覆盖。"
            "你可以在条目中保留 source、volume、updated_in、updated_volume、history 等字段，但不要改变基础 JSON 字段名。"
        )
        linguistic_rule = (
            "角色 characterization 额外支持三个字段：aliases、pronouns 和 speech_quirks。"
            "aliases 用来记录源文中实际出现过、指向该角色的短称、昵称、敬称称呼或别名，例如 マヒル、マヒルさん；不要填译名，不要编造未出现的称呼。"
            "pronouns 用来记录第一人称/第二人称/称呼体系，例如 私/俺/僕/あなた/君。"
            "speech_quirks 用来记录口癖、语尾、固定句式、敬语习惯、粗口习惯等。"
            "如果角色在本卷发生伪装、暴露、立场反转或语气变化，请把这种变化写进对应卷的角色说明和语言特征。"
        )
        context_json = json.dumps(existing_context, ensure_ascii=False, indent=2)
        instruction = f"""

## 增量术语表叠加模式
你正在对同一系列的后续文本做术语/规则增量维护。请先参考“现有规则快照”，再阅读用户提供的新文本。

- {new_rule}
- {replace_rule}
- {delta_rule}
- {metadata_rule}
- 非必要不新增，非必要不修改；不要因为词频高就提取普通名词。
- 如果后续卷揭示角色反转或设定变化，不要删除早期卷视角；输出当前卷之后应使用的新描述即可，程序会记录历史版本。
- {linguistic_rule}

### 现有规则快照
{context_json}
"""
        return f"{system_prompt.rstrip()}\n{instruction.strip()}\n"

    def _append_incremental_split_instruction(self, system_prompt, accumulated_context, batch_index, total_batches):
        context_json = json.dumps(accumulated_context or {}, ensure_ascii=False, indent=2)
        instruction = f"""

## 超长文本增量分批模式
当前文本因预估 Token 过高被顺序拆分分析。你正在分析第 {batch_index + 1}/{total_batches} 批。

请遵守以下规则：
- 先阅读“已累计规则快照”，再阅读当前批文本。
- 当前批只输出新增项，或相对已累计快照有明确变化、补充、纠错价值的条目。
- 如果当前批没有明显新增、补充、纠错或状态变化，允许对应字段返回空数组或空字符串。
- 如果某个术语、角色、世界观或文风信息已经存在且当前批没有提供新证据，不要重复输出。
- 如果当前批有明确证据表明既有术语、角色、世界观、文风、译名或注释发生变化、应补全或应修正，必须输出更新后的条目；不要因为它已存在就省略。
- 输出更新时只写当前最新状态，程序会按当前最新结果合并。
- 这是同一文件内部的分批，不是系列卷号增量；不要输出 Vol_2、Vol_3、第2卷、第3卷、volume、updated_volume、history 等卷号/时间线标识，除非原文本身明确出现这些内容且它们是需要提取的术语。

### 已累计规则快照
{context_json}
"""
        return f"{system_prompt.rstrip()}\n{instruction.strip()}\n"

    def _build_incremental_split_context(self, structured_analysis, terms):
        context = dict(structured_analysis or self._empty_analysis_payload())
        context["terms"] = self._dedupe_terms_for_context(terms)
        context["prompt_dictionary_data"] = context["terms"]
        return {
            "prompt_dictionary_data": self._trim_existing_rule_value(context.get("prompt_dictionary_data", []), max_items=200, max_chars=12000),
            "exclusion_list_data": self._trim_existing_rule_value(context.get("exclusion_list_data", []), max_items=120, max_chars=8000),
            "characterization_data": self._trim_existing_rule_value(context.get("characterization_data", []), max_items=120, max_chars=12000),
            "world_building_content": self._trim_existing_rule_value(context.get("world_building_content", ""), max_items=0, max_chars=10000),
            "writing_style_content": self._trim_existing_rule_value(context.get("writing_style_content", ""), max_items=0, max_chars=6000),
            "translation_example_data": self._trim_existing_rule_value(context.get("translation_example_data", []), max_items=80, max_chars=8000),
        }

    def _dedupe_terms_for_context(self, terms):
        result = []
        by_src = {}
        for term in terms or []:
            if not isinstance(term, dict):
                continue
            src = self._normalize_glossary_text(term.get("src"))
            if not src:
                continue
            if src not in by_src:
                item = dict(term)
                by_src[src] = item
                result.append(item)
                continue
            existing = by_src[src]
            for key, value in term.items():
                if key == "src":
                    continue
                if self._rule_field_has_content(value):
                    existing[key] = value
        return result

    def _build_incremental_existing_rules_context(self):
        context = {}
        for key in STRUCTURED_RULE_KEYS:
            if key.endswith("_history"):
                continue
            value = self.config.get(key, [] if key.endswith("_data") else "")
            context[key] = self._trim_existing_rule_value(value)
        return context

    def _trim_existing_rule_value(self, value, max_items=300, max_chars=12000):
        if isinstance(value, dict):
            return {
                key: self._trim_existing_rule_value(item, max_items=max_items, max_chars=max_chars)
                for key, item in value.items()
            }
        if isinstance(value, list):
            trimmed = value[:max_items]
            if len(value) > max_items:
                trimmed = [*trimmed, {"_truncated": f"{len(value) - max_items} more items omitted"}]
            return trimmed
        text = self._normalize_glossary_text(value)
        if len(text) > max_chars:
            return text[:max_chars] + f"\n...({len(text) - max_chars} chars omitted)"
        return text

    def _merge_analysis_payload(self, target, source, fill_existing=False, replace_existing=False):
        if not source:
            return target

        target.setdefault("terms", []).extend(source.get("terms", []))
        self._extend_unique_dicts(
            target.setdefault("exclusion_list_data", []),
            source.get("exclusion_list_data", []),
            ("markers", "regex"),
        )
        self._merge_character_lists(
            target.setdefault("characterization_data", []),
            source.get("characterization_data", []),
            fill_existing=fill_existing,
            replace_existing=replace_existing,
        )
        self._extend_unique_dicts(
            target.setdefault("translation_example_data", []),
            source.get("translation_example_data", []),
            ("src", "dst"),
        )
        target["world_building_content"] = self._append_text_block(
            target.get("world_building_content", ""),
            source.get("world_building_content", ""),
        )
        target["writing_style_content"] = self._append_text_block(
            target.get("writing_style_content", ""),
            source.get("writing_style_content", ""),
        )
        return target

    def _finalize_analysis_payload(self, payload, terms):
        self._merge_character_lists(
            payload.setdefault("characterization_data", []),
            self._derive_characters_from_terms(terms),
        )
        if not self._normalize_glossary_text(payload.get("world_building_content")):
            payload["world_building_content"] = self._derive_world_building_from_terms(terms)
        return payload

    def _has_non_glossary_analysis(self, payload):
        if not payload:
            return False
        return any([
            bool(payload.get("exclusion_list_data")),
            bool(payload.get("characterization_data")),
            bool(self._normalize_glossary_text(payload.get("world_building_content"))),
            bool(self._normalize_glossary_text(payload.get("writing_style_content"))),
            bool(payload.get("translation_example_data")),
        ])

    def _has_structured_rules(self, structured_rules):
        if not structured_rules:
            return False
        return any([
            bool(structured_rules.get("prompt_dictionary_data")),
            bool(structured_rules.get("exclusion_list_data")),
            bool(structured_rules.get("characterization_data")),
            bool(self._normalize_glossary_text(structured_rules.get("world_building_content"))),
            bool(self._normalize_glossary_text(structured_rules.get("writing_style_content"))),
            bool(structured_rules.get("translation_example_data")),
        ])

    def _build_raw_response_diagnostic(self, batch, response, prompt_tokens=0, completion_tokens=0):
        return {
            "batch": batch,
            "prompt_tokens": prompt_tokens or 0,
            "completion_tokens": completion_tokens or 0,
            "preview": self._normalize_glossary_text(response)[:4000],
        }

    def _save_raw_analysis_response_log(self, input_path, diagnostics, output_dir=None):
        diagnostics = [item for item in diagnostics or [] if item and item.get("preview")]
        if not diagnostics:
            return ""

        input_basename = os.path.splitext(os.path.basename(input_path))[0]
        input_dir = output_dir or os.path.dirname(input_path) or "."
        os.makedirs(input_dir, exist_ok=True)
        diagnostic_path = os.path.join(input_dir, f"{input_basename}_分析原始响应.txt")
        lines = [
            "=== AI术语表分析原始响应诊断 ===",
            "说明: 模型返回了文本，但程序未能解析出术语表/角色/世界观等结构化内容。请检查输出是否为合法 JSON，或是否使用了程序暂不支持的字段结构。",
        ]
        for item in diagnostics:
            lines.extend([
                "",
                f"--- batch: {item.get('batch')} | tokens: {item.get('prompt_tokens', 0)}+{item.get('completion_tokens', 0)}T ---",
                str(item.get("preview") or ""),
            ])

        with open(diagnostic_path, "w", encoding="utf-8") as f:
            f.write("\n".join(lines) + "\n")
        return diagnostic_path

    def _build_structured_rules_config(self, filtered_terms, structured_analysis):
        return {
            "prompt_dictionary_data": self._generate_glossary_json(filtered_terms),
            "exclusion_list_data": structured_analysis.get("exclusion_list_data", []),
            "characterization_data": structured_analysis.get("characterization_data", []),
            "world_building_content": self._normalize_glossary_text(structured_analysis.get("world_building_content")),
            "writing_style_content": self._normalize_glossary_text(structured_analysis.get("writing_style_content")),
            "translation_example_data": structured_analysis.get("translation_example_data", []),
        }

    def _annotate_structured_rules_source(self, structured_rules, options):
        if not structured_rules:
            return structured_rules
        source_label = self._normalize_glossary_text(options.get("source_label"))
        source_volume = self._normalize_volume_number(options.get("source_volume"))
        structured_rules["prompt_dictionary_data"] = [
            self._with_source_metadata(item, source_label, source_volume)
            for item in structured_rules.get("prompt_dictionary_data", []) or []
            if isinstance(item, dict)
        ]
        structured_rules["characterization_data"] = [
            self._with_source_metadata(item, source_label, source_volume)
            for item in structured_rules.get("characterization_data", []) or []
            if isinstance(item, dict)
        ]
        return structured_rules

    def _annotate_text_history_legacy(self, structured_rules, options, mode):
        mode = self.normalize_legacy_mode(mode)
        if mode != "legacy_translation_extract" or not structured_rules:
            return structured_rules
        source_label = self._normalize_glossary_text(options.get("source_label"))
        source_volume = self._normalize_volume_number(options.get("source_volume"))
        if not source_label and source_volume is not None:
            source_label = self._format_volume_label(source_volume)
        for data_key, history_key in (
            ("world_building_content", "world_building_history"),
            ("writing_style_content", "writing_style_history"),
        ):
            content = self._normalize_glossary_text(structured_rules.get(data_key))
            if not content:
                continue
            entry = {
                "source": source_label or self._tr("label_legacy_translation_reference"),
                "content": content,
                "_glossary_origin": LEGACY_GLOSSARY_ORIGIN,
                "_mapping_status": "seed",
                "_legacy_id": self._make_legacy_id(history_key, {"src": content[:80]}, 1),
            }
            if source_volume is not None:
                entry["volume"] = source_volume
            history = structured_rules.get(history_key)
            if not isinstance(history, list):
                history = []
            history.append(entry)
            structured_rules[history_key] = history
        return structured_rules

    def _merge_structured_rules_into_config(self, structured_rules, merge_options=None):
        summary = {key: 0 for key in STRUCTURED_RULE_KEYS}
        options = merge_options or {}
        incremental_enabled = bool(options.get("enabled"))
        allow_new = bool(options.get("new")) if incremental_enabled else True
        allow_replace = bool(options.get("replace"))
        source_label = self._normalize_glossary_text(options.get("source_label"))
        source_volume = self._normalize_volume_number(options.get("source_volume"))
        track_history = incremental_enabled and source_volume is not None

        glossary_items = structured_rules.get("prompt_dictionary_data") or []
        existing_glossary = self.config.get("prompt_dictionary_data", [])
        existing_by_src = {
            self._normalize_glossary_text(item.get("src")): item
            for item in existing_glossary
            if isinstance(item, dict) and self._normalize_glossary_text(item.get("src"))
        }
        for item in glossary_items:
            if not isinstance(item, dict) or not item.get("src"):
                continue
            prepared = self._with_source_metadata(item, source_label, source_volume)
            src = self._normalize_glossary_text(prepared.get("src"))
            existing = existing_by_src.get(src)
            if existing:
                if allow_replace and self._merge_timeline_item(
                    existing,
                    prepared,
                    source_label,
                    source_volume,
                    GLOSSARY_TIMELINE_FIELDS,
                    key_field="src",
                    track_history=track_history,
                ):
                    summary["prompt_dictionary_data"] += 1
                continue
            if not allow_new:
                continue
            existing_glossary.append(prepared)
            existing_by_src[src] = prepared
            if track_history:
                self._ensure_timeline_history(prepared, source_label, source_volume, GLOSSARY_TIMELINE_FIELDS, key_field="src")
            summary["prompt_dictionary_data"] += 1
        if glossary_items:
            self.config["prompt_dictionary_data"] = existing_glossary
            self.config["prompt_dictionary_switch"] = True

        exclusion_items = structured_rules.get("exclusion_list_data") or []
        existing_exclusion = self.config.get("exclusion_list_data", [])
        existing_keys = {
            (item.get("markers", ""), item.get("regex", ""))
            for item in existing_exclusion
            if isinstance(item, dict)
        }
        for item in exclusion_items:
            if not isinstance(item, dict):
                continue
            key = (item.get("markers", ""), item.get("regex", ""))
            if not any(key) or key in existing_keys:
                continue
            if not allow_new:
                continue
            existing_exclusion.append(item)
            existing_keys.add(key)
            summary["exclusion_list_data"] += 1
        if exclusion_items:
            self.config["exclusion_list_data"] = existing_exclusion
            self.config["exclusion_list_switch"] = True

        character_items = structured_rules.get("characterization_data") or []
        existing_characters = self.config.get("characterization_data", [])
        existing_by_name = {
            self._normalize_glossary_text(item.get("original_name")): item
            for item in existing_characters
            if isinstance(item, dict) and self._normalize_glossary_text(item.get("original_name"))
        }
        for item in character_items:
            if not isinstance(item, dict):
                continue
            name = self._normalize_glossary_text(item.get("original_name"))
            if not name:
                continue
            prepared = self._with_source_metadata(item, source_label, source_volume)
            existing = existing_by_name.get(name)
            if existing:
                if incremental_enabled and not allow_replace:
                    continue
                if self._merge_character_item(
                    existing,
                    prepared,
                    source_label,
                    source_volume,
                    allow_replace=allow_replace,
                    track_history=track_history,
                ):
                    summary["characterization_data"] += 1
                continue
            if not allow_new:
                continue
            existing_characters.append(prepared)
            existing_by_name[name] = prepared
            if track_history:
                self._ensure_timeline_history(prepared, source_label, source_volume, CHARACTER_TIMELINE_FIELDS, key_field="original_name")
            summary["characterization_data"] += 1
        if character_items:
            self.config["characterization_data"] = existing_characters
            self.config["characterization_switch"] = True

        world_building = self._normalize_glossary_text(structured_rules.get("world_building_content"))
        if world_building and (allow_new or self._normalize_glossary_text(self.config.get("world_building_content"))):
            self.config["world_building_content"] = self._merge_text_rule_with_history(
                "world_building_content",
                "world_building_history",
                world_building,
                source_label,
                source_volume,
                replace=allow_replace,
                track_history=track_history,
            )
            self.config["world_building_switch"] = True
            summary["world_building_content"] = 1

        writing_style = self._normalize_glossary_text(structured_rules.get("writing_style_content"))
        if writing_style and (allow_new or self._normalize_glossary_text(self.config.get("writing_style_content"))):
            self.config["writing_style_content"] = self._merge_text_rule_with_history(
                "writing_style_content",
                "writing_style_history",
                writing_style,
                source_label,
                source_volume,
                replace=allow_replace,
                track_history=track_history,
            )
            self.config["writing_style_switch"] = True
            summary["writing_style_content"] = 1

        for history_key in ("world_building_history", "writing_style_history"):
            incoming_history = [
                item for item in structured_rules.get(history_key, []) or []
                if isinstance(item, dict)
            ]
            if not incoming_history:
                continue
            existing_history = self.config.get(history_key)
            if not isinstance(existing_history, list):
                existing_history = []
            existing_by_marker = {
                (
                    self._history_key(item),
                    self._normalize_glossary_text(item.get("content")),
                ): item
                for item in existing_history
                if isinstance(item, dict)
            }
            for item in incoming_history:
                marker = (
                    self._history_key(item),
                    self._normalize_glossary_text(item.get("content")),
                )
                existing = existing_by_marker.get(marker)
                if existing:
                    changed = False
                    for meta_key in LEGACY_METADATA_FIELDS:
                        value = item.get(meta_key)
                        if self._rule_field_has_content(value) and existing.get(meta_key) != value:
                            existing[meta_key] = value
                            changed = True
                    if changed:
                        summary[history_key] += 1
                    continue
                existing_history.append(item)
                existing_by_marker[marker] = item
                summary[history_key] += 1
            existing_history.sort(key=lambda entry: self._history_sort_key(entry))
            self.config[history_key] = existing_history

        example_items = structured_rules.get("translation_example_data") or []
        existing_examples = self.config.get("translation_example_data", [])
        example_keys = {
            (item.get("src", ""), item.get("dst", ""))
            for item in existing_examples
            if isinstance(item, dict)
        }
        for item in example_items:
            if not isinstance(item, dict) or not item.get("src") or not item.get("dst"):
                continue
            key = (item.get("src", ""), item.get("dst", ""))
            if key in example_keys:
                continue
            if not allow_new:
                continue
            existing_examples.append(item)
            example_keys.add(key)
            summary["translation_example_data"] += 1
        if example_items:
            self.config["translation_example_data"] = existing_examples
            self.config["translation_example_switch"] = True

        return summary

    def multi_translate_and_select(self, filtered_terms, temp_config=None, rounds=3, save_mode="import", base_glossary_path=None, merge_options=None):
        """
        多翻译选择功能

        Args:
            filtered_terms: 过滤后的术语字典
            temp_config: 临时API配置
            rounds: 翻译轮询次数
        """
        from ModuleFolders.UserInterface.TermSelector.TermSelector import TermSelector
        from ModuleFolders.Infrastructure.TaskConfig.TaskConfig import TaskConfig
        from ModuleFolders.Infrastructure.TaskConfig.TaskType import TaskType

        console.print(f"\n[cyan]{self.i18n.get('msg_starting_multi_translate') or '开始多翻译请求...'}[/cyan]")
        console.print(f"[dim]{self.i18n.get('msg_rounds')}: {rounds}[/dim]")

        # 准备配置
        task_config = TaskConfig()
        task_config.load_config_from_dict(self.config)
        task_config.prepare_for_translation(TaskType.TRANSLATION)

        if temp_config:
            platform_config = temp_config
        else:
            platform_config = task_config.get_platform_configuration("translationReq")

        target_language = task_config.target_language

        # 为每个术语请求多次翻译
        multi_results = []
        total = len(filtered_terms)

        for idx, (src, term_data) in enumerate(filtered_terms.items(), 1):
            console.print(f"[{idx}/{total}] {self.i18n.get('msg_translating') or '正在翻译'}: {src}")

            options = []
            seen = set()

            for r in range(rounds):
                result = self._request_term_translation(src, term_data, target_language, platform_config, seen)
                if result and result['dst'] not in seen:
                    seen.add(result['dst'])
                    options.append(result)

            if options:
                multi_results.append({
                    "src": src,
                    "type": term_data.get("type", ""),
                    "analysis_info": term_data.get("info", "null"),
                    "options": options,
                    "selected_index": 0
                })
            else:
                console.print(f"[red]✗ {src} {self.i18n.get('msg_term_all_failed')}[/red]")

        skipped = total - len(multi_results)
        if skipped > 0:
            console.print(f"\n[yellow]⚠ {skipped} {self.i18n.get('msg_term_skipped_count')}[/yellow]")

        if not multi_results:
            console.print(f"[yellow]{self.i18n.get('msg_no_translation_results') or '未获得翻译结果'}[/yellow]")
            fallback_glossary = self._generate_glossary_json(filtered_terms)
            fallback_path = self._build_output_glossary_path(base_glossary_path, "_翻译失败原文回退")
            self._save_glossary_json_to_path(fallback_glossary, fallback_path)
            console.print(f"[yellow]{self.i18n.get('msg_glossary_saved') or '术语表已保存'}: {fallback_path}[/yellow]")
            return

        # 显示选择界面
        console.print(f"\n[green]{self.i18n.get('msg_translation_complete') or '翻译完成，请选择最佳译法'}[/green]")

        # 定义单条保存回调
        def save_single_term(term_data):
            if save_mode not in ("import", "both"):
                return
            self.save_glossary_directly([term_data], save_mode="import", merge_options=merge_options)

        # 定义重试翻译回调
        def retry_translation(src, term_type, avoid_set=None):
            source = filtered_terms.get(src, {})
            term_data = {"type": term_type, "info": source.get("info", "null")}
            return self._request_term_translation(src, term_data, target_language, platform_config, avoid_set or set())

        selector = TermSelector(multi_results, request_callback=retry_translation, save_callback=save_single_term)
        selected_results = selector.show_selector()

        if not selected_results:
            console.print(f"[yellow]{self.i18n.get('msg_cancelled') or '已取消'}[/yellow]")
            return

        # 保存到术语表
        self._save_selected_translations(
            selected_results,
            filtered_terms,
            save_mode=save_mode,
            base_glossary_path=base_glossary_path,
            merge_options=merge_options,
        )

    def batch_translate_and_select(self, filtered_terms, temp_config=None, save_mode="import", base_glossary_path=None, merge_options=None):
        """批量翻译 - 所有术语一次性发送给AI"""
        from ModuleFolders.UserInterface.TermSelector.TermSelector import TermSelector
        from ModuleFolders.Infrastructure.TaskConfig.TaskConfig import TaskConfig
        from ModuleFolders.Infrastructure.TaskConfig.TaskType import TaskType
        from ModuleFolders.Infrastructure.LLMRequester.LLMRequester import LLMRequester
        import re

        console.print(f"\n[cyan]{self.i18n.get('msg_starting_batch_translate')}[/cyan]")

        task_config = TaskConfig()
        task_config.load_config_from_dict(self.config)
        task_config.prepare_for_translation(TaskType.TRANSLATION)

        platform_config = temp_config if temp_config else task_config.get_platform_configuration("translationReq")
        target_language = task_config.target_language

        # 构建批量请求
        term_list = []
        for src, data in filtered_terms.items():
            term_list.append({
                "src": src,
                "type": data.get("type", "专有名词"),
                "info": data.get("info", "null")
            })

        system_prompt = f"""You are a terminology translator. Translate all terms into "{target_language}".
Each input item may include an "info" field with context from glossary analysis. Use it to keep names, character voice, places, items, and setting terms consistent.
The "src" field is the source-text matching key. The "dst" field is the replacement value that may be inserted verbatim into translated text.
"dst" must contain only the final translated surface form. Never output "translation (note)", "translation / note", "translation: note", "translation - note", or any other "translation + explanation" form.
Never put identity, role labels, character relationships, context notes, disambiguation, jokes, or reader-facing hints in "dst"; put them in "info".
Qualifiers such as left/right/front/back or numbers may appear in "dst" only when explicitly present in "src". If no pure replacement-safe translation is certain, leave "dst" empty.

Output a JSON array, each element: {{"src": "original", "dst": "translation", "info": "note"}}
Only output the JSON array, no other text."""

        user_content = json.dumps(term_list, ensure_ascii=False)
        messages = [{"role": "user", "content": user_content}]

        requester = LLMRequester()
        skip, _, response, pt, ct = requester.sent_request(messages, system_prompt, platform_config)

        if skip or not response:
            console.print(f"[red]{self.i18n.get('msg_no_translation_results')}[/red]")
            return

        console.print(f"[green]{self.i18n.get('msg_batch_translate_complete')} | {pt}+{ct}T[/green]")

        # 解析批量响应
        translated = {}
        try:
            json_match = re.search(r'\[[\s\S]*\]', response)
            if json_match:
                parsed = json.loads(json_match.group())
                for item in parsed:
                    if isinstance(item, dict) and 'src' in item and 'dst' in item:
                        translated[item['src']] = {"dst": item['dst'], "info": item.get('info', '')}
        except Exception:
            pass

        # 构建结果
        multi_results = []
        for src, data in filtered_terms.items():
            t = translated.get(src)
            options = [t] if t and t['dst'] else []
            if options:
                multi_results.append({
                    "src": src,
                    "type": data.get("type", ""),
                    "analysis_info": data.get("info", "null"),
                    "options": options,
                    "selected_index": 0
                })
            else:
                console.print(f"[red]✗ {src} {self.i18n.get('msg_term_all_failed')}[/red]")

        skipped = len(filtered_terms) - len(multi_results)
        if skipped > 0:
            console.print(f"\n[yellow]⚠ {skipped} {self.i18n.get('msg_term_skipped_count')}[/yellow]")

        if not multi_results:
            console.print(f"[yellow]{self.i18n.get('msg_no_translation_results')}[/yellow]")
            fallback_glossary = self._generate_glossary_json(filtered_terms)
            fallback_path = self._build_output_glossary_path(base_glossary_path, "_翻译失败原文回退")
            self._save_glossary_json_to_path(fallback_glossary, fallback_path)
            console.print(f"[yellow]{self.i18n.get('msg_glossary_saved') or '术语表已保存'}: {fallback_path}[/yellow]")
            return

        # 定义回调
        def save_single_term(term_data):
            if save_mode not in ("import", "both"):
                return
            self.save_glossary_directly([term_data], save_mode="import", merge_options=merge_options)

        def retry_translation(src, term_type, avoid_set=None):
            source = filtered_terms.get(src, {})
            term_data = {"type": term_type, "info": source.get("info", "null")}
            return self._request_term_translation(src, term_data, target_language, platform_config, avoid_set or set())

        selector = TermSelector(multi_results, request_callback=retry_translation, save_callback=save_single_term)
        selected_results = selector.show_selector()

        if not selected_results:
            console.print(f"[yellow]{self.i18n.get('msg_cancelled')}[/yellow]")
            return

        self._save_selected_translations(
            selected_results,
            filtered_terms,
            save_mode=save_mode,
            base_glossary_path=base_glossary_path,
            merge_options=merge_options,
        )

    def _save_selected_translations(self, selected_results, filtered_terms, save_mode="import", base_glossary_path=None, merge_options=None):
        """保存用户选择的翻译到术语表"""
        added_count = 0
        if save_mode in ("import", "both"):
            before = self._count_glossary_history_entries(self.config.get("prompt_dictionary_data", []))
            self.save_glossary_directly(selected_results, save_mode="import", merge_options=merge_options)
            after = self._count_glossary_history_entries(self.config.get("prompt_dictionary_data", []))
            added_count = max(0, after - before)
            console.print(f"[bold green]{self.i18n.get('msg_terms_added') or '已添加'} {added_count} {self.i18n.get('msg_terms_to_glossary') or '个术语到术语表'}[/bold green]")

        if save_mode in ("standalone", "both"):
            selected_map = {item.get("src"): item for item in selected_results if item.get("src")}
            merged_glossary = []
            for src, meta in filtered_terms.items():
                selected = selected_map.get(src)
                if selected:
                    merged_glossary.append({
                        "src": src,
                        "dst": selected.get("dst", ""),
                        "info": self._clean_analysis_info(
                            selected.get("info") or meta.get("info"),
                            meta.get("type"),
                            meta.get("category"),
                        )
                    })
                else:
                    merged_glossary.append({
                        "src": src,
                        "dst": "",
                        "info": self._clean_analysis_info(meta.get("info"), meta.get("type"), meta.get("category"))
                    })

            save_path = self._build_output_glossary_path(base_glossary_path, "_独立术语表_翻译结果")
            self._save_glossary_json_to_path(merged_glossary, save_path)
            console.print(f"[bold green]{self.i18n.get('msg_glossary_saved') or '术语表已保存'}: {save_path}[/bold green]")

    def _count_glossary_history_entries(self, items):
        count = 0
        for item in items or []:
            if not isinstance(item, dict):
                continue
            count += 1
            history = item.get("history")
            if isinstance(history, list):
                count += len([entry for entry in history if isinstance(entry, dict)])
        return count

    def _save_glossary_json_to_path(self, glossary_data, output_path):
        os.makedirs(os.path.dirname(output_path), exist_ok=True)
        with open(output_path, 'w', encoding='utf-8') as f:
            json.dump(glossary_data, f, indent=2, ensure_ascii=False)

    def _build_output_glossary_path(self, base_glossary_path=None, suffix="_独立术语表"):
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        if base_glossary_path:
            base_dir = os.path.dirname(base_glossary_path) or "."
            base_name = os.path.splitext(os.path.basename(base_glossary_path))[0]
            if base_name.endswith("_自动术语"):
                base_name = base_name[:-5]
        else:
            base_dir = "."
            base_name = "glossary"
        return os.path.join(base_dir, f"{base_name}{suffix}_{timestamp}.json")

    def _request_term_translation(self, src, term_data, target_language, platform_config, avoid_set):
        """请求单个术语的翻译"""
        from ModuleFolders.Infrastructure.LLMRequester.LLMRequester import LLMRequester

        term_type = term_data.get("type", "专有名词")
        term_info = term_data.get("info", "null")
        avoid_hint = ""
        if avoid_set:
            avoid_list = ", ".join(list(avoid_set)[:5])
            avoid_hint = f"\nPlease provide a different translation from: {avoid_list}"

        system_prompt = f"""You are a terminology translator. Translate the term into "{target_language}".
Term type: {term_type}
Known context: {term_info}
{avoid_hint}

Translation is the exact replacement value that may be inserted into translated text. It must contain only the final translated surface form.
Never output "translation (note)", "translation / note", "translation: note", "translation - note", or any other "translation + explanation" form in Translation.
Never put identity, role labels, character relationships, context notes, disambiguation, jokes, or reader-facing hints in Translation; put them in Note.
Qualifiers such as left/right/front/back or numbers may appear in Translation only when explicitly present in the source term. If no pure replacement-safe translation is certain, leave Translation empty.

Output format (use | as separator):
Translation|Note"""

        messages = [{"role": "user", "content": src}]

        try:
            requester = LLMRequester()
            skip, _, response, _, _ = requester.sent_request(messages, system_prompt, platform_config)

            if skip or not response:
                return None

            response = response.strip()
            if '|' in response:
                parts = response.split('|', 1)
                dst = parts[0].strip()
                info = parts[1].strip() if len(parts) > 1 else ""
            else:
                dst = response.strip()
                info = ""

            if dst and dst != src:
                return {"dst": dst, "info": info}
        except Exception as e:
            console.print(f"[red]{self.i18n.get('msg_translation_error') or '翻译错误'}: {e}[/red]")

        return None

    def _parse_glossary_response(self, response):
        """解析LLM返回的分类规则 JSON，兼容旧版术语数组。"""
        import re
        parsed = self._load_json_from_response(response, re)

        if isinstance(parsed, list):
            payload = self._empty_analysis_payload()
            payload["terms"] = self._normalize_term_items(parsed)
            return payload

        if not isinstance(parsed, dict):
            return self._empty_analysis_payload()

        return self._parse_glossary_payload_dict(parsed)

    def _parse_glossary_payload_dict(self, parsed):
        payload = self._empty_analysis_payload()
        for source in self._iter_analysis_dicts(parsed):
            single_payload = self._parse_single_glossary_payload(source)
            self._merge_analysis_payload(payload, single_payload)
        return payload

    def _iter_analysis_dicts(self, parsed, depth=0, seen=None):
        if not isinstance(parsed, dict) or depth > 4:
            return []
        seen = seen or set()
        object_id = id(parsed)
        if object_id in seen:
            return []
        seen.add(object_id)

        wrapper_keys = (
            "data", "result", "results", "analysis", "rules", "rule_config",
            "payload", "output", "response", "content",
        )
        for key in wrapper_keys:
            wrapped = self._first_present(parsed, (key,), None)
            if isinstance(wrapped, dict):
                return self._iter_analysis_dicts(wrapped, depth + 1, seen)
            if isinstance(wrapped, list):
                return [{"glossary": wrapped}]

        sources = [parsed]
        incremental_keys = (
            "new", "replace", "updated", "updates", "delta", "changes",
            "新增", "新增项", "替换", "更新", "补全", "修改", "变更",
        )
        for key in incremental_keys:
            value = self._first_present(parsed, (key,), None)
            if isinstance(value, dict):
                sources.extend(self._iter_analysis_dicts(value, depth + 1, seen))
            elif isinstance(value, list):
                sources.append({"glossary": value})
        return sources

    def _parse_single_glossary_payload(self, parsed):
        payload = self._empty_analysis_payload()
        term_items = self._first_present(
            parsed,
            (
                "glossary", "terms", "terminology", "term_list", "prompt_dictionary_data",
                "prompt_dictionary", "dictionary", "term_dictionary", "new_terms",
                "replace_terms", "updated_terms", "术语表", "专有名词", "词汇表",
                "名词表", "新增术语", "更新术语", "替换术语",
            ),
            [],
        )
        payload["terms"] = self._normalize_term_items(term_items)

        exclusion_items = self._first_present(
            parsed,
            (
                "exclusion_list", "non_translation_list", "no_translate", "ntl",
                "exclusion_list_data", "do_not_translate", "preserve_list",
                "禁翻表", "排除列表", "不翻译列表", "保留原文列表",
            ),
            [],
        )
        payload["exclusion_list_data"] = self._normalize_exclusion_items(exclusion_items)

        character_items = self._first_present(
            parsed,
            (
                "characterization", "characters", "character_profiles",
                "characterization_data", "character_data", "人物设定",
                "角色设定", "角色", "人物",
            ),
            [],
        )
        payload["characterization_data"] = self._normalize_character_items(character_items)

        world_building = self._first_present(
            parsed,
            (
                "world_building", "worldview", "world_settings", "setting",
                "world_building_content", "background", "lore", "世界观",
                "世界观设定", "世界设定", "背景设定", "设定",
            ),
            "",
        )
        payload["world_building_content"] = self._format_analysis_sections(world_building)

        writing_style = self._first_present(
            parsed,
            (
                "writing_style", "style", "translation_style",
                "writing_style_content", "tone", "文风", "文风要求",
                "翻译风格", "风格", "语体",
            ),
            "",
        )
        payload["writing_style_content"] = self._format_analysis_sections(writing_style)

        examples = self._first_present(
            parsed,
            (
                "translation_examples", "translation_example",
                "translation_example_data", "examples", "sample_translations",
                "翻译示例", "翻译例句", "例句",
            ),
            [],
        )
        payload["translation_example_data"] = self._normalize_translation_examples(examples)

        return payload

    def _load_json_from_response(self, response, re_module):
        if not response:
            return None

        text = response.strip()
        candidates = []
        fence_matches = re_module.findall(r"```(?:json)?\s*([\s\S]*?)```", text, re_module.IGNORECASE)
        for fence_content in fence_matches:
            candidates.append(fence_content.strip())
        candidates.append(text)

        candidates.extend(self._extract_json_span_candidates(text))
        for fence_content in fence_matches:
            candidates.extend(self._extract_json_span_candidates(fence_content.strip()))

        object_match = re_module.search(r"\{[\s\S]*\}", text)
        if object_match:
            candidates.append(object_match.group())
        array_match = re_module.search(r"\[[\s\S]*\]", text)
        if array_match:
            candidates.append(array_match.group())

        for candidate in candidates:
            try:
                return json.loads(candidate)
            except Exception:
                continue
        return None

    def _extract_json_span_candidates(self, text, max_candidates=12):
        spans = []
        for open_char, close_char in (("{", "}"), ("[", "]")):
            for start, char in enumerate(text):
                if char != open_char:
                    continue
                candidate = self._extract_balanced_json_span(text, start, open_char, close_char)
                if candidate:
                    spans.append(candidate)
                    if len(spans) >= max_candidates:
                        return spans
        return spans

    def _extract_balanced_json_span(self, text, start, open_char, close_char):
        depth = 0
        in_string = False
        escape = False
        for index in range(start, len(text)):
            char = text[index]
            if in_string:
                if escape:
                    escape = False
                elif char == "\\":
                    escape = True
                elif char == '"':
                    in_string = False
                continue

            if char == '"':
                in_string = True
            elif char == open_char:
                depth += 1
            elif char == close_char:
                depth -= 1
                if depth == 0:
                    return text[start:index + 1].strip()
        return ""

    def _first_present(self, data, keys, default):
        for key in keys:
            if key in data:
                value = data.get(key)
                return default if value is None else value
        return default

    def _coerce_analysis_items(self, items, single_item_keys=()):
        if isinstance(items, list):
            return items
        if not isinstance(items, dict):
            return []

        direct_keys = (
            "items", "data", "list", "entries", "values", "records",
            "new", "replace", "updated", "updates", "delta", "changes",
            "新增", "新增项", "替换", "更新", "补全", "修改", "变更",
        )
        collected = []
        for key in direct_keys:
            value = items.get(key)
            if value is None:
                continue
            collected.extend(self._coerce_analysis_items(value, single_item_keys))
        if collected:
            return collected

        if any(key in items for key in single_item_keys):
            return [items]
        return []

    def _normalize_term_items(self, items):
        items = self._coerce_analysis_items(
            items,
            ("src", "term", "name", "original", "source", "原文术语", "术语", "原文", "名称"),
        )
        if not items:
            return []

        terms = []
        for item in items:
            if not isinstance(item, dict):
                continue
            src = self._normalize_glossary_text(
                item.get("src")
                or item.get("term")
                or item.get("name")
                or item.get("original")
                or item.get("source")
                or item.get("原文术语")
                or item.get("术语")
                or item.get("原文")
                or item.get("名称")
            )
            if not src:
                continue
            category = self._normalize_glossary_text(item.get("category") or item.get("大类") or item.get("分类"))
            term_type = self._normalize_glossary_text(item.get("type") or item.get("类别") or category, "专有名词")
            dst = self._normalize_glossary_text(
                item.get("dst")
                or item.get("target")
                or item.get("translation")
                or item.get("translated_name")
                or item.get("译名")
                or item.get("翻译")
                or item.get("目标译文")
            )
            raw_info = self._normalize_glossary_info(item)
            terms.append({
                "src": src,
                "dst": dst,
                "type": term_type,
                "category": category,
                "info": self._clean_analysis_info(raw_info, term_type, category),
                **self._extract_legacy_metadata(item),
            })
        return terms

    def _normalize_exclusion_items(self, items):
        if isinstance(items, str):
            items = [{"markers": line.strip()} for line in items.splitlines() if line.strip()]
        else:
            items = self._coerce_analysis_items(
                items,
                ("markers", "marker", "src", "text", "regex", "保留文本", "禁翻文本", "正则"),
            )
        if not items:
            return []

        result = []
        seen = set()
        for item in items:
            if isinstance(item, str):
                item = {"markers": item}
            if not isinstance(item, dict):
                continue
            markers = self._normalize_glossary_text(
                item.get("markers")
                or item.get("marker")
                or item.get("src")
                or item.get("text")
                or item.get("保留文本")
                or item.get("禁翻文本")
                or item.get("原文")
            )
            regex = self._normalize_glossary_text(item.get("regex") or item.get("正则"))
            info = self._normalize_glossary_text(
                item.get("info") or item.get("description") or item.get("desc") or item.get("说明") or item.get("原因")
            )
            if not markers and not regex:
                continue
            key = (markers, regex)
            if key in seen:
                continue
            seen.add(key)
            result.append({
                "markers": markers,
                "info": info,
                "regex": regex,
                **self._extract_legacy_metadata(item),
            })
        return result

    def _normalize_character_items(self, items):
        items = self._coerce_analysis_items(
            items,
            ("original_name", "src", "name", "original", "角色原名", "原名", "角色名", "人物名"),
        )
        if not items:
            return []

        result = []
        for item in items:
            if not isinstance(item, dict):
                continue
            original_name = self._normalize_glossary_text(
                item.get("original_name")
                or item.get("src")
                or item.get("name")
                or item.get("original")
                or item.get("角色原名")
                or item.get("原名")
                or item.get("角色名")
                or item.get("人物名")
            )
            if not original_name:
                continue
            additional_parts = []
            for key in (
                "identity", "role", "relationship", "relationships", "info",
                "description", "desc", "note", "annotation", "身份", "立场",
                "关系", "剧情作用", "说明", "备注", "附加信息",
            ):
                value = self._normalize_glossary_text(item.get(key))
                if value:
                    additional_parts.append(value)
            aliases, alias_notes = self._normalize_aliases_with_notes(
                item.get("aliases")
                or item.get("alias")
                or item.get("nicknames")
                or item.get("other_names")
                or item.get("别名")
                or item.get("昵称")
                or item.get("称呼")
                or item.get("其他称呼")
            )
            additional_parts.extend(alias_notes)
            explicit_additional = self._normalize_glossary_text(item.get("additional_info") or item.get("附加信息"))
            if explicit_additional:
                additional_parts.insert(0, explicit_additional)
            additional_info = "；".join(dict.fromkeys(part for part in additional_parts if part))
            result.append({
                "original_name": original_name,
                "translated_name": self._normalize_glossary_text(
                    item.get("translated_name") or item.get("dst") or item.get("translation") or item.get("译名") or item.get("翻译名")
                ),
                "aliases": aliases,
                "gender": self._normalize_glossary_text(item.get("gender") or item.get("性别")),
                "age": self._normalize_glossary_text(item.get("age") or item.get("年龄")),
                "personality": self._normalize_glossary_text(item.get("personality") or item.get("性格")),
                "speech_style": self._normalize_glossary_text(
                    item.get("speech_style") or item.get("speaking_style") or item.get("tone") or item.get("说话方式") or item.get("语气")
                ),
                "pronouns": self._normalize_glossary_text(
                    item.get("pronouns") or item.get("first_second_person") or item.get("person_pronouns") or item.get("第一人称/第二人称") or item.get("人称代词") or item.get("称呼体系")
                ),
                "speech_quirks": self._normalize_glossary_text(
                    item.get("speech_quirks") or item.get("verbal_quirks") or item.get("catchphrase") or item.get("ending_particles") or item.get("口癖") or item.get("语尾")
                ),
                "additional_info": additional_info,
                **self._extract_legacy_metadata(item),
            })
        return result

    def _normalize_translation_examples(self, items):
        items = self._coerce_analysis_items(
            items,
            ("src", "source", "original", "原文", "例句"),
        )
        if not items:
            return []

        result = []
        seen = set()
        for item in items:
            if not isinstance(item, dict):
                continue
            src = self._normalize_glossary_text(item.get("src") or item.get("source") or item.get("original") or item.get("原文") or item.get("例句"))
            dst = self._normalize_glossary_text(item.get("dst") or item.get("target") or item.get("translation") or item.get("译文") or item.get("翻译"))
            if not src:
                continue
            key = (src, dst)
            if key in seen:
                continue
            seen.add(key)
            result.append({
                "src": src,
                "dst": dst,
                **self._extract_legacy_metadata(item),
            })
        return result

    def _format_analysis_sections(self, value):
        if value is None:
            return ""
        if isinstance(value, str):
            return value.strip()
        if isinstance(value, list):
            return "\n\n".join(
                block for block in (self._format_analysis_sections(item) for item in value) if block
            )
        if isinstance(value, dict):
            title = self._normalize_glossary_text(value.get("title") or value.get("name") or value.get("category"))
            content = value.get("content")
            if content is None:
                content = value.get("description") or value.get("info") or value.get("summary")
            content_text = self._format_analysis_sections(content) if isinstance(content, (list, dict)) else self._normalize_glossary_text(content)
            if not content_text:
                parts = []
                for key, item_value in value.items():
                    if key in ("title", "name", "category"):
                        continue
                    item_text = self._format_analysis_sections(item_value)
                    if item_text:
                        parts.append(f"{key}: {item_text}")
                content_text = "\n".join(parts)
            if title and content_text:
                return f"## {title}\n{content_text}"
            return content_text
        return str(value).strip()

    def _append_text_block(self, existing, addition):
        existing = self._normalize_glossary_text(existing)
        addition = self._normalize_glossary_text(addition)
        if not addition:
            return existing
        if addition in existing:
            return existing
        return f"{existing.rstrip()}\n\n{addition}" if existing else addition

    def _append_timeline_text_block(self, existing, addition):
        existing = self._normalize_glossary_text(existing)
        addition = self._normalize_glossary_text(addition)
        if not addition:
            return existing
        if not existing:
            return addition
        if addition in existing:
            return existing
        if existing in addition:
            return addition
        return f"{existing.rstrip()}\n\n{addition}"

    def _extend_unique_dicts(self, target, incoming, key_fields):
        seen = {
            tuple(self._normalize_glossary_text(item.get(field)) for field in key_fields)
            for item in target
            if isinstance(item, dict)
        }
        for item in incoming or []:
            if not isinstance(item, dict):
                continue
            key = tuple(self._normalize_glossary_text(item.get(field)) for field in key_fields)
            if not any(key) or key in seen:
                continue
            target.append(item)
            seen.add(key)
        return target

    def _timeline_item_has_content(self, item, tracked_fields, key_field="src"):
        if not isinstance(item, dict):
            return False
        if self._normalize_glossary_text(item.get(key_field)):
            return True
        return any(self._rule_field_has_content(item.get(field)) for field in tracked_fields)

    def _merge_character_lists(self, target, incoming, fill_existing=False, replace_existing=False):
        by_name = {
            self._normalize_glossary_text(item.get("original_name")): item
            for item in target
            if isinstance(item, dict) and self._normalize_glossary_text(item.get("original_name"))
        }
        for item in incoming or []:
            if not isinstance(item, dict):
                continue
            name = self._normalize_glossary_text(item.get("original_name"))
            if not name:
                continue
            existing = by_name.get(name)
            if existing:
                if fill_existing or replace_existing:
                    for key, value in item.items():
                        value_text = self._normalize_glossary_text(value)
                        if not value_text:
                            continue
                        if replace_existing or not self._normalize_glossary_text(existing.get(key)):
                            existing[key] = value
                continue
            target.append(item)
            by_name[name] = item
        return target

    def _merge_character_item(self, existing, incoming, source_label, source_volume, allow_replace=False, track_history=True):
        if allow_replace:
            return self._merge_timeline_item(
                existing,
                incoming,
                source_label,
                source_volume,
                CHARACTER_TIMELINE_FIELDS,
                key_field="original_name",
                track_history=track_history,
            )

        changed = False
        for key in CHARACTER_TIMELINE_FIELDS:
            value = incoming.get(key)
            if not self._rule_field_has_content(value):
                continue
            if not self._rule_field_has_content(existing.get(key)):
                changed = True
                break

        if not changed:
            return False

        for key in CHARACTER_TIMELINE_FIELDS:
            value = incoming.get(key)
            if self._rule_field_has_content(value) and not self._rule_field_has_content(existing.get(key)):
                existing[key] = value
        if track_history:
            self._ensure_timeline_history(existing, source_label, source_volume, CHARACTER_TIMELINE_FIELDS, key_field="original_name")
        return True

    def _with_source_metadata(self, item, source_label, source_volume):
        prepared = dict(item)
        label = self._normalize_glossary_text(prepared.get("source") or source_label)
        volume = self._normalize_volume_number(prepared.get("volume"))
        if volume is None:
            volume = source_volume
        if label:
            prepared["source"] = label
        if volume is not None:
            prepared["volume"] = volume
            if not label:
                prepared["source"] = self._format_volume_label(volume)
        return prepared

    def _ensure_timeline_history(self, item, source_label, source_volume, tracked_fields, key_field="src"):
        if not isinstance(item, dict):
            return []
        history = item.get("history")
        if not isinstance(history, list):
            history = []
            item["history"] = history

        volume = self._normalize_volume_number(source_volume)
        if volume is None:
            volume = self._normalize_volume_number(item.get("volume"))
        label = self._normalize_glossary_text(source_label)
        if not label:
            label = self._normalize_glossary_text(item.get("source"))
        if volume is None:
            volume = self._normalize_volume_number(label)
        if volume is None and self._timeline_item_has_content(item, tracked_fields, key_field):
            volume = 1
        if not label and volume is not None:
            label = self._format_volume_label(volume)

        snapshot = self._build_history_snapshot(item, label, volume, tracked_fields, key_field=key_field)
        if not snapshot:
            return history

        snap_key = self._history_key(snapshot)
        for existing in history:
            if self._history_key(existing) == snap_key:
                return history
        history.append(snapshot)
        history.sort(key=lambda entry: self._history_sort_key(entry))
        return history

    def _merge_timeline_item(self, existing, incoming, source_label, source_volume, tracked_fields, key_field="src", track_history=True):
        incoming_values = {
            key: incoming.get(key)
            for key in tracked_fields
            if self._rule_field_has_content(incoming.get(key))
        }
        if not incoming_values:
            return False

        if track_history:
            self._ensure_timeline_history(
                existing,
                existing.get("source"),
                existing.get("volume"),
                tracked_fields,
                key_field=key_field,
            )
        label = self._normalize_glossary_text(source_label or incoming.get("source"))
        volume = self._normalize_volume_number(source_volume)
        if volume is None:
            volume = self._normalize_volume_number(incoming.get("volume"))

        changed = False
        if track_history:
            incoming_snapshot = self._build_history_snapshot(
                {**incoming, **incoming_values},
                label,
                volume,
                tracked_fields,
                key_field=key_field,
            )
            if not incoming_snapshot:
                return False
            incoming_snapshot = self._merge_snapshot_with_previous_history(
                existing,
                incoming_snapshot,
                volume,
                tracked_fields,
                key_field=key_field,
            )
            changed = self._upsert_timeline_history(existing, incoming_snapshot)
            if changed:
                self._sync_latest_timeline_metadata(existing)
            return changed

        for key, value in incoming_values.items():
            if self._normalize_glossary_text(existing.get(key)) != self._normalize_glossary_text(value):
                existing[key] = value
                changed = True
        if changed:
            self._sync_latest_timeline_metadata(existing)
        return changed

    def _upsert_timeline_history(self, item, snapshot):
        history = item.get("history")
        if not isinstance(history, list):
            history = []
            item["history"] = history

        snap_key = self._history_key(snapshot)
        for index, existing in enumerate(history):
            if self._history_key(existing) == snap_key:
                merged = self._merge_history_snapshot(existing, snapshot)
                if existing == merged:
                    return False
                history[index] = merged
                history.sort(key=lambda entry: self._history_sort_key(entry))
                return True

        history.append(snapshot)
        history.sort(key=lambda entry: self._history_sort_key(entry))
        return True

    def _merge_snapshot_with_previous_history(self, item, snapshot, source_volume, tracked_fields, key_field="src"):
        volume = self._normalize_volume_number(source_volume)
        if volume is None:
            return snapshot

        effective = self._effective_history_snapshot(
            item.get("history") if isinstance(item, dict) else [],
            volume,
            tracked_fields,
            key_field=key_field,
        )
        if not effective:
            return snapshot

        merged = {}
        key_value = snapshot.get(key_field) or effective.get(key_field)
        if self._normalize_glossary_text(key_value):
            merged[key_field] = key_value
        if snapshot.get("source"):
            merged["source"] = snapshot.get("source")
        if self._normalize_volume_number(snapshot.get("volume")) is not None:
            merged["volume"] = self._normalize_volume_number(snapshot.get("volume"))
        carry_legacy_metadata = self._has_legacy_mapping_evidence(effective)
        for field in tracked_fields:
            if field in LEGACY_METADATA_FIELDS:
                if carry_legacy_metadata and self._rule_field_has_content(effective.get(field)):
                    merged[field] = effective.get(field)
                if self._rule_field_has_content(snapshot.get(field)):
                    merged[field] = snapshot.get(field)
                continue
            if self._normalize_glossary_text(effective.get(field)):
                merged[field] = effective.get(field)
            if self._rule_field_has_content(snapshot.get(field)):
                merged[field] = snapshot.get(field)
        return merged

    def _effective_history_snapshot(self, history, current_volume, tracked_fields, key_field="src"):
        if not isinstance(history, list):
            return {}
        volume = self._normalize_volume_number(current_volume)
        if volume is None:
            return {}

        effective = {}
        for entry in sorted((item for item in history if isinstance(item, dict)), key=self._history_sort_key):
            entry_volume = self._normalize_volume_number(entry.get("volume"))
            if entry_volume is None or entry_volume > volume:
                continue
            key_value = entry.get(key_field)
            if self._normalize_glossary_text(key_value):
                effective[key_field] = key_value
            for field in tracked_fields:
                if self._rule_field_has_content(entry.get(field)):
                    effective[field] = entry.get(field)
        return effective

    def _merge_history_snapshot(self, existing, incoming):
        if not isinstance(existing, dict):
            return incoming
        if not isinstance(incoming, dict):
            return existing

        merged = dict(existing)
        for key, value in incoming.items():
            if key in ("source", "volume"):
                if self._normalize_glossary_text(value):
                    merged[key] = value
                continue
            if self._rule_field_has_content(value):
                merged[key] = value
        return merged

    def _sync_latest_timeline_metadata(self, item):
        history = item.get("history")
        if not isinstance(history, list) or not history:
            return
        entries = [entry for entry in history if isinstance(entry, dict)]
        numbered_entries = [
            entry for entry in entries
            if self._normalize_volume_number(entry.get("volume")) is not None
        ]
        candidates = numbered_entries or entries
        if not candidates:
            return
        latest = max(candidates, key=self._history_sort_key)
        label = self._normalize_glossary_text(latest.get("source")) if isinstance(latest, dict) else ""
        volume = self._normalize_volume_number(latest.get("volume")) if isinstance(latest, dict) else None
        if label:
            item["updated_in"] = label
        if volume is not None:
            item["updated_volume"] = volume
            if not label:
                item["updated_in"] = self._format_volume_label(volume)
        for meta_key in LEGACY_METADATA_FIELDS:
            if self._rule_field_has_content(latest.get(meta_key)):
                item[meta_key] = latest.get(meta_key)
            else:
                item.pop(meta_key, None)

    def _build_history_snapshot(self, item, source_label, source_volume, tracked_fields, key_field="src"):
        if not isinstance(item, dict):
            return {}
        snapshot = {}
        key_value = self._normalize_glossary_text(item.get(key_field))
        if key_value:
            snapshot[key_field] = key_value
        label = self._normalize_glossary_text(source_label)
        volume = self._normalize_volume_number(source_volume)
        if label:
            snapshot["source"] = label
        if volume is not None:
            snapshot["volume"] = volume
            if not label:
                snapshot["source"] = self._format_volume_label(volume)
        for key in tracked_fields:
            value = item.get(key)
            if self._rule_field_has_content(value):
                snapshot[key] = value
        return snapshot

    def _history_key(self, entry):
        if not isinstance(entry, dict):
            return ("", "")
        volume = self._normalize_volume_number(entry.get("volume"))
        if volume is not None:
            return ("volume", volume)
        return ("source", self._normalize_glossary_text(entry.get("source")))

    def _history_sort_key(self, entry):
        volume = self._normalize_volume_number(entry.get("volume")) if isinstance(entry, dict) else None
        if volume is None:
            return (10**9, self._normalize_glossary_text(entry.get("source") if isinstance(entry, dict) else ""))
        return (volume, "")

    def _merge_text_rule_with_history(self, data_key, history_key, incoming_text, source_label, source_volume, replace=False, track_history=True):
        current_text = self._normalize_glossary_text(self.config.get(data_key))
        if not track_history:
            next_text = incoming_text if replace else self._append_text_block(current_text, incoming_text)
            return next_text

        history = self.config.get(history_key)
        if not isinstance(history, list):
            history = []

        if current_text and not history:
            base_entry = {
                "source": self._format_volume_label(1),
                "volume": 1,
                "content": current_text,
            }
            history.append(base_entry)

        label = self._normalize_glossary_text(source_label)
        volume = self._normalize_volume_number(source_volume)
        if not label and volume is not None:
            label = self._format_volume_label(volume)

        base_text = self._select_text_history_for_merge(history, volume) or current_text
        version_text = self._append_timeline_text_block(base_text, incoming_text)
        if version_text:
            entry = {"source": label, "content": version_text}
            if self._contains_legacy_reference_text(incoming_text):
                entry["_glossary_origin"] = LEGACY_GLOSSARY_ORIGIN
                entry["_mapping_status"] = "seed"
                entry["_legacy_id"] = self._make_legacy_id(history_key, {"src": incoming_text[:80]}, 1)
            if volume is not None:
                entry["volume"] = volume
                if not label:
                    entry["source"] = self._format_volume_label(volume)
            merged = []
            replaced = False
            for old in history:
                if self._history_key(old) == self._history_key(entry):
                    merged.append(entry)
                    replaced = True
                else:
                    merged.append(old)
            if not replaced:
                merged.append(entry)
            history = merged
            history.sort(key=lambda item: self._history_sort_key(item))
            self.config[history_key] = history
        return current_text or incoming_text

    def _select_text_history_for_merge(self, history, current_volume):
        if not isinstance(history, list):
            return ""
        volume = self._normalize_volume_number(current_volume)
        if volume is None:
            return ""
        selected = ""
        for entry in sorted((item for item in history if isinstance(item, dict)), key=self._history_sort_key):
            entry_volume = self._normalize_volume_number(entry.get("volume"))
            if entry_volume is None or entry_volume >= volume:
                continue
            selected = self._append_timeline_text_block(selected, entry.get("content"))
        return selected

    def _derive_characters_from_terms(self, terms):
        result = []
        for term in terms:
            term_type = self._normalize_glossary_text(term.get("type")).lower()
            category = self._normalize_glossary_text(term.get("category")).lower()
            if not any(key in term_type or key in category for key in ("人名", "人物", "角色", "character", "person")):
                continue
            src = self._normalize_glossary_text(term.get("src"))
            if not src:
                continue
            info = self._normalize_glossary_text(term.get("info"))
            result.append({
                "original_name": src,
                "translated_name": "",
                "aliases": [],
                "gender": "",
                "age": "",
                "personality": "",
                "speech_style": "",
                "pronouns": "",
                "speech_quirks": "",
                "additional_info": "" if info.lower() in ("null", "none") else info,
            })
        return result

    def _derive_world_building_from_terms(self, terms):
        lines = []
        for term in terms:
            term_type = self._normalize_glossary_text(term.get("type"))
            category = self._normalize_glossary_text(term.get("category"))
            type_text = f"{category}/{term_type}" if category and category != term_type else term_type
            type_lower = type_text.lower()
            if any(key in type_lower for key in ("人名", "人物", "角色", "character", "person")):
                continue
            if not any(key in type_lower for key in (
                "世界", "设定", "地名", "地点", "组织", "势力", "技能", "能力", "系统",
                "术语", "place", "location", "organization", "faction", "skill", "ability",
                "system", "world", "setting", "term",
            )):
                continue
            src = self._normalize_glossary_text(term.get("src"))
            if not src:
                continue
            info = self._normalize_glossary_text(term.get("info"))
            suffix = "" if info.lower() in ("", "null", "none") else f"：{info}"
            lines.append(f"- {src}（{type_text or '设定'}）{suffix}")
        if not lines:
            return ""
        title = self._tr("glossary_world_building_clues_title", "世界观与设定线索")
        return f"## {title}\n" + "\n".join(dict.fromkeys(lines))

    def _normalize_glossary_text(self, value, default=""):
        if value is None:
            return default
        text = str(value).strip()
        return text if text else default

    def _rule_field_has_content(self, value):
        if value is None:
            return False
        if isinstance(value, (list, tuple, set)):
            return any(self._normalize_glossary_text(item) for item in value)
        return bool(self._normalize_glossary_text(value))

    def _normalize_aliases(self, value):
        aliases, _ = self._normalize_aliases_with_notes(value)
        return aliases

    def _normalize_aliases_with_notes(self, value):
        if value is None:
            return [], []
        if isinstance(value, (list, tuple, set)):
            raw_items = value
        else:
            text = self._normalize_glossary_text(value)
            if not text:
                return [], []
            raw_items = re.split(r"[,;|/，、；／\n]+", text.replace("[Separator]", "\n"))

        aliases = []
        notes = []
        seen = set()
        for item in raw_items:
            alias = self._normalize_glossary_text(item)
            if not alias:
                continue
            alias, note = self._split_alias_note(alias)
            if note:
                notes.append(note)
            if not alias:
                continue
            marker = alias.casefold()
            if marker in seen:
                continue
            seen.add(marker)
            aliases.append(alias)
        return aliases, list(dict.fromkeys(notes))

    def _split_alias_note(self, alias):
        alias = self._normalize_glossary_text(alias)
        if not alias:
            return "", ""

        note_parts = []
        patterns = (
            r"^(?P<alias>.+?)（(?P<note>[^（）]{2,80})）$",
            r"^(?P<alias>.+?)\((?P<note>[^()]{2,80})\)$",
        )
        changed = True
        while changed:
            changed = False
            for pattern in patterns:
                match = re.match(pattern, alias)
                if not match:
                    continue
                candidate = self._normalize_glossary_text(match.group("alias"))
                note = self._normalize_glossary_text(match.group("note"))
                if candidate and note and self._alias_note_looks_like_note(note):
                    alias = candidate
                    note_parts.append(note)
                    changed = True
                    break

        return alias, "；".join(dict.fromkeys(note_parts))

    def _alias_note_looks_like_note(self, note):
        note = self._normalize_glossary_text(note)
        if not note:
            return False
        if len(note) > 80:
            return False
        note_markers = (
            "玩梗", "备注", "说明", "注", "称呼", "昵称", "别称", "旧译",
            "译名", "梗", "调侃", "误称", "假名", "化名", "自称", "他称",
            "joke", "note", "alias", "nickname", "translation", "translated",
            "legacy", "pun", "meme", "called", "reference",
        )
        return any(marker.casefold() in note.casefold() for marker in note_markers)

    def _extract_legacy_metadata(self, item):
        if not isinstance(item, dict):
            return {}
        metadata = {}
        for key in LEGACY_METADATA_FIELDS:
            if key not in item:
                continue
            value = item.get(key)
            if self._rule_field_has_content(value):
                metadata[key] = value
        for source_key, target_key in (
            ("legacy_id", "_legacy_id"),
            ("mapped_from", "_mapped_from"),
            ("mapping_confidence", "_mapping_confidence"),
            ("mapping_note", "_mapping_note"),
            ("mapping_status", "_mapping_status"),
            ("glossary_origin", "_glossary_origin"),
        ):
            if target_key in metadata:
                continue
            value = item.get(source_key)
            if self._rule_field_has_content(value):
                metadata[target_key] = value
        return metadata

    def _mark_legacy_payload(self, payload, mode):
        mode = self.normalize_legacy_mode(mode)
        if mode not in ("legacy_translation_extract", "legacy_mapping_extract"):
            return payload

        legacy_lookup = self._build_legacy_reference_lookup() if mode == "legacy_mapping_extract" else {}
        for index, item in enumerate(payload.get("terms", []) or [], start=1):
            if mode == "legacy_translation_extract":
                self._mark_legacy_item(item, LEGACY_GLOSSARY_ORIGIN, "seed", "term", index, force=True)
            else:
                self._normalize_legacy_mapping_item(item, "term", index, legacy_lookup)
        for index, item in enumerate(payload.get("exclusion_list_data", []) or [], start=1):
            if mode == "legacy_translation_extract":
                self._mark_legacy_item(item, LEGACY_GLOSSARY_ORIGIN, "seed", "exclusion", index, force=True)
            else:
                self._strip_legacy_metadata(item)
        for index, item in enumerate(payload.get("characterization_data", []) or [], start=1):
            if mode == "legacy_translation_extract":
                self._mark_legacy_item(item, LEGACY_GLOSSARY_ORIGIN, "seed", "character", index, force=True)
            else:
                self._normalize_legacy_mapping_item(item, "character", index, legacy_lookup)
        for index, item in enumerate(payload.get("translation_example_data", []) or [], start=1):
            if mode == "legacy_translation_extract":
                self._mark_legacy_item(item, LEGACY_GLOSSARY_ORIGIN, "seed", "example", index, force=True)
            else:
                self._strip_legacy_metadata(item)
        for key in ("world_building_content", "writing_style_content"):
            text = self._normalize_glossary_text(payload.get(key))
            if mode == "legacy_translation_extract" and text:
                payload[key] = text
        return payload

    def _mark_legacy_item(self, item, origin, status, prefix, index, force=False):
        if not isinstance(item, dict):
            return item
        if force:
            item["_glossary_origin"] = origin
            item["_mapping_status"] = status
            item["_legacy_id"] = self._make_legacy_id(prefix, item, index)
            return item
        item.setdefault("_glossary_origin", origin)
        item.setdefault("_mapping_status", status)
        item.setdefault("_legacy_id", self._make_legacy_id(prefix, item, index))
        return item

    def _normalize_legacy_mapping_item(self, item, prefix, index, legacy_lookup=None):
        if not isinstance(item, dict):
            return item

        if legacy_lookup:
            self._apply_exact_legacy_reference_mapping(item, prefix, legacy_lookup)

        copied_seed_id = self._normalize_glossary_text(item.get("_legacy_id"))
        if self._has_legacy_mapping_evidence(item) or self._looks_like_copied_legacy_seed_mapping(item):
            if copied_seed_id and not self._rule_field_has_content(item.get("_mapped_from")):
                item["_mapped_from"] = copied_seed_id
            self._mark_legacy_item(
                item,
                LEGACY_MAPPED_ORIGIN,
                "mapped",
                f"mapped_{prefix}",
                index,
                force=True,
            )
            return item

        self._strip_legacy_metadata(item)
        return item

    def _apply_exact_legacy_reference_mapping(self, item, item_type, legacy_lookup):
        if not isinstance(item, dict):
            return item
        if self._has_legacy_mapping_evidence(item):
            return item

        lookup_key = "character" if item_type == "character" else "term"
        source_field = "original_name" if lookup_key == "character" else "src"
        translation_field = "translated_name" if lookup_key == "character" else "dst"
        source_text = self._normalize_glossary_text(item.get(source_field))
        if not source_text:
            return item

        legacy_item = (legacy_lookup.get(lookup_key) or {}).get(source_text)
        if not legacy_item:
            return item

        mapped_from = self._normalize_glossary_text(legacy_item.get("_legacy_id"))
        translated_name = self._legacy_reference_translation(legacy_item, lookup_key)
        if not mapped_from or not translated_name:
            return item

        if not self._rule_field_has_content(item.get(translation_field)):
            item[translation_field] = translated_name
        item["_mapped_from"] = mapped_from
        item.setdefault("_mapping_confidence", "medium")
        item.setdefault("_mapping_note", "Auto-matched exact legacy reference name.")
        return item

    def _legacy_reference_translation(self, item, item_type):
        if not isinstance(item, dict):
            return ""
        if item_type == "character":
            return self._normalize_glossary_text(
                item.get("translated_name") or item.get("original_name")
            )
        return self._normalize_glossary_text(item.get("dst") or item.get("src"))

    def _build_legacy_reference_lookup(self):
        lookup = {"term": {}, "character": {}}
        for item in self.config.get("prompt_dictionary_data", []) or []:
            if not self._is_legacy_reference_item(item):
                continue
            self._add_legacy_reference_lookup_item(lookup["term"], item, "term")
        for item in self.config.get("characterization_data", []) or []:
            if not self._is_legacy_reference_item(item):
                continue
            self._add_legacy_reference_lookup_item(lookup["character"], item, "character")
        return lookup

    def _add_legacy_reference_lookup_item(self, lookup, item, item_type):
        if not isinstance(lookup, dict) or not isinstance(item, dict):
            return
        legacy_id = self._normalize_glossary_text(item.get("_legacy_id"))
        translated_name = self._legacy_reference_translation(item, item_type)
        if not legacy_id or not translated_name:
            return
        keys = []
        if item_type == "character":
            keys.extend((
                item.get("original_name"),
                item.get("translated_name"),
            ))
        else:
            keys.extend((
                item.get("src"),
                item.get("dst"),
            ))
        for key in keys:
            text = self._normalize_glossary_text(key)
            if text and text not in lookup:
                lookup[text] = item

    def _looks_like_copied_legacy_seed_mapping(self, item):
        if not isinstance(item, dict):
            return False
        origin = self._normalize_glossary_text(item.get("_glossary_origin"))
        status = self._normalize_glossary_text(item.get("_mapping_status"))
        legacy_id = self._normalize_glossary_text(item.get("_legacy_id"))
        copied_seed_marker = (
            origin == LEGACY_GLOSSARY_ORIGIN
            or status == "seed"
            or legacy_id.startswith("legacy_")
        )
        if not copied_seed_marker:
            return False
        return self._rule_field_has_content(item.get("dst")) or self._rule_field_has_content(item.get("translated_name"))

    def _strip_legacy_metadata(self, item):
        if not isinstance(item, dict):
            return item
        for key in LEGACY_METADATA_FIELDS:
            item.pop(key, None)
        return item

    def _is_reliable_legacy_mapping_result(self, item, item_type):
        if not isinstance(item, dict):
            return False
        if not self._has_legacy_mapping_evidence(item):
            return False
        if item_type == "character":
            return self._rule_field_has_content(item.get("translated_name"))
        return self._rule_field_has_content(item.get("dst"))

    def _has_legacy_mapping_evidence(self, item):
        if not isinstance(item, dict):
            return False
        if self._normalize_glossary_text(item.get("_glossary_origin")) == LEGACY_MAPPED_ORIGIN:
            return True
        if self._normalize_glossary_text(item.get("_mapping_status")) == "mapped":
            return True
        return self._rule_field_has_content(item.get("_mapped_from")) or self._rule_field_has_content(item.get("mapped_from"))

    def _make_legacy_id(self, prefix, item, index):
        base = (
            item.get("src")
            or item.get("original_name")
            or item.get("translated_name")
            or index
        )
        text = self._normalize_glossary_text(base) or str(index)
        checksum = sum(ord(char) for char in text) % 100000
        return f"legacy_{prefix}_{checksum:05d}_{index:04d}"

    def _is_legacy_reference_item(self, item):
        if not isinstance(item, dict):
            return False
        origin = self._normalize_glossary_text(item.get("_glossary_origin"))
        status = self._normalize_glossary_text(item.get("_mapping_status"))
        return origin == LEGACY_GLOSSARY_ORIGIN or status == "seed"

    def _contains_legacy_reference_text(self, value):
        text = self._normalize_glossary_text(value)
        if not text:
            return False
        return self._tr("label_legacy_translation_reference") in text

    def _build_legacy_mapping_context(self):
        glossary = [
            self._clean_legacy_context_item(item, "glossary")
            for item in self.config.get("prompt_dictionary_data", []) or []
            if self._is_legacy_reference_item(item)
        ]
        characters = [
            self._clean_legacy_context_item(item, "character")
            for item in self.config.get("characterization_data", []) or []
            if self._is_legacy_reference_item(item)
        ]
        world_history = [
            self._clean_legacy_text_history_item(item)
            for item in self.config.get("world_building_history", []) or []
            if self._is_legacy_reference_item(item)
        ]
        style_history = [
            self._clean_legacy_text_history_item(item)
            for item in self.config.get("writing_style_history", []) or []
            if self._is_legacy_reference_item(item)
        ]
        world_history = self._dedupe_legacy_text_history(world_history)
        style_history = self._dedupe_legacy_text_history(style_history)
        return self._trim_existing_rule_value({
            "legacy_glossary_terms": [item for item in glossary if item],
            "legacy_character_profiles": [item for item in characters if item],
            "legacy_world_building_history": [item for item in world_history if item],
            "legacy_writing_style_history": [item for item in style_history if item],
        })

    def _clean_legacy_context_item(self, item, item_type):
        if not isinstance(item, dict):
            return {}
        if item_type == "character":
            keys = (
                "_legacy_id", "original_name", "translated_name", "aliases",
                "gender", "age", "personality", "speech_style", "pronouns",
                "speech_quirks", "additional_info", "source", "volume",
            )
        else:
            keys = ("_legacy_id", "src", "dst", "info", "source", "volume")
        cleaned = {
            key: item.get(key)
            for key in keys
            if self._rule_field_has_content(item.get(key))
        }
        if item_type == "character":
            aliases, alias_notes = self._normalize_aliases_with_notes(cleaned.get("aliases"))
            if aliases:
                cleaned["aliases"] = aliases
            else:
                cleaned.pop("aliases", None)
            if alias_notes:
                additional_info = self._normalize_glossary_text(cleaned.get("additional_info"))
                note_text = "；".join(dict.fromkeys(alias_notes))
                cleaned["additional_info"] = self._append_text_block(additional_info, note_text)
        return cleaned

    def _clean_legacy_text_history_item(self, item):
        if not isinstance(item, dict):
            return {}
        return {
            key: item.get(key)
            for key in ("source", "volume", "content", "_legacy_id")
            if self._rule_field_has_content(item.get(key))
        }

    def _dedupe_legacy_text_history(self, items):
        result = []
        seen = set()
        for item in items or []:
            if not isinstance(item, dict):
                continue
            content = self._normalize_glossary_text(item.get("content"))
            if not content:
                continue
            marker = (self._normalize_glossary_text(item.get("source")), content)
            if marker in seen:
                continue
            seen.add(marker)
            result.append(item)
        return result

    def _normalize_volume_number(self, value):
        if value is None or value == "":
            return None
        if isinstance(value, bool):
            return None
        try:
            return int(value)
        except (TypeError, ValueError):
            pass
        match = re.search(r"(?i)(?:vol(?:ume)?|book|v|第)?[\s._\-]*0*(\d{1,4})(?:\s*[卷册集部])?", str(value))
        if match:
            try:
                return int(match.group(1))
            except ValueError:
                return None
        return None

    def _format_volume_label(self, volume):
        normalized = self._normalize_volume_number(volume)
        return f"第{normalized}卷" if normalized is not None else ""

    def _normalize_glossary_info(self, item):
        for key in ("info", "description", "desc", "note", "annotation", "说明", "注释", "备注"):
            if key in item:
                value = item.get(key)
                if value is None:
                    return "null"
                text = str(value).strip()
                return text if text else "null"
        return "null"

    def _format_glossary_info(self, term_type, info):
        term_type = self._normalize_glossary_text(term_type, "专有名词")
        info = self._normalize_glossary_text(info, "null")
        if info.lower() in ("null", "none"):
            return f"{term_type} | null"
        return f"{term_type} | {info}"

    def _append_analysis_translation_instruction(self, system_prompt, target_language):
        instruction = f"""

Additional output requirement:
- Translate extracted terms into "{target_language}" during analysis. Put the translation in the glossary item field "dst".
- "src" is the source-text matching key; "dst" is the replacement value that may be inserted verbatim into translated text.
- "dst" must contain only the final translated surface form. Never output "translation (note)", "translation / note", "translation: note", "translation - note", or any other "translation + explanation" form.
- Never put identity, role labels, character relationships, context notes, disambiguation, jokes, memes, or reader-facing hints in "dst"; put them in "info" or "characterization".
- Qualifiers such as left/right/front/back or numbers may appear in "dst" only when explicitly present in "src". If no pure replacement-safe translation is certain, leave "dst" empty.
- Keep "src" as the original text. Do not put category/type labels into "info".
- "type" is only the category, such as character, place, organization, skill, world setting, item, or term.
- "info" must be a short note/annotation in "{target_language}" that explains the term's role, context, or usage.
- For character profiles, fill "translated_name" and write gender, personality, speech_style, and additional_info in "{target_language}" when known.
- For world_building_content, writing_style_content, and translation_example_data.dst, output "{target_language}" content.
- For exclusion_list_data.markers, keep the original text that must not be translated; write its "info" in "{target_language}".
"""
        return f"{system_prompt.rstrip()}\n{instruction.strip()}\n"

    def _append_legacy_translation_extract_instruction(self, system_prompt, target_language):
        instruction = self._load_system_prompt_fragment(
            "glossary_legacy_extract_zh.txt",
            "glossary_legacy_extract_en.txt",
        ).replace("{target_language}", str(target_language))
        return f"{system_prompt.rstrip()}\n{instruction.strip()}\n"

    def _append_legacy_mapping_instruction(self, system_prompt, legacy_context, target_language):
        context_json = json.dumps(legacy_context, ensure_ascii=False, indent=2)
        instruction = self._load_system_prompt_fragment(
            "glossary_legacy_mapping_zh.txt",
            "glossary_legacy_mapping_en.txt",
        )
        instruction = (
            instruction
            .replace("{target_language}", str(target_language))
            .replace("{legacy_context}", context_json)
        )
        return f"{system_prompt.rstrip()}\n{instruction.strip()}\n"

    def _load_system_prompt_fragment(self, zh_filename, en_filename):
        lang = getattr(self.i18n, "lang", "zh_CN")
        preferred = zh_filename if str(lang).startswith("zh") else en_filename
        fallback = en_filename if preferred == zh_filename else zh_filename
        for filename in (preferred, fallback):
            path = os.path.join(self.PROJECT_ROOT, "Resource", "Prompt", "System", filename)
            if os.path.exists(path):
                with open(path, "r", encoding="utf-8") as reader:
                    return reader.read().strip()
        return ""

    def _clean_analysis_info(self, info, term_type="", category=""):
        info = self._normalize_glossary_text(info, "null")
        if info.lower() in ("", "null", "none"):
            return "null"

        labels = {
            self._normalize_glossary_text(term_type).lower(),
            self._normalize_glossary_text(category).lower(),
            "专有名词", "人名", "人物", "角色", "地名", "地点", "组织", "势力",
            "技能", "能力", "物品", "道具", "世界观", "设定", "术语",
            "character", "person", "place", "location", "organization", "faction",
            "skill", "ability", "item", "world", "setting", "term",
        }
        labels = {label for label in labels if label}
        for splitter in ("|", "｜", ":", "："):
            if splitter not in info:
                continue
            left, right = info.split(splitter, 1)
            if left.strip().lower() in labels:
                cleaned = right.strip()
                return cleaned if cleaned else "null"
        return info

    def _resolve_prompt_file(self, prompt_file=None):
        if prompt_file and os.path.exists(prompt_file):
            return prompt_file

        configured_prompt = self.config.get("glossary_analysis_prompt_file")
        if configured_prompt and os.path.exists(configured_prompt):
            return configured_prompt

        lang = getattr(self.i18n, "lang", "zh_CN")
        default_prompt = "glossary_extract_zh.txt" if str(lang).startswith("zh") else "glossary_extract_en.txt"
        prompt_file = os.path.join(self.PROJECT_ROOT, "Resource", "Prompt", "System", default_prompt)
        if not os.path.exists(prompt_file):
            fallback_prompt = "glossary_extract_en.txt" if default_prompt != "glossary_extract_en.txt" else "glossary_extract_zh.txt"
            prompt_file = os.path.join(self.PROJECT_ROOT, "Resource", "Prompt", "System", fallback_prompt)
        return prompt_file

    def _get_split_batch_size(self):
        try:
            batch_size = int(self.config.get("glossary_analysis_split_lines") or 0)
        except (TypeError, ValueError):
            batch_size = 0

        if batch_size <= 0:
            try:
                batch_size = int(self.config.get("lines_limit") or 20)
            except (TypeError, ValueError):
                batch_size = 20

        return max(1, batch_size)

    def _resolve_incremental_split_target_tokens(self, value=None):
        if value is None:
            value = self.config.get("glossary_analysis_incremental_split_target_tokens")
        try:
            target_tokens = int(value or 0)
        except (TypeError, ValueError):
            target_tokens = 0
        if target_tokens <= 0:
            target_tokens = DEFAULT_INCREMENTAL_SPLIT_TARGET_TOKENS
        return min(target_tokens, MAX_INCREMENTAL_SPLIT_TARGET_TOKENS)

    def _split_items_by_estimated_tokens(self, items, target_tokens):
        batches = []
        current_batch = []
        current_tokens = 0
        target_tokens = self._resolve_incremental_split_target_tokens(target_tokens)

        for item in items or []:
            text = getattr(item, "source_text", "")
            item_tokens = max(1, self._estimate_token_count(text))
            if item_tokens > target_tokens:
                if current_batch:
                    batches.append(current_batch)
                    current_batch = []
                    current_tokens = 0
                sentence_chunks = self._split_text_by_sentence_boundaries(text, target_tokens)
                if len(sentence_chunks) > 1:
                    for chunk in sentence_chunks:
                        batches.append([self._clone_cache_item_with_text(item, chunk)])
                    continue
            if current_batch and current_tokens + item_tokens > target_tokens:
                batches.append(current_batch)
                current_batch = []
                current_tokens = 0
            current_batch.append(item)
            current_tokens += item_tokens

        if current_batch:
            batches.append(current_batch)
        return batches or [items]

    def _split_text_by_sentence_boundaries(self, text, target_tokens):
        text = self._normalize_glossary_text(text)
        if not text:
            return []

        sentences = re.findall(r".+?(?:[。！？!?\.]+[”’\"']?|\n+|$)", text, flags=re.S)
        sentences = [sentence for sentence in (s.strip() for s in sentences) if sentence]
        if len(sentences) <= 1:
            return [text]

        chunks = []
        current = []
        current_tokens = 0
        target_tokens = self._resolve_incremental_split_target_tokens(target_tokens)

        for sentence in sentences:
            sentence_tokens = max(1, self._estimate_token_count(sentence))
            if current and current_tokens + sentence_tokens > target_tokens:
                chunks.append("\n".join(current))
                current = []
                current_tokens = 0
            current.append(sentence)
            current_tokens += sentence_tokens

        if current:
            chunks.append("\n".join(current))
        return chunks or [text]

    def _clone_cache_item_with_text(self, item, text):
        class TextOnlyItem:
            def __init__(self, source_text):
                self.source_text = source_text

        try:
            clone = item.__class__.__new__(item.__class__)
            if hasattr(item, "__dict__"):
                clone.__dict__.update(item.__dict__)
            setattr(clone, "source_text", text)
            return clone
        except Exception:
            return TextOnlyItem(text)

    def _estimate_token_count(self, text):
        try:
            from ModuleFolders.Infrastructure.Cache.CacheItem import CacheItem
            return CacheItem.get_token_count(text)
        except Exception:
            if not text:
                return 0
            ascii_count = sum(1 for c in text if ord(c) < 128)
            non_ascii_count = len(text) - ascii_count
            return max(1, int(ascii_count / 4 + non_ascii_count / 1.5))

    def _calculate_term_frequency(self, terms, source_text=None):
        """计算词频统计"""
        freq = {}
        for term in terms:
            src = term.get('src', '').strip()
            if not src:
                continue

            count = self._count_term_occurrences(source_text, src) if source_text else 1
            count = max(1, count)

            if src in freq:
                freq[src]['count'] = max(freq[src]['count'], count)
                if not freq[src].get('dst') and term.get('dst'):
                    freq[src]['dst'] = term.get('dst')
                if freq[src].get('info') in ("", "null") and term.get('info') not in ("", None, "null"):
                    freq[src]['info'] = term.get('info')
                for meta_key in LEGACY_METADATA_FIELDS:
                    if meta_key not in freq[src] and self._rule_field_has_content(term.get(meta_key)):
                        freq[src][meta_key] = term.get(meta_key)
            else:
                freq[src] = {
                    'count': count,
                    'type': term.get('type', '专有名词'),
                    'category': term.get('category', ''),
                    'dst': term.get('dst', ''),
                    'info': term.get('info', 'null')
                }
                for meta_key in LEGACY_METADATA_FIELDS:
                    if self._rule_field_has_content(term.get(meta_key)):
                        freq[src][meta_key] = term.get(meta_key)

        # 按词频排序
        sorted_freq = dict(sorted(freq.items(), key=lambda x: x[1]['count'], reverse=True))
        return sorted_freq

    def _count_term_occurrences(self, text, term):
        if not text or not term:
            return 0
        return text.count(term)

    def _generate_glossary_json(self, filtered_terms):
        """生成标准术语表JSON格式"""
        glossary = []
        for term, data in filtered_terms.items():
            item = {
                "src": term,
                "dst": self._normalize_glossary_text(data.get('dst')),
                "info": self._clean_analysis_info(data.get('info'), data.get('type'), data.get('category'))
            }
            for meta_key in ("source", "volume", "updated_in", "updated_volume", "history", *LEGACY_METADATA_FIELDS):
                if meta_key in data:
                    item[meta_key] = data.get(meta_key)
            glossary.append(item)
        return glossary

    def _save_glossary_analysis_log(
        self,
        log_path,
        input_path,
        percent,
        lines,
        all_terms,
        filtered,
        threshold,
        analysis_mode="full",
        estimated_tokens=0,
        prompt_file="",
        structured_rules=None,
        incremental_options=None,
        legacy_mode="normal_extract",
        raw_response_diagnostics=None,
    ):
        """保存分析日志文件"""
        range_str = (
            self._tr("glossary_log_range_lines", "前{}行", lines)
            if lines
            else self._tr("glossary_log_range_percent", "前{}%", percent)
        )
        mode_label = (
            self._tr("glossary_log_mode_full", "全本/按比例提取（推荐）")
            if analysis_mode == "full"
            else (
                self._tr("glossary_log_mode_incremental_split", "超长增量分批")
                if analysis_mode == "incremental_split"
                else self._tr("glossary_log_mode_split", "拆分提取（不推荐）")
            )
        )
        prompt_label = prompt_file or self._tr("glossary_log_default_prompt", "默认")

        log_lines = [
            f"=== {self._tr('glossary_log_title', 'AI术语表分析日志')} ===",
            f"{self._tr('glossary_log_analysis_time', '分析时间')}: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}",
            f"{self._tr('glossary_log_analysis_file', '分析文件')}: {os.path.basename(input_path)}",
            f"{self._tr('glossary_log_analysis_range', '分析范围')}: {range_str}",
            f"{self._tr('glossary_log_analysis_mode', '分析模式')}: {mode_label}",
            f"{self._tr('glossary_log_estimated_tokens', '预估Token')}: {estimated_tokens}",
            f"{self._tr('glossary_log_prompt_file', '提示词文件')}: {prompt_label}",
        ]
        legacy_mode = self.normalize_legacy_mode(legacy_mode)
        if legacy_mode != "normal_extract":
            legacy_mode_label = self._tr(
                f"glossary_log_legacy_mode_{legacy_mode}",
                legacy_mode,
            )
            log_lines.append(
                f"{self._tr('glossary_log_legacy_mode')}: {legacy_mode_label}"
            )
        if incremental_options and incremental_options.get("enabled"):
            modes = []
            if incremental_options.get("new"):
                modes.append("new")
            if incremental_options.get("replace"):
                modes.append("replace")
            log_lines.extend([
                f"{self._tr('glossary_log_incremental_mode', '增量模式')}: {'/'.join(modes) or '-'}",
                f"{self._tr('glossary_log_incremental_source', '来源标签')}: {incremental_options.get('source_label') or '-'}",
                f"{self._tr('glossary_log_incremental_volume', '来源卷号')}: {incremental_options.get('source_volume') if incremental_options.get('source_volume') is not None else '-'}",
            ])
        log_lines.extend([
            "",
            f"【{self._tr('glossary_log_notice_title', '重要提示')}】",
            self._tr(
                "glossary_log_notice",
                "分析结果的准确程度取决于您使用的API模型能力，此功能仅提供初步分析结果。建议人工审核后再使用，不建议直接作为最终术语表。"
            ),
            "",
            f"=== {self._tr('glossary_log_term_frequency_title', '词频统计')} ===",
        ])
        for term, data in all_terms.items():
            type_info = self._format_glossary_info(data.get('type'), data.get('info'))
            log_lines.append(self._tr("glossary_log_term_line", "{} ({}): 出现 {} 次", term, type_info, data['count']))

        log_lines.extend([
            "",
            f"=== {self._tr('glossary_log_filter_title', '过滤设置')} ===",
            self._tr("glossary_log_min_frequency", "最低词频阈值: {}次", threshold),
            self._tr("glossary_log_total_before_filter", "过滤前总数: {}", len(all_terms)),
            self._tr("glossary_log_total_after_filter", "过滤后总数: {}", len(filtered)),
        ])

        if structured_rules:
            log_lines.extend([
                "",
                f"=== {self._tr('glossary_log_structured_rules_title', '分类规则统计')} ===",
                self._tr("glossary_log_structured_glossary_count", "术语表: {}", len(structured_rules.get('prompt_dictionary_data', []))),
                self._tr("glossary_log_structured_exclusion_count", "禁翻表: {}", len(structured_rules.get('exclusion_list_data', []))),
                self._tr("glossary_log_structured_character_count", "角色设定: {}", len(structured_rules.get('characterization_data', []))),
                self._tr("glossary_log_structured_world_chars", "世界观设定: {} 字符", len(structured_rules.get('world_building_content', ''))),
                self._tr("glossary_log_structured_style_chars", "文风要求: {} 字符", len(structured_rules.get('writing_style_content', ''))),
                self._tr("glossary_log_structured_example_count", "翻译示例: {}", len(structured_rules.get('translation_example_data', []))),
            ])

        diagnostics = [item for item in raw_response_diagnostics or [] if item and item.get("preview")]
        if diagnostics:
            log_lines.extend([
                "",
                f"=== {self._tr('glossary_log_parse_diagnostics_title', '解析诊断')} ===",
                self._tr(
                    "glossary_log_parse_diagnostics_notice",
                    "以下批次有模型响应，但未解析到术语/角色/世界观等结构化内容。下方仅保留响应预览，完整排查请重新运行或查看原始响应诊断文件。"
                ),
            ])
            for item in diagnostics:
                log_lines.extend([
                    "",
                    f"--- batch: {item.get('batch')} | tokens: {item.get('prompt_tokens', 0)}+{item.get('completion_tokens', 0)}T ---",
                    str(item.get("preview") or "")[:2000],
                ])

        with open(log_path, 'w', encoding='utf-8') as f:
            f.write("\n".join(log_lines) + "\n")
