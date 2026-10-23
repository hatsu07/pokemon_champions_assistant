"""Verify actual CUDA availability *inside* the opt-in trainer-gpu container."""
from __future__ import annotations
import json
import torch

def main() -> int:
    available = bool(torch.cuda.is_available())
    info = {
        "cuda_available": available,
        "torch": torch.__version__,
        "torch_cuda_runtime": torch.version.cuda,
        "visible_gpus": torch.cuda.device_count() if available else 0,
    }
    if available:
        device = torch.cuda.get_device_properties(0)
        info["device_name"] = device.name
        info["vram_mib"] = round(device.total_memory / 1048576)
    print(json.dumps(info, indent=2), flush=True)
    if not available:
        print("CUDA unavailable in Docker; check Docker Desktop WSL2 GPU integration.", flush=True)
        return 2
    return 0

if __name__ == "__main__":
    raise SystemExit(main())
