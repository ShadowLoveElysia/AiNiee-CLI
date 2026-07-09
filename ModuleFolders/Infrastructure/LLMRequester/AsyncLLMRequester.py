"""
异步 LLM 请求分发器 - 统一的异步请求入口

支持平台：
- OpenAI 及兼容 API
- Anthropic protocol
- Google Gemini
- Amazon Bedrock
- Cohere
- 本地模型 (LocalLLM, Sakura)
"""

import asyncio
import contextvars
import json
from typing import Tuple, Optional

import aiohttp

from ModuleFolders.Base.Base import Base
from ModuleFolders.Infrastructure.LLMRequester.AsyncOpenaiRequester import AsyncOpenaiRequester
from ModuleFolders.Infrastructure.LLMRequester.ErrorClassifier import ErrorClassifier, ErrorType
from ModuleFolders.Infrastructure.LLMRequester.AsyncSignalHub import get_signal_hub
from ModuleFolders.Infrastructure.LLMRequester.SdkRequestMode import is_anthropic_sdk_mode, is_openai_sdk_mode


class AsyncLLMRequester(Base):
    """异步 LLM 请求分发器"""

    # 全局连接池
    _session: Optional[aiohttp.ClientSession] = None
    _session_lock = asyncio.Lock()
    _current_limit: int = 0  # 记录当前连接池限制

    def __init__(self) -> None:
        super().__init__()

    @classmethod
    async def get_session(cls, max_connections: int = 100) -> aiohttp.ClientSession:
        """
        获取全局 aiohttp 会话

        Args:
            max_connections: 最大连接数，应与用户设置的线程数一致

        Note:
            连接池限制是为了保护本地系统资源（文件描述符、端口数），
            而非API限速。即使API没有429限制，本地系统也有上限。
            这确保高并发是"稳态暴力"而非"自杀式冲击"。
        """
        # 如果连接池限制变化，需要重建会话
        need_rebuild = (
            cls._session is None or
            cls._session.closed or
            cls._current_limit != max_connections
        )

        if need_rebuild:
            async with cls._session_lock:
                if cls._session and not cls._session.closed and cls._current_limit == max_connections:
                    return cls._session

                # 关闭旧会话
                if cls._session and not cls._session.closed:
                    await cls._session.close()

                # 创建新会话，连接数与用户设置的线程数关联
                connector = aiohttp.TCPConnector(
                    limit=max_connections * 2,  # 总连接数 = 线程数 * 2（留有余量）
                    limit_per_host=max_connections,  # 每主机连接数 = 线程数
                    ttl_dns_cache=300,
                    enable_cleanup_closed=True,
                )
                timeout = aiohttp.ClientTimeout(total=300, connect=30, sock_read=120)
                cls._session = aiohttp.ClientSession(connector=connector, timeout=timeout)
                cls._current_limit = max_connections

        return cls._session

    @classmethod
    async def close_session(cls) -> None:
        """关闭全局会话"""
        if cls._session and not cls._session.closed:
            await cls._session.close()
            cls._session = None

    async def send_request_async(
        self,
        messages: list,
        system_prompt: str,
        platform_config: dict
    ) -> Tuple[bool, str, str, int, int]:
        """
        异步分发请求到对应平台

        Returns:
            tuple: (skip, think, content, prompt_tokens, completion_tokens)
        """
        config = self.load_config()
        max_retries = 3 if config.get("enable_retry_backoff", True) else 1
        current_retry = 0
        backoff_delay = 2
        signal_hub = get_signal_hub()

        while current_retry < max_retries:
            # 检查停止信号
            if (
                Base.work_status == Base.STATUS.STOPING
                or await signal_hub.check_stop()
                or not Base.is_task_session_active()
            ):
                return True, "STOPPED", "Task stopped by user", 0, 0

            # 检查暂停信号
            await signal_hub.wait_if_paused()

            target_platform = platform_config.get("target_platform")
            api_format = platform_config.get("api_format")

            try:
                # 根据平台分发请求
                if target_platform == "sakura":
                    result = await self._request_sakura_async(messages, system_prompt, platform_config)
                elif target_platform == "murasaki":
                    result = await self._request_sakura_async(messages, system_prompt, platform_config)
                elif target_platform == "LocalLLM":
                    result = await self._request_local_async(messages, system_prompt, platform_config)
                elif (target_platform == "google" or (target_platform.startswith("custom_platform_") and api_format == "Google")) and not is_openai_sdk_mode(platform_config):
                    result = await self._request_google_async(messages, system_prompt, platform_config)
                elif target_platform == "anthropic" or (target_platform.startswith("custom_platform_") and api_format == "Anthropic"):
                    result = await self._request_anthropic_async(messages, system_prompt, platform_config)
                else:
                    # OpenAI 及兼容 API
                    requester = AsyncOpenaiRequester()
                    result = await requester.request_openai_async(messages, system_prompt, platform_config)

                skip, think, content, pt, ct = result
                if not Base.is_task_session_active():
                    return True, "STOPPED", "Task stopped by user", 0, 0
                if not skip:
                    return result

                # 检查错误类型决定是否重试
                error_type, _ = ErrorClassifier.classify(content)
                if error_type == ErrorType.HARD_ERROR:
                    # 硬伤错误不重试
                    return result

            except Exception as e:
                if Base.work_status == Base.STATUS.STOPING or not Base.is_task_session_active():
                    return True, "STOPPED", "Task stopped by user", 0, 0

                error_str = str(e)
                error_type, _ = ErrorClassifier.classify(error_str)

                if error_type == ErrorType.HARD_ERROR:
                    # 硬伤错误不重试
                    return True, "HARD_ERROR", error_str, 0, 0

                result = (True, "SOFT_ERROR", error_str, 0, 0)

            current_retry += 1
            if current_retry < max_retries:
                if Base.work_status == Base.STATUS.STOPING or not Base.is_task_session_active():
                    return True, "STOPPED", "Task stopped by user", 0, 0
                self.print(f"[[yellow]RETRY[/]] Async request failed. Retrying in {backoff_delay}s... ({current_retry}/{max_retries-1})")
                await asyncio.sleep(backoff_delay)
                backoff_delay *= 2

        return result

    async def _request_anthropic_async(
        self,
        messages: list,
        system_prompt: str,
        platform_config: dict
    ) -> Tuple[bool, str, str, int, int]:
        """异步 Anthropic 请求"""
        try:
            if Base.work_status == Base.STATUS.STOPING or not Base.is_task_session_active():
                return True, "STOPPED", "Task stopped by user", 0, 0

            if is_anthropic_sdk_mode(platform_config):
                from ModuleFolders.Infrastructure.LLMRequester.AnthropicRequester import AnthropicRequester

                loop = asyncio.get_event_loop()
                requester = AnthropicRequester()
                context = contextvars.copy_context()
                result = await loop.run_in_executor(
                    None,
                    context.run,
                    requester.request_anthropic,
                    messages,
                    system_prompt,
                    platform_config,
                )
                if not Base.is_task_session_active():
                    return True, "STOPPED", "Task stopped by user", 0, 0
                return result

            model_name = platform_config.get("model_name")
            api_url = platform_config.get("api_url", "https://api.anthropic.com").rstrip('/')
            api_key = platform_config.get("api_key")
            request_timeout = platform_config.get("request_timeout", 120)
            temperature = platform_config.get("temperature", 1.0)
            max_tokens = platform_config.get("max_tokens", 4096)
            think_switch = platform_config.get("think_switch", False)
            think_budget = platform_config.get("think_budget", 10000)

            # 构建请求体
            request_body = {
                "model": model_name,
                "max_tokens": max_tokens,
                "messages": messages,
            }

            if system_prompt:
                request_body["system"] = system_prompt

            if temperature != 1:
                request_body["temperature"] = temperature

            if think_switch:
                request_body["thinking"] = {
                    "type": "enabled",
                    "budget_tokens": think_budget
                }

            headers = {
                "x-api-key": api_key,
                "anthropic-version": "2023-06-01",
                "Content-Type": "application/json",
            }

            if not api_url.endswith('/messages'):
                api_url = f"{api_url}/v1/messages"

            session = await self.get_session()
            timeout = aiohttp.ClientTimeout(total=request_timeout)

            async with session.post(api_url, json=request_body, headers=headers, timeout=timeout) as resp:
                if not Base.is_task_session_active():
                    return True, "STOPPED", "Task stopped by user", 0, 0

                if resp.status != 200:
                    error_text = await resp.text()
                    raise Exception(f"HTTP {resp.status}: {error_text}")

                response_json = await resp.json()

            if not Base.is_task_session_active():
                return True, "STOPPED", "Task stopped by user", 0, 0

            # 解析响应
            content_blocks = response_json.get("content", [])
            response_content = ""
            response_think = ""

            for block in content_blocks:
                if block.get("type") == "text":
                    response_content += block.get("text", "")
                elif block.get("type") == "thinking":
                    response_think += block.get("thinking", "")

            usage = response_json.get("usage", {})
            prompt_tokens = usage.get("input_tokens", 0)
            completion_tokens = usage.get("output_tokens", 0)

            return False, response_think, response_content, prompt_tokens, completion_tokens

        except Exception as e:
            if Base.work_status == Base.STATUS.STOPING or not Base.is_task_session_active():
                return True, "STOPPED", "Task stopped by user", 0, 0
            error_str = str(e)
            error_type, _ = ErrorClassifier.classify(error_str)
            return True, error_type.value.upper(), error_str, 0, 0

    async def _request_google_async(
        self,
        messages: list,
        system_prompt: str,
        platform_config: dict
    ) -> Tuple[bool, str, str, int, int]:
        """异步 Google Gemini 请求"""
        try:
            if Base.work_status == Base.STATUS.STOPING or not Base.is_task_session_active():
                return True, "STOPPED", "Task stopped by user", 0, 0

            model_name = platform_config.get("model_name")
            api_key = platform_config.get("api_key")
            request_timeout = platform_config.get("request_timeout", 120)
            temperature = platform_config.get("temperature", 1.0)

            # 构建 Gemini API URL
            api_url = f"https://generativelanguage.googleapis.com/v1beta/models/{model_name}:generateContent?key={api_key}"

            # 转换消息格式
            contents = []
            for msg in messages:
                role = "user" if msg["role"] == "user" else "model"
                contents.append({
                    "role": role,
                    "parts": [{"text": msg["content"]}]
                })

            request_body = {
                "contents": contents,
                "generationConfig": {
                    "temperature": temperature,
                }
            }

            if system_prompt:
                request_body["systemInstruction"] = {
                    "parts": [{"text": system_prompt}]
                }

            headers = {"Content-Type": "application/json"}

            session = await self.get_session()
            timeout = aiohttp.ClientTimeout(total=request_timeout)

            async with session.post(api_url, json=request_body, headers=headers, timeout=timeout) as resp:
                if not Base.is_task_session_active():
                    return True, "STOPPED", "Task stopped by user", 0, 0

                if resp.status != 200:
                    error_text = await resp.text()
                    raise Exception(f"HTTP {resp.status}: {error_text}")

                response_json = await resp.json()

            if not Base.is_task_session_active():
                return True, "STOPPED", "Task stopped by user", 0, 0

            # 解析响应
            candidates = response_json.get("candidates", [])
            if not candidates:
                raise Exception("No candidates in response")

            content = candidates[0].get("content", {})
            parts = content.get("parts", [])
            response_content = "".join(p.get("text", "") for p in parts)

            usage = response_json.get("usageMetadata", {})
            prompt_tokens = usage.get("promptTokenCount", 0)
            completion_tokens = usage.get("candidatesTokenCount", 0)

            return False, "", response_content, prompt_tokens, completion_tokens

        except Exception as e:
            if Base.work_status == Base.STATUS.STOPING or not Base.is_task_session_active():
                return True, "STOPPED", "Task stopped by user", 0, 0
            error_str = str(e)
            error_type, _ = ErrorClassifier.classify(error_str)
            return True, error_type.value.upper(), error_str, 0, 0

    async def _request_sakura_async(
        self,
        messages: list,
        system_prompt: str,
        platform_config: dict
    ) -> Tuple[bool, str, str, int, int]:
        """异步 Sakura 本地模型请求"""
        # Sakura 使用 OpenAI 兼容格式
        requester = AsyncOpenaiRequester()
        return await requester.request_openai_async(messages, system_prompt, platform_config)

    async def _request_local_async(
        self,
        messages: list,
        system_prompt: str,
        platform_config: dict
    ) -> Tuple[bool, str, str, int, int]:
        """异步本地 LLM 请求"""
        # LocalLLM 使用 OpenAI 兼容格式
        requester = AsyncOpenaiRequester()
        return await requester.request_openai_async(messages, system_prompt, platform_config)
