import copy
import os
import threading

import rapidjson as json

from ModuleFolders.Infrastructure.LLMRequester.SdkRequestMode import sync_sdk_request_mode_config
from ModuleFolders.Infrastructure.TaskConfig.default_config import DEFAULT_CONFIG


PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", ".."))
RESOURCE_PATH = os.path.join(PROJECT_ROOT, "Resource")
ROOT_CONFIG_FILE = os.path.join(RESOURCE_PATH, "config.json")
PROFILES_PATH = os.path.join(RESOURCE_PATH, "profiles")
RULES_PROFILES_PATH = os.path.join(RESOURCE_PATH, "rules_profiles")
PRESET_PATH = os.path.join(RESOURCE_PATH, "platforms", "preset.json")

RULE_DATA_KEYS = (
    "pre_translation_data",
    "post_translation_data",
    "prompt_dictionary_data",
    "exclusion_list_data",
    "characterization_data",
    "world_building_content",
    "world_building_history",
    "writing_style_content",
    "writing_style_history",
    "translation_example_data",
)

RULE_SWITCH_KEYS = (
    "pre_translation_switch",
    "post_translation_switch",
    "prompt_dictionary_switch",
    "exclusion_list_switch",
    "characterization_switch",
    "world_building_switch",
    "writing_style_switch",
    "translation_example_switch",
)

RULE_PROFILE_KEYS = RULE_DATA_KEYS + RULE_SWITCH_KEYS

ROOT_ONLY_KEYS = {
    "active_profile",
    "active_rules_profile",
    "wizard_completed",
    "recent_projects",
    "plugin_enables",
    "stream_api_cache",
}

_CONFIG_LOCK = threading.RLock()


def atomic_write_json(path: str, data: dict) -> None:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp_path = f"{path}.{os.getpid()}.{threading.get_ident()}.tmp"
    try:
        with open(tmp_path, "w", encoding="utf-8") as writer:
            json.dump(data, writer, indent=4, ensure_ascii=False)
        os.replace(tmp_path, path)
    finally:
        if os.path.exists(tmp_path):
            try:
                os.remove(tmp_path)
            except OSError:
                pass


def load_json_file(path: str, default=None):
    if default is None:
        default = {}
    if not os.path.exists(path) or os.path.getsize(path) == 0:
        return copy.deepcopy(default)
    with open(path, "r", encoding="utf-8-sig") as reader:
        data = json.load(reader)
    return data if isinstance(data, dict) else copy.deepcopy(default)


def deep_merge(base: dict, overlay: dict) -> dict:
    result = copy.deepcopy(base or {})
    if not isinstance(overlay, dict):
        return result
    for key, value in overlay.items():
        if isinstance(value, dict) and isinstance(result.get(key), dict):
            result[key] = deep_merge(result[key], value)
        else:
            result[key] = copy.deepcopy(value)
    return result


def sanitize_profile_name(name: str, *, allow_none: bool = False) -> str:
    profile_name = str(name or "").strip().strip('"').strip("'")
    if allow_none and profile_name == "None":
        return "None"

    invalid_chars = '<>:"/\\|?*\0'
    for char in invalid_chars:
        profile_name = profile_name.replace(char, "_")
    profile_name = profile_name.strip().strip(".")

    if not profile_name:
        raise ValueError("Profile name cannot be empty")
    if profile_name in (".", ".."):
        raise ValueError("Invalid profile name")
    if not allow_none and profile_name == "None":
        raise ValueError("Reserved profile name")
    return profile_name


def resolve_profile_path(base_dir: str, name: str, *, allow_none: bool = False) -> tuple[str, str]:
    profile_name = sanitize_profile_name(name, allow_none=allow_none)
    if allow_none and profile_name == "None":
        return "", profile_name

    base_real = os.path.realpath(base_dir)
    path = os.path.realpath(os.path.join(base_real, f"{profile_name}.json"))
    if os.path.commonpath([base_real, path]) != base_real:
        raise ValueError("Profile path escapes profile directory")
    return path, profile_name


