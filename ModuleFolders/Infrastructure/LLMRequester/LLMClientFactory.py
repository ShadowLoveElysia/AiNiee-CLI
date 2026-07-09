# LLMClientFactory.py
import threading
from typing import Dict, Any
import httpx
import json
from urllib.parse import urlparse, urlunparse


def create_httpx_client(
        http2=False,
        max_connections=256,
        max_keepalive_connections=128,
        keepalive_expiry=30,
        trust_env=True,
        **kwargs
):
    """
    创建配置好的HTTP客户端
    """
    # 提取并处理 headers
    headers = kwargs.pop("headers", {})
    
    # 完善浏览器特征
    headers.setdefault("User-Agent", "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36")
    headers.setdefault("Accept", "application/json, text/plain, */*")
    headers.setdefault("Accept-Language", "zh-CN,zh;q=0.9,en;q=0.8")
    
    # 动态推断 Origin 和 Referer
    api_url = kwargs.get("base_url") or headers.get("api_url")
    if api_url:
        try:
            from urllib.parse import urlparse
            parsed = urlparse(str(api_url))
            origin = f"{parsed.scheme}://{parsed.netloc}"
            headers.setdefault("Origin", origin)
            headers.setdefault("Referer", f"{origin}/")
        except: pass

    return httpx.Client(
        http2=http2,
        headers=headers,
        trust_env=trust_env,
        limits=httpx.Limits(
            max_connections=max_connections,
            max_keepalive_connections=max_keepalive_connections,
            keepalive_expiry=keepalive_expiry
        ),
        **kwargs
    )


