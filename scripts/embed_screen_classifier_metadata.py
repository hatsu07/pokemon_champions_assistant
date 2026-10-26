"""Create a standalone five-class ONNX with embedded inference metadata.

Runs locally in the trainer container, never touches the input model/JSON
and never changes `validated` from its original value. No screenshots or
private image data are uploaded.
"""
from __future__ import annotations

import argparse
from hashlib import sha256
import json
from pathlib import Path

from champions_assist.screen_classifier import (
    EMBEDDED_MANIFEST_KEY, LABELS, LABEL_SCHEMA_VERSION,
    ScreenClassifierError,
)


def embed_manifest(model_path: Path, manifest: dict) -> None:
    """Embed inference contract into a NEW training output or a local copy.

    Embedded SHA-256 is impossible (self-referential); the legacy JSON
    remains the strict SHA-pinned contract when it exists.
    """
    if manifest.get("schema_version") != LABEL_SCHEMA_VERSION:
        raise ScreenClassifierError("INVALID_SCREEN_MODEL_MANIFEST")
    if manifest.get("labels") != list(LABELS):
        raise ScreenClassifierError("INVALID_SCREEN_MODEL_LABELS")
    import onnx
    graph=onnx.load(str(model_path))
    props={property.key:property.value for property in graph.metadata_props}
    embedded={key:value for key,value in manifest.items() if key!="sha256"}
    props[EMBEDDED_MANIFEST_KEY]=json.dumps(embedded,ensure_ascii=False)
    onnx.helper.set_model_props(graph,props)
    onnx.checker.check_model(graph)
    onnx.save_model(graph,str(model_path))


def convert_to_standalone(model_path: Path, manifest_path: Path,
                          output: Path) -> Path:
    """Copy first; never replace a user's original ONNX or its JSON."""
    model_path=model_path.resolve()
    manifest_path=manifest_path.resolve()
    output=output.resolve()
    if output==model_path or output.exists():
        raise ValueError("OUTPUT_MUST_BE_A_NEW_FILE")
    if output.suffix.lower()!=".onnx":
        raise ValueError("OUTPUT_MUST_HAVE_ONNX_SUFFIX")
    info=json.loads(manifest_path.read_text(encoding="utf-8"))
    if not isinstance(info,dict):
        raise ScreenClassifierError("INVALID_SCREEN_MODEL_MANIFEST")
    digest=info.get("sha256")
    if (not isinstance(digest,str) or len(digest)!=64 or
            sha256(model_path.read_bytes()).hexdigest()!=digest.lower()):
        raise ScreenClassifierError("SCREEN_MODEL_CHECKSUM_MISMATCH")
    # Source is already hash-pinned. Build the new, separate standalone file.
    output.parent.mkdir(parents=True,exist_ok=True)
    try:
        output.write_bytes(model_path.read_bytes())
        embed_manifest(output,info)
    except BaseException:
        output.unlink(missing_ok=True)
        raise
    return output


def main() -> int:
    parser=argparse.ArgumentParser()
    parser.add_argument("--model",type=Path,required=True)
    parser.add_argument("--manifest",type=Path,required=True)
    parser.add_argument("--output",type=Path,required=True)
    args=parser.parse_args()
    created=convert_to_standalone(args.model,args.manifest,args.output)
    print("Standalone ONNX saved:",created)
    print("Validation status is unchanged; a trial model remains UNVALIDATED.")
    return 0


if __name__=="__main__":
    raise SystemExit(main())
