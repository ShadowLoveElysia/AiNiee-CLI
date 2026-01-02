@echo off
setlocal
title AiNiee CLI - Prepare Environment

echo [1/3] Checking for uv...
uv --version >nul 2>&1
if %errorlevel% neq 0 (
    echo uv not found. Starting automatic installation...
    powershell -ExecutionPolicy ByPass -c "irm https://astral.sh/uv/install.ps1 | iex"
    
    REM Add uv to current session PATH
    set "PATH=%USERPROFILE%\.cargo\bin;%PATH%"
    
    uv --version >nul 2>&1
    if %errorlevel% neq 0 (
        echo [ERROR] uv installation failed. Please install it manually from https://astral.sh/uv
        pause
        exit /b 1
    )
    echo uv installed successfully.
) else (
    echo uv is already installed.
)

echo [2/3] Syncing project dependencies...
uv sync

if %errorlevel% neq 0 (
    echo [ERROR] Dependency sync failed.
    pause
    exit /b 1
)

echo [3/3] Done!
echo Environment is ready. You can now use Launch.bat to start AiNiee CLI.
pause