class LLMClientFactory:
    """LLM客户端工厂 - 集中管理和缓存不同类型的LLM客户端"""

    _instance = None
    _lock = threading.RLock()

    def __new__(cls):
        with cls._lock:
            if cls._instance is None:
                cls._instance = super(LLMClientFactory, cls).__new__(cls)
                cls._instance._clients = {}
            return cls._instance

    def _get_browser_headers(self) -> Dict[str, str]:
        """获取用于伪装的通用浏览器头"""
        return {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36",
            "Accept": "application/json",
            "X-Requested-With": "XMLHttpRequest"
        }

    def get_openai_client(self, config: Dict[str, Any]) -> Any:
        """获取OpenAI客户端"""
        # 展示需要到的配置项
        api_key = config.get("api_key")
        api_url = config.get("api_url")
        key = ("openai", api_url, api_key)
        return self._get_cached_client(key, lambda: self._create_openai_client(config, api_key))

    def get_openai_client_local(self, config: Dict[str, Any]) -> Any:
        """获取OpenAI客户端"""
        api_key = config.get("api_key")
        if not api_key:
            api_key = "none_api_key"
        api_url = config.get("api_url")
        key = ("openai_local", api_url, api_key)
        return self._get_cached_client(key, lambda: self._create_openai_client(config, api_key, trust_env=False))

    def get_openai_client_sakura(self, config: Dict[str, Any]) -> Any:
        """获取OpenAI客户端"""
        api_key = config.get("api_key")
        if not api_key:
            api_key = "none_api_key"
        api_url = config.get("api_url")
        key = ("openai_sakura", api_url, api_key)
        return self._get_cached_client(key, lambda: self._create_openai_client(config, api_key, trust_env=False))

    def get_anthropic_client(self, config: Dict[str, Any]) -> Any:
        """获取Anthropic客户端"""
        api_key = config.get("api_key")
        api_url = config.get("api_url")
        key = ("anthropic", api_url, api_key)
        return self._get_cached_client(key, lambda: self._create_anthropic_client(config))

    def get_anthropic_bedrock(self, config: Dict[str, Any]) -> Any:
        """获取AnthropicBedrock客户端"""
        region = config.get("region")
        access_key = config.get("access_key")
        secret_key = config.get("secret_key")
        key = ("anthropic_bedrock", region, access_key, secret_key)
        return self._get_cached_client(key, lambda: self._create_anthropic_bedrock(config))

    def get_boto3_bedrock(self, config: Dict[str, Any]) -> Any:
        """获取boto3 bedrock客户端"""
        region = config.get("region")
        access_key = config.get("access_key")
        secret_key = config.get("secret_key")
        key = ("boto3_bedrock", region, access_key, secret_key)
        return self._get_cached_client(key, lambda: self._create_boto3_bedrock(config))

    def get_cohere_client(self, config: Dict[str, Any]) -> Any:
        """获取Cohere客户端"""
        api_key = config.get("api_key")
        api_url = config.get("api_url")
        key = ("cohere", api_url, api_key)
        return self._get_cached_client(key, lambda: self._create_cohere_client(config))

    def get_google_client(self, config: Dict[str, Any]) -> Any:
        """获取Google AI客户端"""
        api_key = config.get("api_key")
        api_url = self._normalize_google_api_url(config.get("api_url"))
        extra_body = config.get("extra_body")
        extra_body_serialized = json.dumps(extra_body, sort_keys=True) if extra_body else None
        key = ("google", api_key, api_url, extra_body_serialized)
        return self._get_cached_client(key, lambda: self._create_google_client(config))

    def _get_cached_client(self, key, factory_func):
        """线程安全地获取或创建客户端"""
        if key not in self._clients:
            with self._lock:
                if key not in self._clients:
                    self._clients[key] = factory_func()
        return self._clients[key]

    # 各种客户端创建函数
    def _create_openai_client(self, config, api_key, trust_env=True):
        from openai import OpenAI

        return OpenAI(
            base_url=config.get("api_url"),
            api_key=api_key,
            http_client=create_httpx_client(trust_env=trust_env),
            default_headers=self._get_browser_headers() # 注入伪装头
        )

    def _create_anthropic_client(self, config):
        import anthropic

        return anthropic.Anthropic(
            base_url=config.get("api_url"),
            api_key=config.get("api_key"),
            http_client=create_httpx_client(),
            default_headers=self._get_browser_headers() # 注入伪装头
        )

    def _create_anthropic_bedrock(self, config):
        import anthropic

        return anthropic.AnthropicBedrock(
            aws_region=config.get("region"),
            aws_access_key=config.get("access_key"),
            aws_secret_key=config.get("secret_key"),
            http_client=create_httpx_client()
        )

    def _create_boto3_bedrock(self, config):
        import boto3

        return boto3.client(
            "bedrock-runtime",
            region_name=config.get("region"),
            aws_access_key_id=config.get("access_key"),
            aws_secret_access_key=config.get("secret_key")
        )

    def _create_cohere_client(self, config):
        import cohere

        return cohere.ClientV2(
            base_url=config.get("api_url"),
            api_key=config.get("api_key"),
            timeout=config.get("request_timeout", 60),
            httpx_client=create_httpx_client()
        )

    def _create_google_client(self, config):
        from google import genai

        api_key = config.get("api_key")
        api_url = self._normalize_google_api_url(config.get("api_url"))
        extra_body = config.get("extra_body")

        http_options = {}
        if api_url:
            http_options["base_url"] = api_url
        if extra_body:
            http_options["extra_body"] = extra_body

        if http_options:
            return genai.Client(api_key=api_key, http_options=http_options)
        else:
            return genai.Client(api_key=api_key)

    def _normalize_google_api_url(self, api_url):
        api_url = str(api_url or "").strip()
        if not api_url:
            return api_url

        parsed = urlparse(api_url)
        path_parts = [part for part in parsed.path.split("/") if part]
        if path_parts and path_parts[-1].lower() == "openai":
            path_parts = path_parts[:-1]
            normalized_path = "/" + "/".join(path_parts) if path_parts else ""
            return urlunparse(parsed._replace(path=normalized_path, params="", query="", fragment="")).rstrip("/")

        return api_url.rstrip("/")
