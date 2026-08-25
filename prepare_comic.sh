#!/bin/bash

set -euo pipefail

PROJECT_ROOT="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
cd "$PROJECT_ROOT"

BACKEND="${1:-}"
if [ -z "$BACKEND" ]; then
    case "$(uname -s)" in
        Darwin)
            BACKEND="metal"
            ;;
        *)
            BACKEND="cpu"
            ;;
    esac
fi

case "$BACKEND" in
    cpu)
        REQUIREMENTS_FILE="ModuleFolders/MangaCore/runtime/requirements_cpu.txt"
        ;;
    gpu)
        REQUIREMENTS_FILE="ModuleFolders/MangaCore/runtime/requirements_gpu.txt"
        ;;
    metal)
        REQUIREMENTS_FILE="ModuleFolders/MangaCore/runtime/requirements_metal.txt"
        ;;
    *)
        echo "[ERROR] Unsupported backend: $BACKEND"
        echo "Usage: ./prepare_comic.sh [cpu|gpu|metal]"
        exit 1
        ;;
esac

MAIN_VENV_DIR="$PROJECT_ROOT/.venv"
MAIN_PYTHON="$MAIN_VENV_DIR/bin/python"

if [ ! -f "$REQUIREMENTS_FILE" ]; then
    echo "[ERROR] Missing requirements file: $REQUIREMENTS_FILE"
    exit 1
fi

if [ ! -d "$PROJECT_ROOT/ModuleFolders/MangaCore/runtime" ]; then
    echo "[ERROR] ModuleFolders/MangaCore/runtime is missing from the project root."
    exit 1
fi

echo "[1/4] Checking for uv..."
if ! command -v uv &> /dev/null; then
    echo "uv not found. Starting automatic installation..."
    curl -LsSf https://astral.sh/uv/install.sh | sh

    if [ -f "$HOME/.cargo/env" ]; then
        # shellcheck disable=SC1090
        source "$HOME/.cargo/env"
    fi

    if ! command -v uv &> /dev/null; then
        echo "[ERROR] uv installation failed. Please install it manually from https://astral.sh/uv"
        exit 1
    fi
    echo "uv installed successfully."
else
    echo "uv is already installed."
fi

echo "[2/4] Preparing main AiNiee runtime environment..."
uv venv "$MAIN_VENV_DIR" --python 3.12 --allow-existing

echo "[2/4] Repairing broken manga runtime package metadata..."
"$MAIN_PYTHON" "$PROJECT_ROOT/ModuleFolders/MangaCore/runtime/repair_runtime_metadata.py"

echo "[3/4] Installing manga runtime dependencies ($BACKEND)..."
uv pip install --python "$MAIN_PYTHON" -r "ModuleFolders/MangaCore/runtime/requirements_common.txt"

echo "[3/4] Installing visual runtime packages ($BACKEND)..."
uv pip uninstall --python "$MAIN_PYTHON" torch torchvision torchaudio onnxruntime onnxruntime-gpu || true
case "$BACKEND" in
    gpu)
        uv pip install --python "$MAIN_PYTHON" --default-index https://pypi.org/simple --reinstall \
            https://download.pytorch.org/whl/cu128/torch/torch-2.8.0%2Bcu128-cp312-cp312-manylinux_2_28_x86_64.whl \
            https://download.pytorch.org/whl/cu128/torchvision/torchvision-0.23.0%2Bcu128-cp312-cp312-manylinux_2_28_x86_64.whl \
            onnxruntime-gpu==1.20.2
        ;;
    cpu)
        uv pip install --python "$MAIN_PYTHON" --default-index https://pypi.org/simple --reinstall \
            https://download.pytorch.org/whl/cpu/torch/torch-2.8.0%2Bcpu-cp312-cp312-manylinux_2_28_x86_64.whl \
            https://download.pytorch.org/whl/cpu/torchvision/torchvision-0.23.0%2Bcpu-cp312-cp312-manylinux_2_28_x86_64.whl \
            onnxruntime==1.20.1
        ;;
    *)
        uv pip install --python "$MAIN_PYTHON" -r "$REQUIREMENTS_FILE"
        ;;
esac

echo "[4/4] Downloading default MangaCore model assets with requests..."
"$MAIN_PYTHON" "$PROJECT_ROOT/ModuleFolders/Service/HttpService/ModelDownload.py"

echo "[Done] MangaCore dependencies and assets are ready in $MAIN_VENV_DIR"
chmod +x Launch.sh prepare_comic.sh
