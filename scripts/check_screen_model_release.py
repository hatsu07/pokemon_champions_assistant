"""Check if a *release* includes an independently validated ONNX UI model.

This is deliberately separate from offline development/GUI smoke validation.
No placeholder or synthetic model is accepted as a release asset.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

from champions_assist.screen_classifier import load_bundled_classifier


def main() -> int:
    parser=argparse.ArgumentParser()
    parser.add_argument("--release",action="store_true",
                        help="Exit nonzero unless a validated classifier loads")
    parser.add_argument("--model-dir",type=Path,default=None)
    args=parser.parse_args()
    classifier,reason=load_bundled_classifier(model_dir=args.model_dir,device="cpu")
    status=("RELEASE_MODEL_VALIDATED" if classifier is not None
            else "DEVELOPMENT_ONLY_NO_VALIDATED_CLASSIFIER")
    print(json.dumps({"status":status,"reason":reason,
                      "release_allowed":classifier is not None},
                     ensure_ascii=False))
    if args.release and classifier is None:
        print("RELEASE_BLOCKED: collect and validate real Champions UI data, "
              "then bundle the signed-off ONNX model and rerun validation.",
              file=sys.stderr)
        return 2
    return 0


if __name__=="__main__":
    sys.exit(main())
