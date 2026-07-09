# AiNiee-Next Text Quick Start Guide

This guide is written for first-time users who want to get AiNiee-Next running without guessing what each menu means. The example flow uses Windows and DeepSeek, but the overall process is similar for other online API providers.

DeepSeek is used as the example because it is inexpensive, practical, and good enough for many translation workflows. It is not the only supported provider. After you understand the setup flow, you can switch to OpenAI, Claude, Gemini, or another compatible platform.

If you do not have a DeepSeek API Key yet, read [DeepSeek API Key Guide](DEEPSEEK_API_KEY_EN.md) first.

## Before You Start: Why This Guide Uses CLI/TUI First

AiNiee-Next provides both CLI/TUI and WebUI operation modes. This guide starts with CLI/TUI, not because WebUI is unavailable or unimportant, but because CLI/TUI is easier for the first run.

CLI/TUI is the menu interface you see after launching `Launch.bat` or running `uv run ainiee_cli.py`. It looks like a black console window, but you do not need to write code. You choose numbered menu items, and the program walks you through the setup in a linear order: language, API, project settings, prompts, then translation.

WebUI is useful after you already understand the basic workflow. It is good for checking progress, managing queues, switching profiles, and monitoring a task from another device on the same local network. However, if you open WebUI before setting up API keys, models, input/output paths, and prompts, it can feel unclear where to begin.

Recommended order:

- **First run**: use CLI/TUI and complete one translation.
- **After one successful run**: use WebUI for monitoring, queue management, and remote access.
- **When your main machine is at home, in a dorm, on a server, or somewhere else on the LAN**: WebUI is a convenient remote control panel.

In short: CLI/TUI is best for learning the workflow; WebUI is best for monitoring and management after the workflow is clear.

## 1. Clone the Project with Git

Please clone the project with Git instead of downloading the ZIP archive. This makes future updates easier.

