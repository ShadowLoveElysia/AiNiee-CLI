@echo off
setlocal
title AiNiee CLI

uv --version >nul 2>&1
if %errorlevel% neq 0 (
    REM Check if it was just installed and in the default cargo path
    if exist "%USERPROFILE%\.cargo\bin\uv.exe" (
        set "PATH=%USERPROFILE%\.cargo\bin;%PATH%"
    ) else (
        echo [ERROR] uv is not installed or not in PATH.
        echo Please run 'prepare.bat' first to set up the environment.
        echo.
        pause
        exit /b 1
    )
)

echo Starting AiNiee CLI...
uv run ainiee_cli.py
pause
