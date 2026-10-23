#!/usr/bin/env bash
# Run the optional local screen-classifier training stack inside Docker.
# The source screenshots are mounted read-only and remain outside the repository.
set -euo pipefail

repo_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$repo_dir"

if [[ -z "${TRAINING_DATA_DIR:-}" ]]; then
    if ! command -v powershell.exe >/dev/null || ! command -v wslpath >/dev/null; then
        echo "Set TRAINING_DATA_DIR to the labelled screens directory." >&2
        exit 2
    fi
    local_appdata="$(powershell.exe -NoProfile -Command '[Environment]::GetFolderPath("LocalApplicationData")' | tr -d '\r')"
    if [[ -z "$local_appdata" ]]; then
        echo "Could not resolve Windows LOCALAPPDATA; set TRAINING_DATA_DIR explicitly." >&2
        exit 2
    fi
    export TRAINING_DATA_DIR="$(wslpath -u "$local_appdata")/PokemonChampionsAssistant/private-training/screens"
fi

if [[ ! -d "$TRAINING_DATA_DIR" ]]; then
    echo "Training data directory does not exist: $TRAINING_DATA_DIR" >&2
    exit 2
fi

exec docker compose --profile training run --rm --build \
    --user "$(id -u):$(id -g)" \
    trainer \
    python scripts/train_screen_classifier.py \
    --data-dir /training/screens \
    --output-dir private-training/output \
    --epochs 12 \
    --device auto \
    --pretrained \
    "$@"
