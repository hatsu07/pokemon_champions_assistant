"""Stage pinned official PP-OCRv6 small ONNX models for Windows builds.

Network is used only at *build time*. Runtime never downloads weights.
The distribution includes both weights and the exact CTC character dictionary.
"""
from __future__ import annotations

import argparse
from hashlib import sha256
from pathlib import Path
from urllib.request import urlopen

MODELS = (
    ("pp-ocrv6-small-det.onnx",
     "https://huggingface.co/PaddlePaddle/PP-OCRv6_small_det_onnx/resolve/"
     "5994b4f1a827310b6d38f6b13716af6381847be0/inference.onnx",
     "d73e0058b7a8086bbd57f3d10b8bcd4ff95363f67e06e2762b5e814fe9c9410e"),
    ("pp-ocrv6-small-rec.onnx",
     "https://huggingface.co/PaddlePaddle/PP-OCRv6_small_rec_onnx/resolve/"
     "b8f84f0b80c529de40b4fbb3544b84fa7233a513/inference.onnx",
     "5435fd747c9e0efe15a96d0b378d5bd157e9492ed8fd80edf08f30d02fa24634"),
)
DICTIONARY_URL = ("https://raw.githubusercontent.com/PaddlePaddle/"
                  "PaddleOCR/main/ppocr/utils/dict/ppocrv6_dict.txt")


def _fetch(url: str, target: Path, expected_sha: str | None):
    if target.is_file():
        existing = target.read_bytes()
        if expected_sha and sha256(existing).hexdigest() == expected_sha:
            return
        if not expected_sha and len(existing.decode("utf-8-sig").splitlines()) == 18708:
            return
    tmp = target.with_suffix(target.suffix + ".downloading")
    try:
        with urlopen(url, timeout=90) as reply, tmp.open("wb") as sink:
            while True:
                part = reply.read(1024 * 1024)
                if not part:
                    break
                sink.write(part)
        raw = tmp.read_bytes()
        if expected_sha and sha256(raw).hexdigest() != expected_sha:
            raise RuntimeError("MODEL_SHA256_MISMATCH: " + target.name)
        if not expected_sha:
            chars = raw.decode("utf-8-sig").splitlines()
            if len(chars) != 18708 or not all(chars):
                raise RuntimeError("MODEL_CTC_DICTIONARY_INVALID")
        tmp.replace(target)
    finally:
        tmp.unlink(missing_ok=True)


def stage(destination: Path):
    destination.mkdir(parents=True, exist_ok=True)
    for filename, url, expected_sha in MODELS:
        _fetch(url, destination / filename, expected_sha)
    _fetch(DICTIONARY_URL, destination / "ppocrv6_dict.txt", None)
    print("PP_OCRV6_SMALL_ONNX_STAGED")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    stage(args.output)
