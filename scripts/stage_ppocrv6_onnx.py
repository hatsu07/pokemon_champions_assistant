"""Stage official PP-OCRv6 small ONNX models and vocabulary for Windows onedir.

Build-time network only. Runtime is entirely offline. A staged bundle is
reusable across source tests and the PyInstaller GUI.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
from urllib.request import urlopen

# Pinned official Hugging Face revisions (not mutable main).
MODELS = {
    "det": ("PaddlePaddle/PP-OCRv6_small_det_onnx", "28fe589"),
    "rec": ("PaddlePaddle/PP-OCRv6_small_rec_onnx", "b8f84f0"),
}

def download(repo: str, rev: str, filename: str) -> bytes:
    url = f"https://huggingface.co/{repo}/resolve/{rev}/{filename}"
    with urlopen(url, timeout=90) as response:
        return response.read()

def stage(destination: Path):
    try:
        import yaml
    except ImportError as exc:
        raise RuntimeError("Build staging requires PyYAML; install it only in CI/build") from exc
    destination.mkdir(parents=True, exist_ok=True)
    hashes = {}
    for name, (repo, rev) in MODELS.items():
        data = download(repo, rev, "inference.onnx")
        if len(data) < 100_000:
            raise RuntimeError(f"invalid {name} model payload")
        path = destination / f"pp-ocrv6-small-{name}.onnx"
        path.write_bytes(data)
        hashes[path.name] = hashlib.sha256(data).hexdigest()
        config = yaml.safe_load(download(repo, rev, "inference.yml"))
        if name == "rec":
            post = config.get("PostProcess") or {}
            vocab = post.get("character_dict")
            if not isinstance(vocab, list) or len(vocab) < 1000:
                raise RuntimeError("official recognition character_dict missing/invalid")
            if not all(isinstance(ch, str) and ch for ch in vocab):
                raise RuntimeError("official character_dict contains invalid entries")
            # Official CTCLabelDecode reserves index 0 for blank.
            # Preserve the exact sequence supplied by the model metadata.
            (destination / "ppocrv6_dict.txt").write_text(
                "\n".join(vocab) + "\n", encoding="utf-8")
            operators = (config.get("PreProcess") or {}).get("transform_ops") or []
            recog = next((op["RecResizeImg"] for op in operators
                          if isinstance(op, dict) and "RecResizeImg" in op), {})
            shape = recog.get("image_shape") if isinstance(recog, dict) else None
            metadata = {"recognition_image_shape": shape,
                        "recognition_postprocess": post.get("name"),
                        "vocab_size": len(vocab)}
            (destination / "config.json").write_text(
                json.dumps(metadata, ensure_ascii=False, indent=2), encoding="utf-8")
    (destination / "model_manifest.json").write_text(
        json.dumps({"upstream": MODELS, "sha256": hashes},
                   ensure_ascii=False, indent=2), encoding="utf-8")
    return hashes

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    hashes = stage(args.output)
    for name, checksum in hashes.items():
        print(name, checksum)
if __name__ == "__main__":
    main()