def load_root_config() -> dict:
    try:
        root_config = load_json_file(ROOT_CONFIG_FILE, {})
    except Exception:
        root_config = {}

    if "active_profile" not in root_config:
        root_config["active_profile"] = "default"
    if "active_rules_profile" not in root_config:
        root_config["active_rules_profile"] = "default"
    return root_config


def save_root_config(root_config: dict) -> None:
    atomic_write_json(ROOT_CONFIG_FILE, root_config or {})


def get_config_mode() -> tuple[str, dict]:
    if not os.path.exists(ROOT_CONFIG_FILE):
        return "profile", {"active_profile": "default", "active_rules_profile": "default"}
    root_config = load_root_config()
    return "profile", root_config


def get_active_profile_name(root_config: dict | None = None) -> str:
    root_config = root_config if isinstance(root_config, dict) else load_root_config()
    try:
        return sanitize_profile_name(root_config.get("active_profile", "default"))
    except ValueError:
        return "default"


def get_active_rules_profile_name(root_config: dict | None = None) -> str:
    root_config = root_config if isinstance(root_config, dict) else load_root_config()
    try:
        return sanitize_profile_name(root_config.get("active_rules_profile", "default"), allow_none=True)
    except ValueError:
        return "default"


def load_master_preset() -> dict:
    preset = {}
    try:
        preset = load_json_file(PRESET_PATH, {})
    except Exception:
        preset = {}
    return deep_merge(DEFAULT_CONFIG, preset)


def default_rules_payload() -> dict:
    return {
        "pre_translation_data": [],
        "post_translation_data": [],
        "prompt_dictionary_data": [],
        "exclusion_list_data": [],
        "characterization_data": [],
        "world_building_content": "",
        "world_building_history": [],
        "writing_style_content": "",
        "writing_style_history": [],
        "translation_example_data": [],
        "pre_translation_switch": False,
        "post_translation_switch": False,
        "prompt_dictionary_switch": False,
        "exclusion_list_switch": False,
        "characterization_switch": False,
        "world_building_switch": False,
        "writing_style_switch": False,
        "translation_example_switch": False,
    }


def _rule_value_has_content(key: str, value) -> bool:
    if key in ("world_building_content", "writing_style_content"):
        return bool(str(value or "").strip())
    return bool(value)


def normalize_rules_payload(payload: dict, *, infer_missing_switches: bool = True) -> dict:
    normalized = default_rules_payload()
    if isinstance(payload, dict):
        for key in RULE_DATA_KEYS:
            if key in payload:
                normalized[key] = payload[key]
        for key in RULE_SWITCH_KEYS:
            if key in payload:
                normalized[key] = bool(payload[key])

    if infer_missing_switches and isinstance(payload, dict):
        inferred = {
            "pre_translation_switch": "pre_translation_data",
            "post_translation_switch": "post_translation_data",
            "prompt_dictionary_switch": "prompt_dictionary_data",
            "exclusion_list_switch": "exclusion_list_data",
            "characterization_switch": "characterization_data",
            "world_building_switch": "world_building_content",
            "writing_style_switch": "writing_style_content",
            "translation_example_switch": "translation_example_data",
        }
        for switch_key, data_key in inferred.items():
            if switch_key not in payload and _rule_value_has_content(data_key, normalized.get(data_key)):
                normalized[switch_key] = True

    return normalized


def split_effective_config(config: dict, *, prefer_sdk_request_mode: bool = False) -> tuple[dict, dict, dict]:
    sync_sdk_request_mode_config(config, prefer_sdk_request_mode=prefer_sdk_request_mode)

    settings = {}
    rules = {}
    root_updates = {}
    for key, value in (config or {}).items():
        if key in RULE_PROFILE_KEYS:
            rules[key] = value
        elif key in ROOT_ONLY_KEYS:
            root_updates[key] = value
        else:
            settings[key] = value
    return settings, rules, root_updates


