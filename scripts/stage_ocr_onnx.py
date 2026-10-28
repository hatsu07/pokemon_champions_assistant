"""Stage one immutable, audited PP-OCRv6 small ONNX bundle (build-time only).

The detector, recognizer, official inference metadata and dictionary are
pinned to upstream revisions. Runtime never fetches artifacts.
"""
from __future__ import annotations

import argparse
from hashlib import sha256
import json
from pathlib import Path
from urllib.request import urlopen

MODELS = {
    "det": {
        "repo": "PaddlePaddle/PP-OCRv6_small_det_onnx",
        "revision": "5994b4f1a827310b6d38f6b13716af6381847be0",
        "sha256": "d73e0058b7a8086bbd57f3d10b8bcd4ff95363f67e06e2762b5e814fe9c9410e",
    },
    "rec": {
        "repo": "PaddlePaddle/PP-OCRv6_small_rec_onnx",
        "revision": "b8f84f0b80c529de40b4fbb3544b84fa7233a513",
        "sha256": "5435fd747c9e0efe15a96d0b378d5bd157e9492ed8fd80edf08f30d02fa24634",
    },
}
PADDLEOCR_LICENSE_REVISION = "dab3fe35379033fdcb2d0e9572fac0b36c9a9ebf"


def download(url: str) -> bytes:
    with urlopen(url, timeout=90) as response:
        return response.read()


def stage(destination: Path) -> dict[str, str]:
    try:
        import yaml
    except ImportError as exc:
        raise RuntimeError("ONNX_OCR_STAGING_NEEDS_PYYAML") from exc
    destination.mkdir(parents=True, exist_ok=True)
    hashes: dict[str, str] = {}
    rec_metadata = None
    for kind, model in MODELS.items():
        root = f"https://huggingface.co/{model['repo']}/resolve/{model['revision']}"
        name = f"pp-ocrv6-small-{kind}.onnx"
        target = destination / name
        if target.is_file() and sha256(target.read_bytes()).hexdigest() == model["sha256"]:
            raw = target.read_bytes()
        else:
            raw = download(root + "/inference.onnx")
        digest = sha256(raw).hexdigest()
        if digest != model["sha256"]:
            raise RuntimeError("ONNX_OCR_MODEL_SHA256_MISMATCH: " + name)
        target.write_bytes(raw)
        hashes[name] = digest
        if kind == "rec":
            meta = yaml.safe_load(download(root + "/inference.yml"))
            if not isinstance(meta, dict):
                raise RuntimeError("ONNX_OCR_OFFICIAL_METADATA_MISSING")
            post = meta.get("PostProcess") or {}
            vocab = post.get("character_dict")
            if (not isinstance(vocab, list) or len(vocab) < 1000
                    or not all(isinstance(c, str) and c for c in vocab)):
                raise RuntimeError("ONNX_OCR_OFFICIAL_DICTIONARY_INVALID")
            dictionary = ("\\n".join(vocab) + "\\n").encode("utf-8")
            dictionary_name = "ppocrv6_dict.txt"
            (destination / dictionary_name).write_bytes(dictionary)
            hashes[dictionary_name] = sha256(dictionary).hexdigest()
            operators = (meta.get("PreProcess") or {}).get("transform_ops") or []
            recog = next((op["RecResizeImg"] for op in operators
                          if isinstance(op, dict) and "RecResizeImg" in op), {})
            rec_metadata = {
                "recognition_image_shape": recog.get("image_shape"),
                "recognition_postprocess": post.get("name"),
                "vocab_size": len(vocab),
            }
    config = json.dumps(rec_metadata, ensure_ascii=False, indent=2).encode("utf-8")
    (destination / "config.json").write_bytes(config)
    hashes["config.json"] = sha256(config).hexdigest()
    license_bytes = download(
        "https://raw.githubusercontent.com/PaddlePaddle/PaddleOCR/"
        + PADDLEOCR_LICENSE_REVISION + "/LICENSE"
    )
    if len(license_bytes) < 1000 or b"Apache License" not in license_bytes:
        raise RuntimeError("ONNX_OCR_LICENSE_INVALID")
    (destination / "PP-OCRv6-LICENSE.txt").write_bytes(license_bytes)
    hashes["PP-OCRv6-LICENSE.txt"] = sha256(license_bytes).hexdigest()
    manifest = {
        "schema": 1,
        "upstream_models": MODELS,
        "upstream_license_revision": PADDLEOCR_LICENSE_REVISION,
        "sha256": hashes,
    }
    (destination / "model_manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    return hashes


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    for name, digest in stage(args.output).items():
        print(name, digest)
