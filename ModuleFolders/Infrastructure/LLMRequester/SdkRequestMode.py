SDK_REQUEST_MODE_HTTPX = "httpx"
SDK_REQUEST_MODE_OPENAI = "openai"
SDK_REQUEST_MODE_ANTHROPIC = "anthropic"
SDK_REQUEST_MODE_GOOGLE = "google"

SDK_REQUEST_MODES = (
    SDK_REQUEST_MODE_HTTPX,
    SDK_REQUEST_MODE_OPENAI,
    SDK_REQUEST_MODE_ANTHROPIC,
    SDK_REQUEST_MODE_GOOGLE,
)

SDK_REQUEST_MODE_LABELS = {
    SDK_REQUEST_MODE_HTTPX: "HTTPX",
    SDK_REQUEST_MODE_OPENAI: "OpenAI SDK",
    SDK_REQUEST_MODE_ANTHROPIC: "Anthropic SDK",
    SDK_REQUEST_MODE_GOOGLE: "Google SDK",
}


def normalize_sdk_request_mode(config: dict | None) -> str:
    config = config if isinstance(config, dict) else {}
    raw_mode = str(config.get("sdk_request_mode") or "").strip().lower()
    explicit_non_openai_mode = raw_mode in (SDK_REQUEST_MODE_ANTHROPIC, SDK_REQUEST_MODE_GOOGLE)
    if bool(config.get("use_openai_sdk", False)) and not explicit_non_openai_mode:
        return SDK_REQUEST_MODE_OPENAI
    if raw_mode in SDK_REQUEST_MODES:
        return raw_mode

    return SDK_REQUEST_MODE_HTTPX


def sdk_request_mode_label(mode: str) -> str:
    return SDK_REQUEST_MODE_LABELS.get(normalize_sdk_request_mode({"sdk_request_mode": mode}), "HTTPX")


def next_sdk_request_mode(mode: str) -> str:
    normalized = normalize_sdk_request_mode({"sdk_request_mode": mode})
    index = SDK_REQUEST_MODES.index(normalized)
    return SDK_REQUEST_MODES[(index + 1) % len(SDK_REQUEST_MODES)]


def is_openai_sdk_mode(config: dict | None) -> bool:
    return normalize_sdk_request_mode(config) == SDK_REQUEST_MODE_OPENAI


def is_anthropic_sdk_mode(config: dict | None) -> bool:
    return normalize_sdk_request_mode(config) == SDK_REQUEST_MODE_ANTHROPIC


def is_google_sdk_mode(config: dict | None) -> bool:
    return normalize_sdk_request_mode(config) == SDK_REQUEST_MODE_GOOGLE


def sync_sdk_request_mode_config(config: dict | None, *, prefer_sdk_request_mode: bool = False) -> dict | None:
    if not isinstance(config, dict):
        return config

    raw_mode = str(config.get("sdk_request_mode") or "").strip().lower()
    if prefer_sdk_request_mode and raw_mode in SDK_REQUEST_MODES:
        mode = raw_mode
    else:
        mode = normalize_sdk_request_mode(config)

    config["sdk_request_mode"] = mode
    config["use_openai_sdk"] = mode == SDK_REQUEST_MODE_OPENAI
    return config