def load_effective_config(
    *,
    root_config: dict | None = None,
    active_profile_name: str | None = None,
    active_rules_profile_name: str | None = None,
    create_missing: bool = False,
    interface_language: str | None = None,
) -> dict:
    with _CONFIG_LOCK:
        root_config = copy.deepcopy(root_config) if isinstance(root_config, dict) else load_root_config()
        profile_name = sanitize_profile_name(active_profile_name or get_active_profile_name(root_config))
        rules_profile_name = sanitize_profile_name(
            active_rules_profile_name or get_active_rules_profile_name(root_config),
            allow_none=True,
        )

        os.makedirs(PROFILES_PATH, exist_ok=True)
        os.makedirs(RULES_PROFILES_PATH, exist_ok=True)

        base_config = load_master_preset()
        profile_path, profile_name = resolve_profile_path(PROFILES_PATH, profile_name)
        if not os.path.exists(profile_path) and profile_name != "default":
            profile_name = "default"
            root_config["active_profile"] = "default"
            profile_path, _ = resolve_profile_path(PROFILES_PATH, profile_name)

        if not os.path.exists(profile_path):
            if create_missing:
                atomic_write_json(profile_path, base_config)
            profile_config = {}
        else:
            try:
                profile_config = load_json_file(profile_path, {})
            except Exception:
                profile_config = {}

        effective = deep_merge(base_config, profile_config)

        if rules_profile_name == "None":
            effective = deep_merge(effective, default_rules_payload())
        else:
            rules_path, rules_profile_name = resolve_profile_path(
                RULES_PROFILES_PATH,
                rules_profile_name,
                allow_none=True,
            )
            if not os.path.exists(rules_path) and rules_profile_name != "default" and not create_missing:
                rules_profile_name = "default"
                rules_path, _ = resolve_profile_path(
                    RULES_PROFILES_PATH,
                    rules_profile_name,
                    allow_none=True,
                )
            if not os.path.exists(rules_path):
                if create_missing:
                    atomic_write_json(rules_path, default_rules_payload())
                rules_config = {}
            else:
                try:
                    rules_config = load_json_file(rules_path, {})
                except Exception:
                    rules_config = {}
            effective = deep_merge(effective, normalize_rules_payload(rules_config))

        for key in ROOT_ONLY_KEYS:
            if key in root_config:
                effective[key] = copy.deepcopy(root_config[key])

        effective["active_profile"] = profile_name
        effective["active_rules_profile"] = rules_profile_name
        if interface_language and not effective.get("interface_language"):
            effective["interface_language"] = interface_language
        sync_sdk_request_mode_config(
            effective,
            prefer_sdk_request_mode=isinstance(profile_config, dict) and "sdk_request_mode" in profile_config,
        )
        return effective


def save_effective_config(
    config: dict,
    *,
    root_config: dict | None = None,
    active_profile_name: str | None = None,
    active_rules_profile_name: str | None = None,
    write_root: bool = True,
    prefer_sdk_request_mode: bool = False,
) -> dict:
    with _CONFIG_LOCK:
        root_config = copy.deepcopy(root_config) if isinstance(root_config, dict) else load_root_config()
        profile_name = sanitize_profile_name(active_profile_name or config.get("active_profile") or get_active_profile_name(root_config))
        rules_profile_name = sanitize_profile_name(
            active_rules_profile_name or config.get("active_rules_profile") or get_active_rules_profile_name(root_config),
            allow_none=True,
        )

        settings_updates, rules_updates, root_updates = split_effective_config(
            config,
            prefer_sdk_request_mode=prefer_sdk_request_mode,
        )
        profile_path, profile_name = resolve_profile_path(PROFILES_PATH, profile_name)
        current_profile = load_json_file(profile_path, {}) if os.path.exists(profile_path) else {}
        for key in RULE_PROFILE_KEYS:
            current_profile.pop(key, None)
        for key in ROOT_ONLY_KEYS:
            current_profile.pop(key, None)
        current_profile.update(settings_updates)
        atomic_write_json(profile_path, current_profile)

        if rules_profile_name != "None":
            rules_path, rules_profile_name = resolve_profile_path(
                RULES_PROFILES_PATH,
                rules_profile_name,
                allow_none=True,
            )
            current_rules = load_json_file(rules_path, {}) if os.path.exists(rules_path) else default_rules_payload()
            current_rules = {key: value for key, value in current_rules.items() if key in RULE_PROFILE_KEYS}
            current_rules.update(rules_updates)
            current_rules = normalize_rules_payload(current_rules, infer_missing_switches=False)
            atomic_write_json(rules_path, current_rules)

        root_config.update(root_updates)
        root_config["active_profile"] = profile_name
        root_config["active_rules_profile"] = rules_profile_name
        if write_root:
            save_root_config(root_config)
        return root_config


