# AiNiee CLI (Optimized Edition)

<div align="center">
  <img src="https://img.shields.io/badge/Interface-CLI%20%2F%20TUI-0078D4?style=for-the-badge&logo=windows-terminal&logoColor=white" alt="CLI">
  <img src="https://img.shields.io/badge/Runtime-uv-purple?style=for-the-badge&logo=python&logoColor=white" alt="uv">
  <img src="https://img.shields.io/badge/Status-Optimized-success?style=for-the-badge" alt="Status">
</div>

<br/>

[English](README_EN.md) | [简体中文](README.md)

**AiNiee CLI** 是针对 [AiNiee](https://github.com/NEKOparapa/AiNiee) 核心逻辑进行工程化重构的命令行版本。

本项目引入了现代化的 Python 包管理工具 **`uv`**，并对底层运行时进行了多项稳定性优化。通过接管底层 IO 流与异常处理，我们构建了一个适合长时间挂机、服务器部署及自动化工作流的高健壮性 TUI 环境。

> **🚧 提示**：为了降低环境配置难度，本项目深度集成 `uv`。后续版本将直接提供 `Launch.bat` 和 `Launch.sh` 一键启动脚本，敬请期待。

---

## ✨ 核心优化 (Key Optimizations)

### 🛡️ 运行时稳定性 (Runtime Stability)
*   **IO 流清洗与接管**：重构了标准输出流 (Stdout/Stderr) 的捕获逻辑，有效屏蔽了底层依赖库（如 TensorFlow/C++ Bindings）的冗余日志，防止 TUI 界面撕裂或崩溃，确保在 Headless 环境下的日志可读性。

### ⚙️ 流程与配置管理 (Workflow & Config)
*   **智能化格式输入/转换**：优化了输入处理管线，支持对非标准格式（.mobi/.azw3等）进行全自动的“识别 -> 转换 -> 翻译 -> 还原”闭环操作，大幅简化了前置处理步骤。
*   **多配置文件系统 (Profiles)**：引入 Profile 概念，支持创建、克隆、切换多套配置方案（如区分“快速翻译”与“精细润色”），所有配置隔离存储，互不干扰。
*   **实时任务指挥中心 (Live Mission Control)**： **[New]** 引入全新的 TUI 任务状态与控制中心，支持：
    *   **动态并发调整**：在任务运行时，通过键盘 `+` 和 `-` 键实时增减并发线程数。
    *   **API Key 热切换**：通过键盘 `K` 键强制触发 API Key 轮换，应对特定 API 的调用限制。
    *   **系统状态监控**：底部状态栏实时显示系统运行状态（正常/修复中/警告/错误），并联动改变面板边框颜色。
    *   **预估成本与时间**：任务启动前，自动预估本次任务的总 Token 消耗、在线 API 预估费用及预计完成时间（ETA），并在日志窗格中醒目提示（仅供参考）。
*   **可视化交互体验**：基于 `Rich` 构建的现代化 TUI，提供实时性能监控（RPM/TPM）、双屏日志分离及类 BIOS 的交互式设置菜单。

---

## 🚀 快速开始 (Quick Start)

本项目推荐使用 **[uv](https://github.com/astral-sh/uv)** 进行启动，以获得最快的环境解析速度和最纯净的依赖隔离。

### 1. 安装 uv
如果你的环境中尚未安装 `uv`，请执行：

**Windows (PowerShell):**
```powershell
powershell -c "irm https://astral.sh/uv/install.ps1 | iex"
```

**Linux / macOS:**
```bash
curl -LsSf https://astral.sh/uv/install.sh | sh
```

### 2. 获取代码
```bash
git clone -b cli-enhanced https://github.com/YourRepo/AiNiee.git
cd AiNiee
```

### 3. 极速启动
无需手动创建虚拟环境或安装 pip 依赖，直接使用 `uv run` 即可自动处理环境并启动工具：

```bash
# 自动同步依赖并启动 CLI (交互式菜单)
uv run ainiee_cli.py
```

### 4. 控制台参数启动 (Non-Interactive CLI)
**[New]** 本项目支持通过命令行参数直接启动翻译或导出任务，适用于脚本集成与自动化。

**翻译任务示例：**
```bash
uv run ainiee_cli.py translate H:\小说\我的书.txt -o H:\小说\我的书_Output -p MyProfile -s Japanese -t Chinese --resume --yes
```
**参数说明：**
*   `translate` / `polish` / `export`: 指定任务类型。
*   `H:\小说\我的书.txt`: (位置参数) 输入文件或文件夹路径。
*   `-o, --output`: 指定输出路径。
*   `-p, --profile`: 指定配置 Profile 名称（例如 `Elysia`）。
*   `-s, --source`: 指定源语言。
*   `-t, --target`: 指定目标语言。
*   `--resume`: 如果检测到缓存，自动恢复任务。
*   `--yes`: 非交互模式，对所有确认提示自动回答 `yes`。

---

## 📖 菜单功能概览

启动后，您将通过交互式菜单操作以下功能：

*   **Start Translation / Polishing**: 核心翻译与润色任务（支持自动断点续传）。
*   **Export Only**: 针对已有缓存的快速导出模式。
*   **Profiles**: **[New]** 配置方案切换与管理中心。
*   **Settings / API Settings**: 经过分类整理的参数设置，支持热重载。
*   **Glossary**: 提示词模板的预览与应用。

---

## 🛠️ 架构说明

本项目采用了 **Wrapper / Adapter** 模式：

*   **Core**: 保持原版 AiNiee 的核心业务逻辑不变。
*   **Adapter Layer**: `ainiee_cli.py` 作为防腐层，负责环境隔离、异常拦截与资源调度。
*   **Runtime**: 由 `uv` 托管，确保依赖环境的一致性与启动速度。

---

## ⚠️ 免责声明

*   本项目是 AiNiee 的非官方 CLI 优化分支，侧重于运行体验与工程稳定性。
*   核心翻译算法与原版保持一致，请遵守原版的使用协议。
*   本工具仅供个人学习与合法用途使用。

---

<div align="center">
  Made with 💻 and ☕ by ShadowLoveElysia
  <br>
  Based on the original work by NEKOparapa
</div>
```
