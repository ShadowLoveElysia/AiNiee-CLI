"""
LLM task for generating AI proofread suggestions.
"""

from __future__ import annotations

import time

from ModuleFolders.Base.Base import Base
from ModuleFolders.Infrastructure.LLMRequester.LLMRequester import LLMRequester
from ModuleFolders.Infrastructure.RequestLimiter.RequestLimiter import RequestLimiter
from ModuleFolders.Infrastructure.TaskConfig.TaskConfig import TaskConfig
from ModuleFolders.Infrastructure.Tokener.Tokener import Tokener
from ModuleFolders.Service.Proofreader.ProofreadSuggestion import (
    ProofreadBatch,
    ProofreadSuggestionParseResult,
    build_suggestion_prompt,
    parse_suggestion_response,
)


class ProofreadSuggestionTask(Base):
    def __init__(
        self,
        config: TaskConfig,
        request_limiter: RequestLimiter,
        batch: ProofreadBatch,
        glossary: list[dict] | None = None,
        context_lines: list[dict] | None = None,
        suggestion_mode: str = "proofread",
    ) -> None:
        super().__init__()
        self.config = config
        self.request_limiter = request_limiter
        self.batch = batch
        self.glossary = glossary or []
        self.context_lines = context_lines or []
        self.suggestion_mode = suggestion_mode
        self.system_prompt = ""
        self.messages: list[dict[str, str]] = []
        self.task_id = batch.batch_id

    def prepare(self) -> None:
        prompt = build_suggestion_prompt(
            self.batch,
            self.glossary,
            self.context_lines,
            suggestion_mode=self.suggestion_mode,
        )
        self.messages = [{"role": "user", "content": prompt}]

    def run(self) -> dict:
        if not any(line.allow_suggestion for line in self.batch.lines):
            return self._skip_result()

        if not self.messages:
            self.prepare()

        request_tokens_consume = Tokener().calculate_tokens(self.messages, self.system_prompt) or 0
        wait_start_time = time.time()
        while True:
            if Base.work_status == Base.STATUS.STOPING:
                return self._skip_result()
            if self.request_limiter.check_limiter(request_tokens_consume):
                break
            if time.time() - wait_start_time > 600:
                return self._skip_result()
            time.sleep(0.1)

        try:
            platform_config = self.config.get_platform_configuration("translationReq")
            requester = LLMRequester()
            skip, response_think, response_content, prompt_tokens, completion_tokens = requester.sent_request(
                messages=self.messages,
                system_prompt=self.system_prompt,
                platform_config=platform_config,
            )
            if skip:
                return self._skip_result()

            parsed = parse_suggestion_response(
                response_content,
                self.batch,
                suggestion_mode=self.suggestion_mode,
            )
            result = {
                "skip": False,
                "prompt_tokens": prompt_tokens,
                "completion_tokens": completion_tokens,
                "result": parsed,
            }
            if bool(getattr(self.config, "proofread_save_raw_responses", False)):
                result["raw_response"] = {
                    "batch_id": self.batch.batch_id,
                    "batch_hash": self.batch.batch_hash,
                    "suggestion_mode": self.suggestion_mode,
                    "response_think": response_think,
                    "response_content": response_content,
                    "prompt_tokens": prompt_tokens,
                    "completion_tokens": completion_tokens,
                }
            return result
        except Exception as exc:
            self.print(f"[{Base.tra('proofread_suggestion_error_prefix')}] {exc}")
            return self._skip_result()

    def _skip_result(self) -> dict:
        return {
            "skip": True,
            "prompt_tokens": 0,
            "completion_tokens": 0,
            "result": ProofreadSuggestionParseResult(
                batch_id=self.batch.batch_id,
                batch_hash=self.batch.batch_hash,
                closed_without_suggestions=False,
                suggestions=[],
            ),
        }
