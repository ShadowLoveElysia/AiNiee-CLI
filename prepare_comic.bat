@echo off
setlocal EnableExtensions
title AiNiee CLI - Prepare Comic Runtime
pushd "%~dp0" >nul

set "BACKEND=%~1"
set "BACKEND_REASON="
if "%BACKEND%"=="" goto autodetect_backend
if /I "%BACKEND%"=="cpu" goto backend_ok
if /I "%BACKEND%"=="gpu" goto backend_ok
if /I "%BACKEND%"=="cuda" (
    set "BACKEND=gpu"
    goto backend_ok
)
if /I "%BACKEND%"=="metal" goto backend_ok

echo [ERROR] Unsupported backend: %BACKEND%
echo Usage: prepare_comic.bat [cpu^|gpu^|cuda^|metal]
popd
pause
exit /b 1

:autodetect_backend
set "BACKEND=cpu"
set "BACKEND_REASON=No NVIDIA GPU detected; using CPU runtime."

where nvidia-smi >nul 2>&1
if not errorlevel 1 (
    set "BACKEND=gpu"
    set "BACKEND_REASON=NVIDIA GPU detected via nvidia-smi; using GPU(CUDA) runtime."
    goto backend_ok
)

for /f "skip=1 tokens=*" %%G in ('wmic path win32_VideoController get name 2^>nul') do (
    echo %%G | findstr /I "NVIDIA" >nul 2>&1
    if not errorlevel 1 (
        set "BACKEND=gpu"
        set "BACKEND_REASON=NVIDIA GPU detected via Windows video controller; using GPU(CUDA) runtime."
        goto backend_ok
    )
)

powershell -NoProfile -ExecutionPolicy Bypass -Command "if (Get-CimInstance Win32_VideoController | Where-Object { $_.Name -match 'NVIDIA' } | Select-Object -First 1) { exit 0 } exit 1" >nul 2>&1
if not errorlevel 1 (
    set "BACKEND=gpu"
    set "BACKEND_REASON=NVIDIA GPU detected via PowerShell CIM; using GPU(CUDA) runtime."
    goto backend_ok
)

:backend_ok
set "REQUIREMENTS_FILE="
if /I "%BACKEND%"=="cpu" set "REQUIREMENTS_FILE=ModuleFolders\MangaCore\runtime\requirements_cpu.txt"
if /I "%BACKEND%"=="gpu" set "REQUIREMENTS_FILE=ModuleFolders\MangaCore\runtime\requirements_gpu.txt"
if /I "%BACKEND%"=="metal" set "REQUIREMENTS_FILE=ModuleFolders\MangaCore\runtime\requirements_metal.txt"
set "BACKEND_LABEL=%BACKEND%"
if /I "%BACKEND%"=="cpu" set "BACKEND_LABEL=CPU"
if /I "%BACKEND%"=="gpu" set "BACKEND_LABEL=GPU(CUDA)"
if /I "%BACKEND%"=="metal" set "BACKEND_LABEL=Metal"
if defined BACKEND_REASON echo [Auto] %BACKEND_REASON%
echo [Backend] %BACKEND_LABEL%

if not exist "%REQUIREMENTS_FILE%" (
    echo [ERROR] Missing requirements file: %REQUIREMENTS_FILE%
    popd
    pause
    exit /b 1
)

if not exist "ModuleFolders\MangaCore\runtime" (
    echo [ERROR] ModuleFolders\MangaCore\runtime is missing from the project root.
    popd
    pause
    exit /b 1
)

set "MAIN_VENV=%CD%\.venv-win"
set "MAIN_PYTHON=%MAIN_VENV%\Scripts\python.exe"

echo [1/4] Checking for uv...
uv --version >nul 2>&1
if errorlevel 1 (
    echo uv not found. Starting automatic installation...
    powershell -ExecutionPolicy ByPass -c "irm https://astral.sh/uv/install.ps1 | iex"

    set "PATH=%USERPROFILE%\.cargo\bin;%PATH%"

    uv --version >nul 2>&1
    if errorlevel 1 (
        echo [ERROR] uv installation failed. Please install it manually from https://astral.sh/uv
        popd
        pause
        exit /b 1
    )
    echo uv installed successfully.
) else (
    echo uv is already installed.
)

echo [2/4] Preparing main AiNiee runtime environment...
uv venv "%MAIN_VENV%" --python 3.12 --allow-existing
if errorlevel 1 (
    echo [ERROR] Failed to prepare main AiNiee runtime environment.
    popd
    pause
    exit /b 1
)

echo [2/4] Repairing broken manga runtime package metadata...
"%MAIN_PYTHON%" "%CD%\ModuleFolders\MangaCore\runtime\repair_runtime_metadata.py"
if errorlevel 1 (
    echo [ERROR] Failed to repair manga runtime package metadata.
    popd
    pause
    exit /b 1
)

echo [3/4] Installing manga runtime dependencies (%BACKEND_LABEL%)...
uv pip install --python "%MAIN_PYTHON%" -r "ModuleFolders\MangaCore\runtime\requirements_common.txt"
if errorlevel 1 (
    echo [ERROR] Failed to install common manga runtime dependencies.
    popd
    pause
    exit /b 1
)

echo [3/4] Installing visual runtime packages (%BACKEND_LABEL%)...
uv pip uninstall --python "%MAIN_PYTHON%" torch torchvision torchaudio onnxruntime onnxruntime-gpu
if /I "%BACKEND%"=="gpu" (
    uv pip install --python "%MAIN_PYTHON%" --default-index https://pypi.org/simple --reinstall ^
        https://download.pytorch.org/whl/cu128/torch/torch-2.8.0%%2Bcu128-cp312-cp312-win_amd64.whl ^
        https://download.pytorch.org/whl/cu128/torchvision/torchvision-0.23.0%%2Bcu128-cp312-cp312-win_amd64.whl ^
        onnxruntime-gpu==1.20.2
) else if /I "%BACKEND%"=="cpu" (
    uv pip install --python "%MAIN_PYTHON%" --default-index https://pypi.org/simple --reinstall ^
        https://download.pytorch.org/whl/cpu/torch/torch-2.8.0%%2Bcpu-cp312-cp312-win_amd64.whl ^
        https://download.pytorch.org/whl/cpu/torchvision/torchvision-0.23.0%%2Bcpu-cp312-cp312-win_amd64.whl ^
        onnxruntime==1.20.1
) else (
    uv pip install --python "%MAIN_PYTHON%" -r "%REQUIREMENTS_FILE%"
)
if errorlevel 1 (
    echo [ERROR] Failed to install visual runtime packages.
    popd
    pause
    exit /b 1
)

echo [4/4] Downloading default MangaCore model assets with requests...
"%MAIN_PYTHON%" "%CD%\ModuleFolders\Service\HttpService\ModelDownload.py"
if errorlevel 1 (
    echo [ERROR] Failed to download default MangaCore model assets.
    popd
    pause
    exit /b 1
)

echo [Done] MangaCore dependencies and assets are ready in "%MAIN_VENV%"
popd
pause
exit /b 0
