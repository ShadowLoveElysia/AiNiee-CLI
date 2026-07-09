"""
API管理模块 - 从 ainiee_cli.py 分离
负责API平台选择、验证、配置等功能
"""
import os
import time
import rapidjson as json

from rich.console import Console
from rich.panel import Panel
from rich.prompt import Prompt, IntPrompt
from rich.table import Table

from ModuleFolders.Infrastructure.LLMRequester.SdkRequestMode import (
    is_openai_sdk_mode,
    next_sdk_request_mode,
    normalize_sdk_request_mode,
    sdk_request_mode_label,
)

console = Console()


def mask_key(key):
    """遮蔽API Key显示"""
    if not key: return ""
    if len(key) <= 5: return key[:1] + "***" + key[-1:]
    return key[:2] + "***" + key[-3:]


def open_in_editor(file_path):
    """在系统默认编辑器中打开文件"""
    import sys
    import subprocess
    try:
        if sys.platform == 'win32':
            os.startfile(file_path)
        elif sys.platform == 'darwin':
            subprocess.run(['open', file_path])
        else:
            subprocess.run(['xdg-open', file_path])
        return True
    except Exception as e:
        console.print(f"[red]Failed to open editor: {e}[/red]")
        return False


class APIManager:
    """API管理器，处理API平台选择、验证、配置等功能"""

    def __init__(self, cli_menu):
        """
        初始化API管理器

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

    @property
    def profiles_dir(self):
        return self.cli.profiles_dir

    @property
    def active_profile_name(self):
        return self.cli.active_profile_name

    @property
    def task_executor(self):
        return self.cli.task_executor

    @property
    def PROJECT_ROOT(self):
        return self.cli.PROJECT_ROOT

    def save_config(self):
        self.cli.save_config()

    def load_config(self):
        self.cli.load_config()

    def display_banner(self):
        self.cli.display_banner()

    def api_settings_menu(self):
        """API设置主菜单"""
        while True:
            self.display_banner()
            current_p = self.config.get("target_platform", "None")
            current_m = self.config.get("model", "None")
            sdk_mode = normalize_sdk_request_mode(self.config)
            mode_label = sdk_request_mode_label(sdk_mode)
            console.print(Panel(
                f"[bold]{self.i18n.get('menu_api_settings')}[/bold] "
                f"[dim](Current: {current_p} - {current_m})[/dim]"
            ))
            menus = ["online", "local", "validate", "manual"]
            table = Table(show_header=False, box=None)
            for i, m in enumerate(menus):
                color = "cyan" if i < 2 else "green" if i < 3 else "yellow"
                table.add_row(f"[{color}]{i+1}.[/]", self.i18n.get(f"menu_api_{m}"))

            # 请求模式切换
            sdk_status_style = "yellow" if sdk_mode == "httpx" else "green"
            sdk_status = f"[{sdk_status_style}]{mode_label}[/]"
            table.add_row(
                "[bold blue]5.[/]",
                f"{self.i18n.get('setting_sdk_request_mode')}: {sdk_status}"
            )
            # 编辑器打开
            table.add_row("[magenta]6.[/]", self.i18n.get("menu_edit_in_editor"))

            console.print(table)
            console.print(f"\n[dim]0. {self.i18n.get('menu_exit')}[/dim]")
            choice = IntPrompt.ask(
                self.i18n.get('prompt_select'),
                choices=list("0123456"),
                show_choices=False
            )
            console.print()
            if choice == 0:
                break
            elif choice in [1, 2]:
                self.select_api_menu(online=choice == 1)
            elif choice == 3:
                self.validate_api()
            elif choice == 4:
                self.manual_edit_api_menu()
            elif choice == 5:
                new_mode = next_sdk_request_mode(sdk_mode)
                self.config["sdk_request_mode"] = new_mode
                self.config["use_openai_sdk"] = new_mode == "openai"
                self.save_config()
                console.print(f"[green]{self.i18n.get('setting_sdk_request_mode')} -> {sdk_request_mode_label(new_mode)}[/green]")
                time.sleep(0.5)
            elif choice == 6:
                profile_path = os.path.join(self.profiles_dir, f"{self.active_profile_name}.json")
                if open_in_editor(profile_path):
                    Prompt.ask(f"\n{self.i18n.get('msg_press_enter_after_save')}")
                    self.load_config()
                    console.print(f"[green]Profile '{self.active_profile_name}' reloaded.[/green]")
                time.sleep(1)

    def select_api_menu(self, online: bool):
        """选择API平台菜单"""
        local_keys = ["localllm", "sakura", "murasaki"]
        platforms = self.config.get("platforms", {})

        # 分类逻辑
        official_options = {}
        custom_options = {}

        target_options = {k: v for k, v in platforms.items() if (k.lower() not in local_keys) == online}

        for k, v in target_options.items():
            group = v.get("group", "")
            if group == "custom" or "custom" in k.lower():
                custom_options[k] = v
            else:
                official_options[k] = v

        if not target_options:
            return

        # 显示菜单
        console.print(Panel(f"[bold]{self.i18n.get('msg_api_select_' + ('online' if online else 'local'))}[/bold]"))

        all_keys = []
        # 1. 官方预设
        if official_options:
            console.print(f"\n[bold yellow]{self.i18n.get('label_api_official')}[/bold yellow]")
            console.print(f"[dim]{self.i18n.get('tip_api_official')}[/dim]")
            sorted_off = sorted(list(official_options.keys()))
            table_off = Table(show_header=False, box=None)
            for i, k in enumerate(sorted_off):
                table_off.add_row(f"[cyan]{len(all_keys) + 1}.[/]", k)
                all_keys.append(k)
            console.print(table_off)

        # 2. 自定义/中转
        if custom_options:
            console.print(f"\n[bold cyan]{self.i18n.get('label_api_custom')}[/bold cyan]")
            console.print(f"[dim]{self.i18n.get('tip_api_custom')}[/dim]")
            sorted_cust = sorted(list(custom_options.keys()))
            table_cust = Table(show_header=False, box=None)
            for i, k in enumerate(sorted_cust):
                table_cust.add_row(f"[cyan]{len(all_keys) + 1}.[/]", k)
                all_keys.append(k)
            console.print(table_cust)

        # 3. 新增自定义选项
        console.print(f"\n[cyan]A.[/] [bold]{self.i18n.get('menu_api_add_custom')}[/bold]")
        console.print(f"\n[dim]0. {self.i18n.get('menu_exit')}[/dim]")

        choice_str = Prompt.ask(self.i18n.get('prompt_select')).upper()
        if choice_str == '0':
            return

        if choice_str == 'A':
            new_tag = Prompt.ask(self.i18n.get("prompt_custom_api_name")).strip()
            if not new_tag:
                return

            # 使用 custom 模板创建
            custom_template = platforms.get("custom", {
                "tag": "custom", "group": "custom", "name": "Custom API",
                "api_url": "", "api_key": "", "api_format": "OpenAI",
                "model": "gpt-4o", "key_in_settings": ["api_url", "api_key", "model"]
            }).copy()

            custom_template["tag"] = new_tag
            custom_template["name"] = new_tag

            if "platforms" not in self.config:
                self.config["platforms"] = {}
            self.config["platforms"][new_tag] = custom_template
            self.save_config()
            console.print(f"[green]{self.i18n.get('msg_custom_api_created').format(new_tag)}[/green]")
            time.sleep(1)
            return self.select_api_menu(online)

        if not choice_str.isdigit():
            return
        choice = int(choice_str)
        if not (1 <= choice <= len(all_keys)):
            return

        sel = all_keys[choice - 1]
        plat_conf = target_options[sel]
        is_custom = plat_conf.get("group") == "custom" or "custom" in sel.lower()

        # 复制配置
        new_plat_conf = plat_conf.copy()
        if sel in self.config.get("platforms", {}):
            new_plat_conf.update(self.config["platforms"][sel])

        console.print(f"\n[bold cyan]--- {self.i18n.get('menu_api_manual')}: {sel} ---[/bold cyan]")

        # --- 交互询问逻辑 ---
        # 1. 询问 URL (仅限自定义平台)
        if is_custom:
            new_plat_conf["api_url"] = Prompt.ask(self.i18n.get("prompt_api_url"), default=new_plat_conf.get("api_url", "")).strip()

        # 2. 询问 API Key (所有在线平台都需要)
        if online and plat_conf.get("api_key") != "nokey":
            new_plat_conf["api_key"] = Prompt.ask(self.i18n.get("prompt_api_key"), password=True, default=new_plat_conf.get("api_key", "")).strip()

        # 3. 询问 Model
        new_plat_conf["model"] = Prompt.ask(self.i18n.get("prompt_model"), default=new_plat_conf.get("model", "")).strip()

        # 4. 特殊平台额外参数 (Amazon Bedrock 等)
        if sel == "amazonbedrock":
            new_plat_conf["access_key"] = Prompt.ask("Access Key", password=True, default=new_plat_conf.get("access_key", "")).strip()
            new_plat_conf["secret_key"] = Prompt.ask("Secret Key", password=True, default=new_plat_conf.get("secret_key", "")).strip()
            new_plat_conf["region"] = Prompt.ask("Region", default=new_plat_conf.get("region", "us-east-1")).strip()

        # 更新全局状态
        self.config.update({
            "target_platform": sel,
            "base_url": new_plat_conf.get("api_url", ""),
            "api_key": new_plat_conf.get("api_key", ""),
            "model": new_plat_conf.get("model", ""),
            "api_settings": {"translate": sel, "polish": sel}
        })

        if "platforms" not in self.config:
            self.config["platforms"] = {}
        self.config["platforms"][sel] = new_plat_conf

        self.save_config()
        console.print(f"\n[green]{self.i18n.get('msg_active_platform').format(sel)}[/green]")

        # Murasaki 推理模型提示
        if sel == "murasaki" or "murasaki" in new_plat_conf.get("model", "").lower():
            console.print(f"\n[bold yellow]⚠ {self.i18n.get('msg_murasaki_think_hint')}[/bold yellow]")

        time.sleep(1)

    def validate_api(self):
        """验证API连接"""
        from ModuleFolders.Infrastructure.TaskConfig.TaskType import TaskType
        from ModuleFolders.Base.Base import Base

        task_config = self.task_executor.config
        self.task_executor.config.initialize(self.config)

        original_base_print = Base.print
        Base.print = lambda *args, **kwargs: None
        try:
            self.task_executor.config.prepare_for_translation(TaskType.TRANSLATION)
        finally:
            Base.print = original_base_print

        target_platform = self.task_executor.config.target_platform
        base_url_for_validation = task_config.base_url if task_config.base_url else ""

        with console.status(f"[cyan]{self.i18n.get('msg_api_validating')}[/cyan]"):
            try:
                if target_platform.lower() in ["localllm", "sakura", "murasaki"]:
                    import httpx
                    console.print(f"[dim]Debug: base_url_for_validation = {base_url_for_validation}[/dim]")

                    if not base_url_for_validation.startswith("http://") and not base_url_for_validation.startswith("https://"):
                        api_url = "http://" + base_url_for_validation.rstrip('/')
                    else:
                        api_url = base_url_for_validation.rstrip('/')

                    if not api_url.endswith('/chat/completions'):
                        api_url = f"{api_url}/chat/completions"

                    console.print(f"[dim]Debug: constructed api_url = {api_url}[/dim]")

                    api_key = task_config.get_next_apikey()
                    headers = {"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"}
                    payload = {"messages": [{"role": "user", "content": self.i18n.get("msg_test_msg")}], "max_tokens": 50}

                    with httpx.Client(timeout=20) as client:
                        response = client.post(api_url, json=payload, headers=headers)
                        response.raise_for_status()
                        content = response.json()["choices"][0]["message"]["content"]
                else:
                    plat_conf = task_config.get_platform_configuration("translationReq")
                    if plat_conf.get("api_format") == "Anthropic":
                        # ===== Anthropic 协议验证模式 =====
                        from ModuleFolders.Infrastructure.LLMRequester.AnthropicRequester import AnthropicRequester

                        skip, response_think, response_content, prompt_tokens, completion_tokens = AnthropicRequester().request_anthropic(
                            [{"role": "user", "content": self.i18n.get("msg_test_msg")}],
                            "",
                            plat_conf,
                        )
                        if skip:
                            raise Exception(response_content)
                        content = response_content
                    elif plat_conf.get("api_format") == "Google" and not is_openai_sdk_mode(self.config):
                        # ===== Google SDK 验证模式 =====
                        from ModuleFolders.Infrastructure.LLMRequester.GoogleRequester import GoogleRequester

                        skip, response_think, response_content, prompt_tokens, completion_tokens = GoogleRequester().request_google(
                            [{"role": "user", "content": self.i18n.get("msg_test_msg")}],
                            "",
                            plat_conf,
                        )
                        if skip:
                            raise Exception(response_content)
                        content = response_content
                    elif is_openai_sdk_mode(self.config):
                        # ===== OpenAI SDK 验证模式 =====
                        from ModuleFolders.Infrastructure.LLMRequester.LLMClientFactory import LLMClientFactory

                        api_key = task_config.get_next_apikey()
                        model_name = task_config.model

                        sdk_config = {
                            "api_url": task_config.base_url,
                            "api_key": api_key,
                            "auto_complete": plat_conf.get("auto_complete", False),
                        }
                        client = LLMClientFactory().get_openai_client(sdk_config)
                        response = client.chat.completions.create(
                            model=model_name,
                            messages=[{"role": "user", "content": self.i18n.get("msg_test_msg")}],
                            max_tokens=100,
                            timeout=20,
                        )
                        content = response.choices[0].message.content or ""
                    else:
                        # ===== 原生 HTTPX 验证模式 =====
                        import httpx
                        api_url = task_config.base_url.rstrip('/')

                        api_format = plat_conf.get("api_format", "OpenAI")
                        auto_complete = plat_conf.get("auto_complete", False)

                        if api_format == "OpenAI" and auto_complete and not any(api_url.endswith(s) for s in ["/chat/completions", "/completions"]):
                            api_url = f"{api_url}/chat/completions"

                        api_key = task_config.get_next_apikey()
                        model_name = task_config.model

                        payload = {
                            "model": model_name,
                            "messages": [{"role": "user", "content": self.i18n.get("msg_test_msg")}],
                            "max_tokens": 100,
                            "stream": False
                        }

                        from ModuleFolders.Infrastructure.LLMRequester.LLMClientFactory import create_httpx_client
                        with create_httpx_client(timeout=20) as client:
                            auth_headers = {
                                "Authorization": f"Bearer {api_key}",
                                "Content-Type": "application/json",
                                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
                                "Accept": "application/json"
                            }

                            response = client.post(api_url, json=payload, headers=auth_headers)

                            if response.status_code != 200:
                                server_type = response.headers.get('Server', 'Unknown')
                                error_body = response.text[:500]
                                debug_info = f"\n  - [Status] {response.status_code}\n  - [Server] {server_type}\n  - [Body] {error_body}"
                                raise Exception(f"HTTP {response.status_code} Error.{debug_info}")

                            raw_content = response.text.strip()

                            if raw_content.startswith("data:"):
                                full_content = ""
                                for line in raw_content.split("\n"):
                                    if line.startswith("data:"):
                                        json_str = line.replace("data:", "").strip()
                                        if json_str == "[DONE]":
                                            break
                                        try:
                                            res_json = json.loads(json_str)
                                            if "choices" in res_json:
                                                choice = res_json["choices"][0]
                                                chunk_text = choice.get("message", {}).get("content", "") or choice.get("delta", {}).get("content", "")
                                                full_content += chunk_text
                                        except:
                                            continue
                                content = full_content
                            else:
                                try:
                                    res_json = response.json()
                                    if "choices" in res_json:
                                        content = res_json["choices"][0]["message"].get("content", "")
                                    else:
                                        content = str(res_json)
                                except Exception:
                                    raise Exception(f"Response is not valid JSON. Status: {response.status_code}, Body: {raw_content[:500]}")

                console.print(f"[green]✓ {self.i18n.get('msg_api_ok')}[/green]")
                console.print(f"[cyan]Response:[/cyan] {content}")

            except Exception as e:
                console.print(f"[red]✗ {self.i18n.get('msg_api_fail')}:[/red] {e}")

        Prompt.ask(f"\n{self.i18n.get('msg_press_enter')}")

    def manual_edit_api_menu(self):
        """手动编辑API设置菜单"""
        while True:
            self.display_banner()
            console.print(Panel(f"[bold]{self.i18n.get('menu_api_manual')}[/bold]"))
            table = Table(show_header=True)
            table.add_column("ID", style="dim")
            table.add_column("Setting")
            table.add_column("Value", style="cyan")

            tp = self.config.get("target_platform", "")
            plat_conf = self.config.get("platforms", {}).get(tp, {})

            think_sw = self.config.get("think_switch", plat_conf.get("think_switch", False))
            think_dp = self.config.get("think_depth", plat_conf.get("think_depth", "low"))
            think_budget = self.config.get("thinking_budget")
            if think_budget is None:
                think_budget = plat_conf.get("thinking_budget", 4096)

            structured_mode = plat_conf.get("structured_output_mode", 0)
            auto_comp = plat_conf.get("auto_complete", False)
            mode_display = ["OFF", "JSON Mode", "Function Call"][structured_mode] if structured_mode < 3 else "Unknown"

            table.add_row("1", self.i18n.get("label_platform"), tp)
            table.add_row("2", self.i18n.get("label_url"), self.config.get("base_url", ""))
            table.add_row("3", self.i18n.get("label_key"), "****")
            table.add_row("4", self.i18n.get("label_model"), self.config.get("model", ""))
            table.add_row("5", self.i18n.get("menu_api_think_switch"), "[green]ON[/]" if think_sw else "[red]OFF[/]")
            table.add_row("6", self.i18n.get("menu_api_structured_output_switch"), f"[cyan]{mode_display}[/cyan]")

            api_format = plat_conf.get("api_format", "")
            is_local_platform = tp.lower() in ["sakura", "localllm", "murasaki"]

            table.add_row("7", self.i18n.get("menu_api_think_depth"), str(think_dp))
            table.add_row("8", self.i18n.get("menu_api_think_budget"), str(think_budget))
            table.add_row("10", self.i18n.get("自动补全 OpenAI 规范的 Chat 终点"), "[green]ON[/]" if auto_comp else "[red]OFF[/]")

            temp_val = plat_conf.get("temperature", 1.0)
            top_p_val = plat_conf.get("top_p", 1.0)
            table.add_row("11", f"[yellow]{self.i18n.get('menu_api_sampling_params')}[/yellow]", f"[dim]T={temp_val}, P={top_p_val}[/dim]")

            if api_format == "Anthropic":
                table.add_row("9", self.i18n.get("menu_fetch_models"), "...")

            console.print(table)

            if think_sw:
                if is_local_platform:
                    console.print(f"\n[red]⚠️ {self.i18n.get('warning_thinking_online_only')}[/red]")
                else:
                    console.print(f"\n[red]⚠️ {self.i18n.get('warning_thinking_compatibility')}[/red]")

            console.print(f"\n[dim]0. {self.i18n.get('menu_exit')}[/dim]")
            choice = IntPrompt.ask(self.i18n.get('prompt_select'), choices=["0","1","2","3","4","5","6","7","8","9","10","11"], show_choices=False)
            console.print()

            if choice == 0:
                break
            elif choice == 1:
                new_plat = Prompt.ask(self.i18n.get("label_platform"), default=self.config.get("target_platform")).strip()
                self.config["target_platform"] = new_plat
                self.config["api_settings"] = {"translate": new_plat, "polish": new_plat}
            elif choice == 2:
                new_url = Prompt.ask(self.i18n.get("label_url"), default=self.config.get("base_url")).strip()
                self.config["base_url"] = new_url
                if tp in self.config.get("platforms", {}):
                    self.config["platforms"][tp]["api_url"] = new_url
            elif choice == 3:
                new_key = Prompt.ask(self.i18n.get("label_key"), password=True, default=self.config.get("api_key", ""))
                if new_key:
                    new_key = new_key.strip()
                    self.config["api_key"] = new_key
                    if tp in self.config.get("platforms", {}):
                        self.config["platforms"][tp]["api_key"] = new_key
            elif choice == 4:
                new_model = Prompt.ask(self.i18n.get("label_model"), default=self.config.get("model")).strip()
                self.config["model"] = new_model
                if tp in self.config.get("platforms", {}):
                    self.config["platforms"][tp]["model"] = new_model
            elif choice == 5:
                new_state = not think_sw
                self.config["think_switch"] = new_state
                if tp in self.config.get("platforms", {}):
                    self.config["platforms"][tp]["think_switch"] = new_state
            elif choice == 6:
                new_mode = (structured_mode + 1) % 3
                if tp in self.config.get("platforms", {}):
                    self.config["platforms"][tp]["structured_output_mode"] = new_mode
            elif choice == 7:
                if api_format == "Anthropic":
                    val = Prompt.ask(self.i18n.get("prompt_think_depth_claude"), choices=["low", "medium", "high"], default=str(think_dp) if think_dp in ["low", "medium", "high"] else "low")
                elif tp == "deepseek":
                    val = Prompt.ask(self.i18n.get("prompt_think_depth"), choices=["low", "medium", "high", "xhigh", "max"], default=str(think_dp) if think_dp in ["low", "medium", "high", "xhigh", "max"] else "low")
                else:
                    val = Prompt.ask(self.i18n.get("prompt_think_depth"), choices=["minimal", "low", "medium", "high", "xhigh"], default=str(think_dp) if think_dp in ["minimal", "low", "medium", "high", "xhigh"] else "low")
                self.config["think_depth"] = val
                if tp in self.config.get("platforms", {}):
                    self.config["platforms"][tp]["think_depth"] = val
            elif choice == 8:
                console.print(f"[dim]{self.i18n.get('hint_think_budget') or '提示: 0=关闭, -1=无上限'}[/dim]")
                val_str = Prompt.ask(self.i18n.get("prompt_think_budget"), default=str(int(think_budget)))
                try:
                    val = int(val_str)
                    self.config["thinking_budget"] = val
                    if tp in self.config.get("platforms", {}):
                        self.config["platforms"][tp]["thinking_budget"] = val
                except ValueError:
                    console.print("[red]Invalid input[/red]")
            elif choice == 10:
                new_state = not auto_comp
                if tp in self.config.get("platforms", {}):
                    self.config["platforms"][tp]["auto_complete"] = new_state
            elif choice == 11:
                self._sampling_params_menu(tp, plat_conf)
            elif choice == 9 and api_format == "Anthropic":
                self._fetch_anthropic_models(tp, plat_conf)

            self.save_config()

    def _fetch_anthropic_models(self, tp, plat_conf):
        """获取Anthropic模型列表"""
        from ModuleFolders.Infrastructure.LLMRequester.AnthropicRequester import AnthropicRequester

        with console.status(f"[cyan]{self.i18n.get('msg_fetching_models')}...[/cyan]"):
            requester = AnthropicRequester()
            temp_config = plat_conf.copy()
            temp_config["api_url"] = self.config.get("base_url", plat_conf.get("api_url"))
            temp_config["api_key"] = self.config.get("api_key", plat_conf.get("api_key"))
            temp_config["auto_complete"] = plat_conf.get("auto_complete", False)

            models = requester.get_model_list(temp_config)

            if models:
                console.print(f"[green]✓ {self.i18n.get('msg_fetch_models_ok')}[/green]")
                m_table = Table(show_header=True)
                m_table.add_column("ID", style="dim")
                m_table.add_column("Model ID")
                for i, m in enumerate(models):
                    m_table.add_row(str(i+1), m)
                console.print(m_table)

                m_choice = IntPrompt.ask(self.i18n.get('prompt_select_model'), choices=[str(i+1) for i in range(len(models))] + ["0"], show_choices=False)
                if m_choice > 0:
                    selected_model = models[m_choice-1]
                    self.config["model"] = selected_model
                    if tp in self.config.get("platforms", {}):
                        self.config["platforms"][tp]["model"] = selected_model
                        self.config["platforms"][tp]["model_datas"] = models
                    console.print(f"[green]Selected model: {selected_model}[/green]")
                time.sleep(1)
            else:
                console.print(f"[red]✗ {self.i18n.get('msg_fetch_models_fail')}[/red]")
                time.sleep(1)

    def _sampling_params_menu(self, tp: str, plat_conf: dict):
        """采样参数设置子菜单"""
        while True:
            self.display_banner()
            console.print(Panel(
                f"[bold yellow]{self.i18n.get('warn_sampling_params')}[/bold yellow]\n"
                f"[dim]{self.i18n.get('warn_sampling_params_desc')}[/dim]",
                title=f"[bold]{self.i18n.get('menu_api_sampling_params')}[/bold]",
                border_style="yellow"
            ))

            temp_val = plat_conf.get("temperature", 1.0)
            top_p_val = plat_conf.get("top_p", 1.0)
            top_k_val = plat_conf.get("top_k", 0)
            presence_val = plat_conf.get("presence_penalty", 0.0)
            frequency_val = plat_conf.get("frequency_penalty", 0.0)

            table = Table(show_header=True)
            table.add_column("ID", style="dim")
            table.add_column(self.i18n.get("label_setting_name"))
            table.add_column(self.i18n.get("label_value"), style="cyan")
            table.add_column("Hint", style="dim")

            table.add_row("1", self.i18n.get("setting_temperature"), str(temp_val), self.i18n.get("hint_temperature"))
            table.add_row("2", self.i18n.get("setting_top_p"), str(top_p_val), self.i18n.get("hint_top_p"))
            table.add_row("3", self.i18n.get("setting_top_k"), str(top_k_val), self.i18n.get("hint_top_k"))
            table.add_row("4", self.i18n.get("setting_presence_penalty"), str(presence_val), self.i18n.get("hint_presence_penalty"))
            table.add_row("5", self.i18n.get("setting_frequency_penalty"), str(frequency_val), self.i18n.get("hint_frequency_penalty"))

            console.print(table)
            console.print(f"\n[dim]0. {self.i18n.get('menu_back')}[/dim]")

            choice = IntPrompt.ask(self.i18n.get('prompt_select'), choices=["0", "1", "2", "3", "4", "5"], show_choices=False)

            if choice == 0:
                break

            console.print(f"\n[bold red]{self.i18n.get('warn_sampling_params')}[/bold red]")
            confirm = Prompt.ask(self.i18n.get('confirm_sampling_change')).strip().upper()

            if confirm != "YES":
                console.print(f"[yellow]{self.i18n.get('msg_sampling_change_cancelled')}[/yellow]")
                time.sleep(1)
                continue

            param_map = {
                1: ("temperature", temp_val, 0.0, 2.0),
                2: ("top_p", top_p_val, 0.0, 1.0),
                3: ("top_k", top_k_val, 0, 100),
                4: ("presence_penalty", presence_val, -2.0, 2.0),
                5: ("frequency_penalty", frequency_val, -2.0, 2.0),
            }

            param_name, current_val, min_val, max_val = param_map[choice]

            try:
                if choice == 3:
                    new_val = IntPrompt.ask(f"{self.i18n.get('prompt_new_value')} ({min_val}-{max_val})", default=int(current_val))
                    new_val = max(min_val, min(max_val, new_val))
                else:
                    new_val_str = Prompt.ask(f"{self.i18n.get('prompt_new_value')} ({min_val}-{max_val})", default=str(current_val))
                    new_val = float(new_val_str)
                    new_val = max(min_val, min(max_val, new_val))

                if tp in self.config.get("platforms", {}):
                    self.config["platforms"][tp][param_name] = new_val
                    plat_conf[param_name] = new_val

                self.save_config()
                console.print(f"[green]{self.i18n.get('msg_sampling_change_saved')} {param_name} = {new_val}[/green]")
                time.sleep(1)

            except ValueError:
                console.print("[red]Invalid input[/red]")
                time.sleep(1)

    def api_pool_menu(self):
        """API池管理菜单"""
        while True:
            self.display_banner()
            sw = self.config.get("enable_api_failover", False)
            th = self.config.get("api_failover_threshold", 10)
            pool = self.config.get("backup_apis", [])

            console.print(Panel(f"[bold]{self.i18n.get('menu_api_pool_settings')}[/bold]"))
            console.print(f"[dim]{self.i18n.get('tip_failover_logic')}[/dim]\n")

            table = Table(show_header=False, box=None)
            table.add_row("[cyan]1.[/]", f"{self.i18n.get('setting_failover_status')}: [green]{'ON' if sw else 'OFF'}[/green]")
            table.add_row("[cyan]2.[/]", f"{self.i18n.get('setting_failover_threshold')}: [yellow]{th}[/yellow]")
            table.add_row("[cyan]3.[/]", f"{self.i18n.get('prompt_add_to_pool')}")
            table.add_row("[cyan]4.[/]", f"{self.i18n.get('prompt_remove_from_pool')}")
            console.print(table)

            if pool:
                console.print(f"\n[bold]{self.i18n.get('label_current_pool')}:[/bold]")
                p_list = " ➔ ".join([f"[cyan]{p}[/cyan]" for p in pool])
                console.print(f"  {p_list}")
            else:
                console.print(f"\n[dim]{self.i18n.get('msg_api_pool_empty')}[/dim]")

            console.print(f"\n[dim]0. {self.i18n.get('menu_back')}[/dim]")
            c = IntPrompt.ask(self.i18n.get('prompt_select'), choices=["0", "1", "2", "3", "4"], show_choices=False)

            if c == 0:
                break
            elif c == 1:
                self.config["enable_api_failover"] = not sw
            elif c == 2:
                self.config["api_failover_threshold"] = IntPrompt.ask(self.i18n.get("setting_failover_threshold"), default=10)
            elif c == 3:
                local_keys = ["localllm", "sakura", "murasaki"]
                online_platforms = [k for k in self.config.get("platforms", {}).keys() if k.lower() not in local_keys]
                candidates = [k for k in online_platforms if k not in pool]
                if not candidates:
                    continue

                console.print(Panel(self.i18n.get("prompt_add_to_pool")))
                c_table = Table(show_header=False, box=None)
                for i, k in enumerate(candidates):
                    c_table.add_row(f"[cyan]{i+1}.[/]", k)
                console.print(c_table)
                sel = IntPrompt.ask(self.i18n.get("prompt_select"), choices=[str(i) for i in range(len(candidates)+1)], default=0, show_choices=False)
                if sel > 0:
                    pool.append(candidates[sel-1])
                    self.config["backup_apis"] = pool
            elif c == 4:
                if not pool:
                    continue
                console.print(Panel(self.i18n.get("prompt_remove_from_pool")))
                r_table = Table(show_header=False, box=None)
                for i, k in enumerate(pool):
                    r_table.add_row(f"[cyan]{i+1}.[/]", k)
                console.print(r_table)
                sel = IntPrompt.ask(self.i18n.get("prompt_select"), choices=[str(i) for i in range(len(pool)+1)], default=0, show_choices=False)
                if sel > 0:
                    pool.pop(sel-1)
                    self.config["backup_apis"] = pool
            self.save_config()

    def configure_temp_api_for_analysis(self):
        """配置临时API用于术语分析"""
        try:
            preset_path = os.path.join(self.PROJECT_ROOT, "Resource", "platforms", "preset.json")
            with open(preset_path, 'r', encoding='utf-8') as f:
                preset = json.load(f)

            platforms = preset.get("platforms", {})
            online_platforms = {k: v for k, v in platforms.items() if v.get("group") in ["online", "custom"]}

            sorted_keys = sorted(online_platforms.keys())
            console.print(Panel(self.i18n.get("prompt_temp_api_platform") or "选择临时API平台"))
            p_table = Table(show_header=False, box=None)
            for i, k in enumerate(sorted_keys):
                p_table.add_row(f"[cyan]{i+1}.[/]", online_platforms[k].get("name", k))
            console.print(p_table)
            console.print(f"\n[dim]0. {self.i18n.get('menu_back')}[/dim]")

            plat_idx = IntPrompt.ask(self.i18n.get('prompt_select'), default=0)
            if plat_idx == 0 or plat_idx > len(sorted_keys):
                return None

            sel_tag = sorted_keys[plat_idx - 1]
            sel_conf = online_platforms[sel_tag].copy()

            if "api_key" in sel_conf.get("key_in_settings", []) or "api_key" in sel_conf:
                sel_conf["api_key"] = Prompt.ask(self.i18n.get("prompt_temp_api_key") or "API Key", password=True).strip()

            if "api_url" in sel_conf.get("key_in_settings", []) or sel_tag == "custom":
                sel_conf["api_url"] = Prompt.ask(self.i18n.get("prompt_temp_api_url") or "API URL", default=sel_conf.get("api_url", "")).strip()

            if "model" in sel_conf.get("key_in_settings", []):
                model_options = sel_conf.get("model_datas", [])
                if model_options:
                    console.print(f"\n[cyan]Suggested Models:[/] {', '.join(model_options[:5])}")
                sel_conf["model"] = Prompt.ask(self.i18n.get("prompt_temp_model") or "Model", default=sel_conf.get("model", "")).strip()

            thread_count = IntPrompt.ask(
                self.i18n.get("msg_thread_count") or "并发线程数",
                default=5
            )
            sel_conf["thread_counts"] = thread_count

            sel_conf["target_platform"] = sel_tag
            console.print(f"[green]{self.i18n.get('msg_temp_api_ok') or '临时配置已生效'}[/green]")
            return sel_conf

        except Exception as e:
            console.print(f"[red]配置临时API失败: {e}[/red]")
            return None