If Git is not installed yet, install [Git for Windows](https://git-scm.com/download/win) first. Then open a terminal in the folder where you want to keep the project and run:

```bash
git clone https://github.com/ShadowLoveElysia/AiNiee-Next.git
cd AiNiee-Next
```

On Windows, the files you usually need first are:

- `prepare.bat`: prepares the runtime environment.
- `Launch.bat`: starts AiNiee-Next.

## 2. Prepare the Runtime Environment

On Windows, double-click:

```text
prepare.bat
```

The script checks `uv`, creates the virtual environment, and installs dependencies.

When you see a message similar to:

```text
Environment is ready. You can now use Launch.bat to start AiNiee CLI.
```

the environment is ready. Press any key to close the preparation window.

On Linux or macOS:

```bash
chmod +x prepare.sh
./prepare.sh
```

## 3. Start AiNiee-Next

On Windows, double-click:

```text
Launch.bat
```

On Linux or macOS:

```bash
./Launch.sh
```

If the program says that the Web build package is missing, you can enter `0` to skip it for now. This only skips the WebUI package. It does not block CLI/TUI translation, API setup, or project setup. You can install or build the WebUI package later when you need the browser control panel.

## 4. First-Time Setup Wizard

On the first run, AiNiee-Next may open a quick setup wizard. For a basic DeepSeek setup, use this order:

1. Interface language: choose Simplified Chinese, English, or your preferred language.
2. Source language: use `auto` if you are unsure, or choose the actual source language.
3. Target language: use `Chinese` if you want Simplified Chinese output.
4. API type: choose **Online API preset**.
5. Provider preset: choose **DeepSeek**.

After selecting DeepSeek, the wizard will ask for API information.

## 5. Enter the DeepSeek API Key and Model

In the DeepSeek API configuration screen, fill in:

- **API Key**: paste the key you created on the DeepSeek Open Platform.
- **Model**: use the project preset model first, or choose another DeepSeek model supported by your account.
- **API URL**: usually the preset value is already correct.

For DeepSeek, the preset API URL is usually:

```text
https://api.deepseek.com/v1
```

When pasting the API Key, the console may not display the full key because the program protects sensitive values. This is normal. Paste it once, confirm, and continue.

Important: keep your API Key private. Anyone who gets your key can spend your account balance.

## 6. Verify the API

After saving the API configuration, use the menu option similar to:

```text
Verify current API
```

If the API is configured correctly, the program should return a normal test response.

If verification fails:

- Check that the API Key is copied correctly.
- Check whether the DeepSeek account has enough balance.
- Check whether the selected model name is valid.
- Check whether the API URL is correct.
- If you see a compatibility error, switch SDK Request Mode in API settings.

For DeepSeek, if the default HTTPX request mode fails with a 404-like error, go to API settings and choose:

```text
SDK Request Mode
```

This option cycles `HTTPX -> OpenAI SDK -> Anthropic SDK -> Google SDK -> HTTPX`. For DeepSeek, switch it to `OpenAI SDK`, then verify the API again. `Anthropic SDK` means the Anthropic protocol; it is not limited to Claude models. `Google SDK` means the official Google GenAI SDK.

## 7. Configure Project Settings

Return to the main menu and enter **Project Settings**.

The most important settings for a first run are:

- **Input path**: the file or folder you want to translate.
- **Output path**: where translated files will be saved.
- **Source language**: use `auto` or the actual source language.
- **Target language**: for Chinese output, use `Chinese`.
- **Lines per request**: start with `20`.
- **Previous context lines**: start with `3`.
- **Request timeout**: use `60` or `120` seconds.
- **Thread count / concurrency**: start with `5` to `10`.

Do not start with extreme concurrency. First make sure the API, paths, and output format work. After one successful test, increase concurrency gradually.

For DeepSeek, users with stable network and sufficient balance can later try higher values such as `20`, `30`, or `50`, but only if the success rate stays high.

## 8. Choose and Apply a Prompt

Return to the main menu and enter the prompt or glossary settings section.

For the first run, choose a default translation prompt such as:

```text
common_system_zh.txt
```

Preview it, apply it, and return to the main menu.

Do not spend too much time writing a custom prompt before the first test. First confirm the toolchain works. After that, improve prompts, glossary, character profiles, and style settings.

For deeper prompt and glossary guidance, read:

[Prompt, Glossary, Polishing, and Advanced Settings Guide](TRANSLATION_WORKFLOW_GUIDE_EN.md)

## 9. Start Translation

Return to the main menu and choose **Start Translation**.

Depending on the file selector, you may be able to:

- choose a file by index,
- enter a full file path,
- enter a folder path,
- or type `q` to cancel.

For the first test, use a small file or a small project. Do not translate an entire long novel before confirming that the API, prompt, glossary, and output format are correct.

During translation, the TUI will show status information such as:

- current file,
- progress,
- thread count,
- RPM,
- TPM,
- success rate,
- error rate,
- estimated time.

If errors keep repeating and the task stops, do not panic. Lower the thread count, check the API settings, and run the same file again. The cache and resume logic can often continue from already processed content.

## 10. Check the Output

After the task finishes, the summary will show the input file and output directory. Open the output directory and check the translated file.

For EPUB files, open the result in an ebook reader. For subtitles, check timing and line length. For game scripts or JSON-like files, check that variables, tags, keys, and control symbols are still intact.

## 11. What to Learn Next

After your first successful translation, continue with these topics:

- DeepSeek API Key creation: [DeepSeek API Key Guide](DEEPSEEK_API_KEY_EN.md)
- Prompt and glossary writing: [Prompt, Glossary, Polishing, and Advanced Settings Guide](TRANSLATION_WORKFLOW_GUIDE_EN.md)
- WebUI monitoring and queue management
- Profiles for separate projects
- Polishing workflow
- MCP integration for LLM clients

The safest learning path is:

1. Run one small translation.
2. Add a small glossary.
3. Adjust the prompt.
4. Translate a larger sample.
5. Use queue, WebUI, or MCP only after the basic workflow is stable.
