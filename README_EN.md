# AiNiee CLI (Optimized Edition)

<div align="center">
  <img src="https://img.shields.io/badge/Interface-CLI%20%2F%20TUI-0078D4?style=for-the-badge&logo=windows-terminal&logoColor=white" alt="CLI">
  <img src="https://img.shields.io/badge/Runtime-uv-purple?style=for-the-badge&logo=python&logoColor=white" alt="uv">
  <img src="https://img.shields.io/badge/Status-Optimized-success?style=for-the-badge" alt="Status">
</div>

<br/>

[English](README_EN.md) | [简体中文](README.md)

**AiNiee CLI** is an engineering-focused refactor of the [AiNiee](https://github.com/NEKOparapa/AiNiee) core logic, designed for command-line environments.

This project introduces **`uv`**, a modern Python package manager, and implements significant stability optimizations for the underlying runtime. By taking control of IO streams and exception handling, we have built a robust TUI environment perfect for long-running tasks, headless server deployments, and automated workflows.

> **🚧 Note**: To minimize environment configuration issues, this project is deeply integrated with `uv`. One-click launch scripts (`Launch.bat` and `Launch.sh`) will be available in future updates.

---

## ✨ Key Optimizations

### 🛡️ Runtime Stability
*   **IO Stream Cleaning & Hijacking**: Refactored the capture logic for Standard Output/Error (Stdout/Stderr). This effectively blocks redundant noise from underlying dependencies (like TensorFlow or C++ bindings), preventing TUI tearing or crashes and ensuring log readability in headless environments.

### ⚙️ Workflow & Configuration
*   **Intelligent Format Input/Conversion**: An optimized input processing pipeline. Supports a fully automated closed-loop operation ("Identify -> Convert -> Translate -> Restore") for non-standard formats (e.g., .mobi/.azw3), significantly simplifying pre-processing steps.
*   **Multi-Profile System (Profiles)**: Introduces a profile management system. You can create, clone, and switch between multiple configuration sets (e.g., separating settings for "Fast Translation" vs. "High-Quality Polishing"). All profiles are stored in isolation.\n*   **Live Mission Control**: **[New]** Introduces a new TUI task status and control center, supporting:
    *   **Dynamic Concurrency Adjustment**: Real-time increase or decrease of concurrent threads using `+` and `-` keys during task execution.
    *   **API Key Hot-Swap**: Force API key rotation using the `K` key to handle specific API call limits.
    *   **System Status Monitoring**: A bottom status bar that displays real-time system operational status (Normal/Fixing/Warning/Error) and dynamically changes the panel border color.
    *   **Cost and Time Estimation**: Before task startup, automatically estimates total Token consumption, approximate cost for online APIs, and Estimated Time of Arrival (ETA), prominently displayed in the log pane (for reference only).
*   **Visual Interactive Experience**: A modern TUI built on `Rich`, featuring real-time performance monitoring (RPM/TPM), split-screen logging, and a BIOS-like interactive settings menu.

---

## 🚀 Quick Start

We highly recommend using **[uv](https://github.com/astral-sh/uv)** to launch this project for the fastest environment resolution and cleanest dependency isolation.

### 1. Install uv
If `uv` is not yet installed in your environment:

**Windows (PowerShell):**
```powershell
powershell -c "irm https://astral.sh/uv/install.ps1 | iex"
```

**Linux / macOS:**
```bash
curl -LsSf https://astral.sh/uv/install.sh | sh
```

### 2. Clone the Repository
```bash
git clone -b cli-enhanced https://github.com/YourRepo/AiNiee.git
cd AiNiee
```

### 3. Instant Launch
No need to manually create virtual environments or install pip dependencies. Simply run `uv run` to handle the environment automatically:

```bash
# Automatically sync dependencies and start the CLI (Interactive Menu)
uv run ainiee_cli.py
```

### 4. Command-Line Argument Launch (Non-Interactive CLI)
**[New]** This project supports launching translation or export tasks directly via command-line arguments, suitable for script integration and automation.

**Translation Task Example:**
```bash
uv run ainiee_cli.py translate "H:\\Novels\\MyBook.txt" -o "H:\\Novels\\MyBook_Output" -p MyProfile -s Japanese -t Chinese --resume --yes
```
**Argument Description:**
*   `translate` / `polish` / `export`: Specifies the task type.
*   `H:\\Novels\\MyBook.txt`: (Positional argument) Input file or folder path.
*   `-o, --output`: Specifies the output path.
*   `-p, --profile`: Specifies the configuration profile name (e.g., `Elysia`).
*   `-s, --source`: Specifies the source language.
*   `-t, --target`: Specifies the target language.
*   `--resume`: Automatically resumes the task if a cache is detected.
*   `--yes`: Non-interactive mode; automatically answers `yes` to all confirmation prompts.

---

## 📖 Menu Overview

Once launched, you can navigate the following features via the interactive menu:

*   **Start Translation / Polishing**: Core translation and polishing tasks (supports automatic resume).
*   **Export Only**: Quick export mode for existing cache files.
*   **Profiles**: **[New]** Center for switching and managing configuration profiles.
*   **Settings / API Settings**: Categorized parameter settings with hot-reload support.
*   **Glossary**: Preview and apply prompt templates.

---

## 🛠️ Architecture

This project utilizes a **Wrapper / Adapter** pattern:

*   **Core**: The original AiNiee core business logic remains unchanged.
*   **Adapter Layer**: `ainiee_cli.py` acts as an anti-corruption layer, responsible for environment isolation, exception interception, and resource scheduling.
*   **Runtime**: Managed by `uv` to ensure consistency and startup speed.

---

## ⚠️ Disclaimer

*   This project is an unofficial optimized CLI branch of AiNiee, focusing on operational experience and engineering stability.
*   The core translation algorithms remain consistent with the original version. Please adhere to the original usage license.
*   This tool is intended for personal learning and legal use only.

---

<div align="center">
  Made with 💻 and ☕ by ShadowLoveElysia
  <br>
  Based on the original work by NEKOparapa
</div>