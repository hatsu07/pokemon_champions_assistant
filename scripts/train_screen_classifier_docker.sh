#!/usr/bin/env bash
# Training screenshots are local read-only mounts; outputs remain git-ignored.
set -euo pipefail

repo_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$repo_dir"

gpu=0
probe=0
archive=0
args=()
for argument in "$@"; do
    case "$argument" in
        --gpu) gpu=1 ;;
        --check-gpu) probe=1 ;;
        --archive-output) archive=1 ;;
        --device|--device=*|--data-dir|--data-dir=*|--output-dir|--output-dir=*)
            echo "Wrapper controls device/data/output; use --gpu for CUDA." >&2
            exit 2
            ;;
        *) args+=("$argument") ;;
    esac
done
if ((probe && !gpu)); then
    echo "Use --gpu --check-gpu for the GPU diagnostic." >&2
    exit 2
fi

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
TRAINING_DATA_DIR="$(cd "$TRAINING_DATA_DIR" && pwd -P)"
if [[ "$TRAINING_DATA_DIR" == "$repo_dir" || "$TRAINING_DATA_DIR" == "$repo_dir/"* ]]; then
    echo "Training data must stay outside the workspace." >&2
    exit 2
fi
export TRAINING_DATA_DIR
mkdir -p private-training/output private-training/.torch-cache

output="private-training/output"
if ((archive && !probe)); then
    # Separate each training model/manifest for safe manual comparison.
    run_dir="$(mktemp -d "private-training/output/run-$(date -u +%Y%m%dT%H%M%SZ)-XXXXXXXX")"
    output="$run_dir"
    printf '[trainer] archived_output=%s\n' "$output" >&2
fi
profile=training
service=trainer
device=auto
if ((gpu)); then
    profile=training-gpu
    service=trainer-gpu
    device=cuda
fi
printf '[trainer] requested_device=%s\n' "$device" >&2
if ((probe)); then
    exec docker compose --profile "$profile" run --rm --build \
        --user "$(id -u):$(id -g)" "$service" python scripts/check_training_gpu.py
fi
exec docker compose --profile "$profile" run --rm --build \
    --user "$(id -u):$(id -g)" "$service" \
    python scripts/train_screen_classifier.py \
    --data-dir /training/screens \
    --output-dir "$output" \
    --epochs 12 \
    --device "$device" \
    "${args[@]}"
