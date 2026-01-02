#!/bin/bash

echo "[1/3] Checking for uv..."
if ! command -v uv &> /dev/null; then
    echo "uv not found. Starting automatic installation..."
    curl -LsSf https://astral.sh/uv/install.sh | sh
    
    # Source the cargo env for current session
    source $HOME/.cargo/env
    
    if ! command -v uv &> /dev/null; then
        echo "[ERROR] uv installation failed. Please install it manually from https://astral.sh/uv"
        exit 1
    fi
    echo "uv installed successfully."
else
    echo "uv is already installed."
fi

echo "[2/3] Syncing project dependencies..."
uv sync

if [ $? -ne 0 ]; then
    echo "[ERROR] Dependency sync failed."
    exit 1
fi

echo "[3/3] Done!"
echo "Environment is ready. You can now use ./Launch.sh to start AiNiee CLI."
chmod +x Launch.sh