def set_active_platform_thinking(platform_tag: str, think_switch: bool) -> dict:
    """Atomically update one platform's thinking switch in the active settings profile."""
    if not isinstance(platform_tag, str) or not platform_tag.strip():
        raise ValueError("platform_tag must be a non-empty string")
    if type(think_switch) is not bool:
        raise ValueError("think_switch must be a boolean")

    normalized_tag = platform_tag.strip()
    with _CONFIG_LOCK:
        root_config = load_root_config()
        effective = load_effective_config(root_config=root_config, create_missing=False)
        active_profile = sanitize_profile_name(
            effective.get("active_profile") or get_active_profile_name(root_config)
        )

        effective_platforms = effective.get("platforms")
        platform_config = (
            effective_platforms.get(normalized_tag)
            if isinstance(effective_platforms, dict)
            else None
        )
        if not isinstance(platform_config, dict):
            raise KeyError(normalized_tag)

        profile_path, active_profile = resolve_profile_path(PROFILES_PATH, active_profile)
        profile_config = load_json_file(profile_path, {}) if os.path.exists(profile_path) else {}

        profile_platforms = profile_config.get("platforms")
        if profile_platforms is None:
            profile_platforms = {}
        elif not isinstance(profile_platforms, dict):
            raise ValueError("The active profile has an invalid platforms mapping")

        stored_platform = profile_platforms.get(normalized_tag)
        if stored_platform is None:
            stored_platform = {}
        elif not isinstance(stored_platform, dict):
            raise ValueError("The active profile has an invalid platform configuration")

        stored_platform["think_switch"] = think_switch
        profile_platforms[normalized_tag] = stored_platform
        profile_config["platforms"] = profile_platforms

        api_settings = effective.get("api_settings")
        translate_platform = (
            str(api_settings.get("translate") or "").strip()
            if isinstance(api_settings, dict)
            else ""
        )
        target_platform = str(effective.get("target_platform") or "").strip()
        if normalized_tag in {target_platform, translate_platform}:
            profile_config["think_switch"] = think_switch

        atomic_write_json(profile_path, profile_config)
        return {
            "active_profile": active_profile,
            "platform_tag": normalized_tag,
            "think_switch": think_switch,
            "think_depth": platform_config.get("think_depth"),
        }


def save_setting_value(key: str, value) -> None:
    config = load_effective_config(create_missing=True)
    config[key] = value
    if key in ("sdk_request_mode", "use_openai_sdk"):
        sync_sdk_request_mode_config(config, prefer_sdk_request_mode=key == "sdk_request_mode")
    save_effective_config(config, prefer_sdk_request_mode=key == "sdk_request_mode")


def save_rule_value(key: str, value) -> None:
    if key not in RULE_PROFILE_KEYS:
        raise KeyError(f"{key} is not a rules profile key")
    root_config = load_root_config()
    rules_profile_name = get_active_rules_profile_name(root_config)
    if rules_profile_name == "None":
        raise ValueError("No active rules profile selected")
    rules_path, _ = resolve_profile_path(RULES_PROFILES_PATH, rules_profile_name, allow_none=True)
    current_rules = load_json_file(rules_path, {}) if os.path.exists(rules_path) else default_rules_payload()
    current_rules[key] = value
    atomic_write_json(rules_path, normalize_rules_payload(current_rules, infer_missing_switches=False))


def list_profile_names(base_dir: str, *, include_none: bool = False) -> list[str]:
    os.makedirs(base_dir, exist_ok=True)
    names = []
    for filename in os.listdir(base_dir):
        if not filename.endswith(".json"):
            continue
        try:
            names.append(sanitize_profile_name(filename[:-5], allow_none=include_none))
        except ValueError:
            continue
    names = sorted(dict.fromkeys(names))
    if include_none:
        return ["None"] + (names or ["default"])
    return names or ["default"]
