# AiNiee-Next

<div align="center">
  <img src="https://img.shields.io/badge/Interface-CLI%20%2F%20TUI-0078D4?style=for-the-badge&logo=windows-terminal&logoColor=white" alt="CLI">
  <img src="https://img.shields.io/badge/Runtime-uv-purple?style=for-the-badge&logo=python&logoColor=white" alt="uv">
  <img src="https://img.shields.io/badge/Status-Stable-success?style=for-the-badge" alt="Status">
</div>

<br/>

[简体中文](README.md) | [English](README_EN.md) | [繁體中文](Docs/README_zh_CNTW.md) | [日本語](Docs/README_JA.md) | [한국어](Docs/README_KO.md) | [Русский](Docs/README_RU.md) | [Español](Docs/README_ES.md)

**AiNiee-Next** 是基于 [AiNiee](https://github.com/NEKOparapa/AiNiee) 核心翻译逻辑开发的命令行版本。项目使用 **uv** 管理 Python 环境，并针对长时间运行、批量任务、服务器部署和自动化使用做了大量改进。

项目以 CLI/TUI 为主要操作界面，同时提供 Web 控制面板、任务队列、插件系统和 MCP 服务，适合个人翻译、长篇内容处理和需要长期挂机的批量任务。

---

## 智能诊断与问题反馈

任务出现异常时，程序会收集错误堆栈、运行环境、API 平台、模型和最近的操作流程，并结合内置规则与可选的 LLM 分析，帮助判断问题来自 API、网络、配置、运行环境还是项目代码。对于疑似代码问题，还可以自动整理包含错误描述、环境信息、关键 traceback 和初步分析的 GitHub Issue，方便反馈和排查。

---

## 性能展示

**本项目为极致的性能释放和稳定性而生。**

下图展示了一个约 20,000 行的待翻译文件，在 50 并发线程下仅用约 4 分钟即可完成翻译任务：

<div align="center">
  <img src="README_IMG/50并发deepseek测试.png" alt="50并发性能测试" width="90%">
  <br>
  <em>50 并发 + DeepSeek API | 20k 行 | ~4 分钟完成 | 99.6% 成功率 | 397k TPM</em>
</div>

---

## 核心特性

- **稳定运行与错误恢复**：清理底层 I/O 输出，减少冗余日志对 TUI 的干扰，并支持异常拦截、自动重试和断点续传，适合长时间挂机运行。
- **跨平台支持**：可在 Windows、Linux、macOS 和 Android（Termux）上运行，也适合 Headless 服务器环境。
- **多种文件格式**：支持 Epub、Docx、Txt、Srt、Ass、Vtt、Lrc、Json、Po、Paratranz 等 20 多种格式，并可结合 Calibre 自动处理 `.mobi`、`.azw3`、`.kepub`、`.fb2` 等电子书格式。
- **任务与配置管理**：支持运行中调整并发、切换 API Key、启动 Web 监控、查看任务状态以及费用和完成时间预估，同时提供多 Profile 管理、配置热重载和可调整顺序的批量任务队列；队列运行时也能修改待处理任务，并会按顺序自动执行。
- **插件与翻译辅助**：可以通过插件扩展功能，并提供集中管理、RAG 历史译文参考和翻译检查，用于改善长篇翻译中的术语、文风一致性，并检测漏译、错译和格式异常。
- **上下文缓存**：支持 Anthropic、Google 和 Amazon Bedrock 的上下文缓存，可缓存系统提示词和术语表；当前 API 不兼容时会自动关闭并提示。
- **模型与 API 支持**：兼容主流在线 API、第三方中转服务和本地模型，会根据接口类型给出对应的参数提示，支持 DeepSeek R1、Claude 3.5 等推理模型，并提供多 API 故障转移、自动切换和可配置的触发阈值。
- **高并发处理**：基于 aiohttp 的异步请求模式支持 100 以上并发，可区分不可重试错误和临时错误，记录不同 API 的功能兼容情况，并在高并发时保护文件描述符、端口等系统资源；并发达到 15 时会提示启用异步模式。

---

## 快速开始

> 新用户建议先阅读：[图文快速上手教程](Docs/README_QUICK_START.md)；还没有 API Key 的用户可先看：[DeepSeek API Key 申请教程](Docs/DEEPSEEK_API_KEY.md)；想提升翻译质量可继续看：[提示词、术语表、润色与软件设置教程](Docs/TRANSLATION_WORKFLOW_GUIDE.md)

### 方式一：一键启动（推荐）

**1. 获取代码**
```bash
git clone https://github.com/ShadowLoveElysia/AiNiee-Next.git
cd AiNiee-Next
```

**2. 环境准备（首次运行）**

Windows:
```batch
双击 prepare.bat
```

Linux / macOS:
```bash
chmod +x prepare.sh && ./prepare.sh
```

**3. 启动应用**

Windows:
```batch
双击 Launch.bat
```

Linux / macOS:
```bash
./Launch.sh
```

---

### 方式二：手动配置

**1. 安装 uv**

Windows (PowerShell):
```powershell
powershell -c "irm https://astral.sh/uv/install.ps1 | iex"
```

Linux / macOS:
```bash
curl -LsSf https://astral.sh/uv/install.sh | sh
```

Android (Termux):
```bash
pkg update && pkg upgrade
pkg install python
pip install uv
```

**2. 获取代码并启动**
```bash
git clone https://github.com/ShadowLoveElysia/AiNiee-Next.git
cd AiNiee-Next
uv run ainiee_cli.py
```

---

## 命令行参数

支持通过命令行参数直接启动任务，适用于脚本集成与自动化。

**翻译任务示例：**
```bash
uv run ainiee_cli.py translate input.txt -o output_dir -p MyProfile -s Japanese -t Chinese --resume --yes
```

**队列任务示例：**
```bash
uv run ainiee_cli.py queue --queue-file my_queue.json --yes
```

**MCP 服务示例：**
```bash
uv run ainiee_cli.py mcp --mcp-transport stdio
```

**主要参数：**
- `translate` / `polish` / `export` / `queue` / `mcp`: 任务类型
- `-o, --output`: 输出路径
- `-p, --profile`: 配置 Profile 名称
- `-s, --source`: 源语言
- `-t, --target`: 目标语言
- `--type`: 项目类型 (Txt, Epub, MTool, RenPy 等)
- `--resume`: 自动恢复缓存任务
- `--yes`: 非交互模式
- `--threads`: 并发线程数
- `--platform`: 目标平台
- `--model`: 模型名称
- `--api-url`: API 地址
- `--api-key`: API 密钥
- `--mcp-transport`: MCP 传输模式，可选 `stdio` / `streamable-http` / `sse`

---

## Web 控制面板

本项目集成基于 React 构建的 Web 控制面板，已进入稳定阶段。

**启动方式：**
1. 运行 `uv run ainiee_cli.py` 进入主菜单
2. 选择 **15. Start Web Server**
3. 程序将自动启动服务（默认端口 8000）并打开浏览器

Web 服务默认仅监听 `127.0.0.1`，只能从本机访问。若需要从局域网或远程设备访问，请在 TUI 的项目设置 → 高级设置中开启 **局域网/远程访问**；该安全开关只在 TUI 设置列表中提供，Web 设置页无法修改。开关关闭时，每次从 TUI 启动 Web 服务都会显示一行黄色提示。

在没有 TUI 的服务器上，可以用下面的命令仅为本次无头 Web 进程开放远程监听：

```bash
uv run python Tools/TauriShell/tauri_web_host.py --host 0.0.0.0 --port 8000 --allow-remote-access
```

`--allow-remote-access` 仅对本次启动有效，不会写入 Profile。未传入该参数时，非 loopback 的 `--host` 会被拒绝。远程访问只应在可信网络中开启；若要暴露到公网，请额外配置 TLS、独立的反向代理认证和必要的网络访问控制。

**功能：**
- 可视化看板：实时图表展示 RPM、TPM 及任务进度
- 网络访问：开启 TUI 中的局域网/远程访问开关后，可从局域网或远程设备监控
- 配置管理：网页端创建、切换配置 Profile
- 队列管理：拖拽排序、实时编辑任务参数
- 插件中心：启用/禁用 RAG 等高级功能

> **开发说明**：Web 控制面板已稳定运行，但功能相对 TUI 模式较少。本项目以 CLI/TUI 交互为核心开发方向，Web 端功能更新将在后续版本中逐步跟进。

---

## MCP 服务

本项目提供可选的 MCP 服务模块，复用现有 WebServer 后端能力，并尽量覆盖全部 Web API 路由，以便在 MCP 客户端中获得接近 Web 面板的操作体验。
任何支持 MCP `stdio` 或 `streamable-http` 的 LLM 客户端，都可以直接接入本项目，不需要额外读取项目源码或手动拼接 Web API。

**启动方式：**
1. 命令行直启：`uv run ainiee_cli.py mcp --mcp-transport stdio`
2. 主菜单启动：进入主菜单后选择 **16. 启动 MCP 服务**

**说明：**
- MCP 服务是可选组件，缺失时不会影响主程序其他功能
- 每次启动 MCP 前都会检查必要组件与依赖
- 若缺少依赖，程序会提示当前系统可直接执行的完整安装命令
- 菜单启动默认使用后台 `streamable-http` 模式，等待 3 秒后返回菜单
- 如果修改了 `mcp_server_port`，请同步更新 MCP 客户端中的连接路由
- MCP 的 `streamable-http` / `sse` 监听使用同一个 TUI 高级设置 **局域网/远程访问**：默认只监听本机，开启后才允许局域网或远程连接。`stdio` 传输不受网络监听设置影响

**直接接入 LLM 客户端：**
1. 支持 `stdio` 的 MCP 客户端，可以直接把 AiNiee CLI 作为本地 MCP Server 接入。
如果客户端使用 `command + args` 配置格式，可参考下面这个通用模板：

```json
{
  "mcpServers": {
    "ainiee-cli": {
      "command": "uv",
      "args": [
        "run",
        "--directory",
        "H:\\小说\\AiNiee-CLI",
        "--isolated",
        "--no-project",
        "--quiet",
        "--with",
        "mcp",
        "--with",
        "fastapi",
        "--with",
        "uvicorn[standard]",
        "--with",
        "requests",
        "python",
        "Tools/MCPServer/server.py",
        "--transport",
        "stdio"
      ]
    }
  }
}
```

不同客户端的配置文件字段名可能略有差异，但核心信息通常就是 `command=uv` 加上上面的 `args`。
上面的路径请替换成你自己的项目目录。Linux / macOS 可把 `H:\\小说\\AiNiee-CLI` 替换成 `/path/to/AiNiee-CLI`。

2. 如果客户端只接受“原始命令”，可直接使用：

```bash
uv run --directory /path/to/AiNiee-CLI --isolated --no-project --quiet --with mcp --with fastapi --with uvicorn[standard] --with requests python Tools/MCPServer/server.py --transport stdio
```

3. Codex 通过 `stdio` 直连时，推荐直接使用项目内置 launcher：

```bash
codex mcp add ainiee-cli -- /path/to/AiNiee-CLI/Tools/MCPServer/codex_stdio_launcher.sh
```

首次启动如果依赖尚未缓存，建议在 `~/.codex/config.toml` 中给该 MCP 增加较大的超时，例如：

```toml
[mcp_servers.ainiee-cli]
startup_timeout_sec = 90
```

4. 支持 `streamable-http` 的 MCP 客户端，可以直接连接 AiNiee CLI 暴露出来的 MCP HTTP 路由。
先启动：

```bash
uv run ainiee_cli.py mcp --mcp-transport streamable-http
```

或者在主菜单选择 **16. 启动 MCP 服务**。

客户端侧如果使用 URL 配置格式，可参考：

```json
{
  "mcpServers": {
    "ainiee-cli": {
      "transport": "streamable-http",
      "url": "http://127.0.0.1:8765/mcp"
    }
  }
}
```

连接地址：

```text
本机地址: http://127.0.0.1:8765/mcp
局域网地址（需先开启 TUI 的局域网/远程访问）: http://<你的局域网IP>:8765/mcp
```

5. 如果启动 MCP 时提示缺少依赖，可以在项目根目录执行：

```bash
set "UV_PROJECT_ENVIRONMENT=%CD%\.venv-win" && uv --directory "%CD%" add "mcp" "fastapi" "uvicorn[standard]" "requests"
```

Linux / macOS 可使用：

```bash
UV_PROJECT_ENVIRONMENT="$(pwd)/.venv" uv --directory "$(pwd)" add 'mcp' 'fastapi' 'uvicorn[standard]' 'requests'
```

如果你把 `mcp_server_port` 改成了其他值，上面的 `8765` 也要同步替换。
如果项目目录里的 `.venv` 曾经在另一套系统下创建过，例如 WSL 生成后又在 Windows 下执行 `uv add`，建议先重建 `.venv`，否则容易出现 `lib64` / 符号链接相关报错。

**LLM 客户端建议首轮调用：**
- `get_mcp_usage_manual`
- `get_mcp_security_policy`
- `get_mcp_tool_categories`
- `get_mcp_tool_catalog(category="需要的分类")`
- `get_mcp_validation_checklist`

这些工具会直接告诉 LLM 当前 MCP 暴露了哪些能力、参数如何组织、哪些接口受限，以及为什么不能绕过 MCP 直连 WebUI。端点目录默认按分类读取，避免一次性把全部 Web API 端点注入上下文。

**MCP 安全要求：**
- LLM 严禁绕过 MCP，直接向 WebUI / localhost / 局域网端口发 HTTP 请求取数
- LLM 只能通过 MCP 工具访问项目能力
- MCP 读取到的 `api_key` / `access_key` / `secret_key` 会被脱敏
- MCP 读取敏感配置时会额外返回 `_mcp_security_notice`，明确说明这是权限限制，并禁止通过其他渠道绕过获取
- 脱敏占位符不是可用密钥，也不能当真实值写回配置或队列
- 敏感 Web API 路由要求有效的 Web UI 会话 cookie 或 MCP bridge token，裸 HTTP 直连会被拒绝

完整的客户端说明文档见：
- `Tools/MCPServer/MCP_CLIENT_GUIDE.md`

---

## 架构说明

本项目采用 Wrapper / Adapter 模式：

- **Core**: 保持原版 AiNiee 的核心业务逻辑
- **Adapter Layer**: `ainiee_cli.py` 作为防腐层，负责环境隔离与异常拦截
- **Runtime**: 由 uv 托管，确保依赖环境一致性

---

## 漫画处理参考

本项目的 MangaCore 漫画子系统采用“自动跑批”和“人工精修”分层设计，不把整册自动翻译任务与页级编辑工作台混成同一个入口。

**全自动漫画翻译工作流** 主要参考 `manga-translator-ui-main` 所代表的工作流，以及其上游 **hgmzhn / manga-translator-ui**：

- GitHub: https://github.com/hgmzhn/manga-translator-ui
- Gitee 备份: https://gitee.com/hgmzhn/manga-translator-ui

该部分主要参考其“导入图片/压缩包 -> 文本检测 -> OCR -> 翻译 -> 修补 -> 嵌字渲染 -> 导出”的阶段拆分、运行时资产组织和整册自动处理思路。AiNiee-Next 侧会以 `translate ... --manga`、Web 任务页 Manga Mode 和 `MangaCore` 批处理管线承载这一类少交互、可挂机的自动任务。

**人工精修与漫画编辑器逻辑** 主要参考 **mayocream / Koharu**：

- GitHub: https://github.com/mayocream/koharu

该部分主要参考 Koharu 的人工精修思路，包括工程/页面/文本块、图层化页面状态、当前页局部重跑、文本块位置与样式微调、修补结果检查、可编辑成品导出等精修链路。

后续若参考、接入或复用相关核心模块，本项目会持续保留来源说明与鸣谢信息，并遵守对应开源协议。

---

## 免责声明

- 本项目是 AiNiee 的非官方优化分支，侧重于运行体验与工程稳定性
- 核心翻译算法与原版保持一致，请遵守原版使用协议
- 本工具仅供个人学习与合法用途使用

---

## 支持 AiNiee-Next

如果这个项目对你有帮助喵，欢迎通过爱发电或赞赏码支持 AiNiee-Next 版本的持续开发与维护。支持人会被加入到项目鸣谢列表；无论金额多少，都是一份暖暖的心意喵，也是加速项目开发的动力。

- 爱发电: https://ifdian.net/a/Next_ZhiXie

<div align="center">
  <img src="README_IMG/赞赏码.png" alt="赞赏码" width="320">
</div>

---

<div align="center">
  Made by ShadowLoveElysia
  <br>
  Based on the original work by NEKOparapa
</div>
