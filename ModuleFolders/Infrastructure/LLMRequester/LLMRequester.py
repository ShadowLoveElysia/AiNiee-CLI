class LLMRequester:
    def __init__(self) -> None:
        pass

    @staticmethod
    def _classify_failure(response_think: str, response_content: str) -> str:
        """Classify failure reason for adaptive retry policy."""
        status = (response_think or "").lower()
        detail = (response_content or "").lower()
        text = f"{status} {detail}"

        if "stopped" in text:
            return "stopped"
        if any(k in text for k in ("api key", "invalid key", "unauthorized", "forbidden", "permission")):
            return "auth"
        if any(k in text for k in ("rate limit", "too many requests", "429", "quota", "throttle")):
            return "rate_limit"
        if any(k in text for k in ("timeout", "timed out", "read timeout", "connect timeout")):
            return "timeout"
        if any(k in text for k in ("connection", "dns", "network", "temporarily unavailable", "reset by peer")):
            return "network"
        if any(k in text for k in ("500", "502", "503", "504", "server error", "bad gateway", "gateway timeout")):
            return "server"
        if any(k in text for k in ("empty response", "invalid json", "parse", "format", "schema")):
            return "content"
        return "generic"

    @staticmethod
    def _retry_policy(failure_type: str, retry_enabled: bool) -> tuple[int, float, float]:
        """Return (max_retries, initial_delay, multiplier)."""
        if not retry_enabled:
            return (1, 0.0, 1.0)

        policies = {
            "auth": (1, 0.0, 1.0),
            "stopped": (1, 0.0, 1.0),
            "rate_limit": (5, 2.0, 2.5),
            "timeout": (4, 1.5, 2.0),
            "network": (4, 1.0, 2.0),
            "server": (4, 1.0, 2.0),
            "content": (2, 0.8, 1.5),
            "generic": (3, 2.0, 2.0),
        }
        return policies.get(failure_type, policies["generic"])

    @staticmethod
    def _clone_messages(messages: list[dict]) -> list[dict]:
        """Clone message list to avoid in-place mutations across retries."""
        if not messages:
            return []
        return [message.copy() if isinstance(message, dict) else message for message in messages]

    # Dispatch request
    def sent_request(self, messages: list[dict], system_prompt: str, platform_config: dict) -> tuple[bool, str, str, int, int]:
        from ModuleFolders.Base.Base import Base
        from ModuleFolders.Infrastructure.LLMRequester.SdkRequestMode import is_openai_sdk_mode

        config = Base().load_config()

        configured_retries = int(config.get("retry_count", 3) or 3)
        retry_enabled = config.get("enable_retry_backoff", True)
        max_retries = configured_retries if retry_enabled else 1
        current_retry = 0
        base_messages = self._clone_messages(messages)

        skip = True
        response_think = "API_FAIL"
        response_content = ""
        prompt_tokens = 0
        completion_tokens = 0

        while current_retry < max_retries:
            if Base.work_status == Base.STATUS.STOPING or not Base.is_task_session_active():
                return True, "STOPPED", "Task stopped by user", 0, 0

            target_platform = platform_config.get("target_platform")
            api_format = platform_config.get("api_format")
            request_messages = self._clone_messages(base_messages)

            if target_platform == "sakura":
                from ModuleFolders.Infrastructure.LLMRequester.SakuraRequester import SakuraRequester

                sakura_requester = SakuraRequester()
                skip, response_think, response_content, prompt_tokens, completion_tokens = sakura_requester.request_sakura(
                    request_messages,
                    system_prompt,
                    platform_config,
                )
            elif target_platform == "murasaki":
                from ModuleFolders.Infrastructure.LLMRequester.MurasakiRequester import MurasakiRequester

                murasaki_requester = MurasakiRequester()
                skip, response_think, response_content, prompt_tokens, completion_tokens = murasaki_requester.request_murasaki(
                    request_messages,
                    system_prompt,
                    platform_config,
                )
            elif target_platform == "LocalLLM":
                from ModuleFolders.Infrastructure.LLMRequester.LocalLLMRequester import LocalLLMRequester

                local_llm_requester = LocalLLMRequester()
                skip, response_think, response_content, prompt_tokens, completion_tokens = local_llm_requester.request_LocalLLM(
                    request_messages,
                    system_prompt,
                    platform_config,
                )
            elif target_platform == "cohere":
                from ModuleFolders.Infrastructure.LLMRequester.CohereRequester import CohereRequester

                cohere_requester = CohereRequester()
                skip, response_think, response_content, prompt_tokens, completion_tokens = cohere_requester.request_cohere(
                    request_messages,
                    system_prompt,
                    platform_config,
                )
            elif (target_platform == "google" or (target_platform.startswith("custom_platform_") and api_format == "Google")) and not is_openai_sdk_mode(platform_config):
                from ModuleFolders.Infrastructure.LLMRequester.GoogleRequester import GoogleRequester

                google_requester = GoogleRequester()
                skip, response_think, response_content, prompt_tokens, completion_tokens = google_requester.request_google(
                    request_messages,
                    system_prompt,
                    platform_config,
                )
            elif target_platform == "anthropic" or (target_platform.startswith("custom_platform_") and api_format == "Anthropic"):
                from ModuleFolders.Infrastructure.LLMRequester.AnthropicRequester import AnthropicRequester

                anthropic_requester = AnthropicRequester()
                skip, response_think, response_content, prompt_tokens, completion_tokens = anthropic_requester.request_anthropic(
                    request_messages,
                    system_prompt,
                    platform_config,
                )
            elif target_platform == "amazonbedrock":
                from ModuleFolders.Infrastructure.LLMRequester.AmazonbedrockRequester import AmazonbedrockRequester

                amazonbedrock_requester = AmazonbedrockRequester()
                skip, response_think, response_content, prompt_tokens, completion_tokens = amazonbedrock_requester.request_amazonbedrock(
                    request_messages,
                    system_prompt,
                    platform_config,
                )
            elif target_platform == "dashscope":
                from ModuleFolders.Infrastructure.LLMRequester.DashscopeRequester import DashscopeRequester

                dashscope_requester = DashscopeRequester()
                skip, response_think, response_content, prompt_tokens, completion_tokens = dashscope_requester.request_openai(
                    request_messages,
                    system_prompt,
                    platform_config,
                )
            else:
                from ModuleFolders.Infrastructure.LLMRequester.OpenaiRequester import OpenaiRequester

                openai_requester = OpenaiRequester()
                skip, response_think, response_content, prompt_tokens, completion_tokens = openai_requester.request_openai(
                    request_messages,
                    system_prompt,
                    platform_config,
                )

            if not Base.is_task_session_active():
                return True, "STOPPED", "Task stopped by user", 0, 0

            if not skip:
                return skip, response_think, response_content, prompt_tokens, completion_tokens

            current_retry += 1
            failure_type = self._classify_failure(response_think, response_content)
            policy_max_retries, initial_delay, delay_multiplier = self._retry_policy(failure_type, retry_enabled)
            effective_max_retries = max(1, min(max_retries, policy_max_retries))

            if current_retry < effective_max_retries:
                if Base.work_status == Base.STATUS.STOPING or not Base.is_task_session_active():
                    return True, "STOPPED", "Task stopped by user", 0, 0

                import time
                from rich import print

                backoff_delay = initial_delay * (delay_multiplier ** (current_retry - 1))
                if not Base.should_suppress_task_output():
                    print(
                        f"[[yellow]RETRY-{failure_type.upper()}[/]] Request failed. "
                        f"Retrying in {backoff_delay:.1f}s... ({current_retry}/{effective_max_retries-1})"
                    )
                time.sleep(backoff_delay)
            else:
                break

        return skip, response_think, response_content, prompt_tokens, completion_tokens
