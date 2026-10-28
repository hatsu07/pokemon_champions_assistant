"""Offline release gate for bundled ONNX OCR: real model inference on CPU."""
from __future__ import annotations

import argparse
import cv2
import numpy as np
from champions_assist.ocr_onnx import PPocrV6SmallOnnx, ENGINE_NAME

def main() -> int:
    parser=argparse.ArgumentParser()
    parser.add_argument("--model-dir",required=True)
    a=parser.parse_args()
    reader=PPocrV6SmallOnnx(model_dir=a.model_dir,device="cpu")
    reader.warmup()
    if reader.provider!="CPUExecutionProvider":
        raise RuntimeError("ONNX_OCR_CPU_PROVIDER_NOT_ACTIVE")
    sample=np.full((96,500,3),255,np.uint8)
    cv2.putText(sample,"97%",(15,68),cv2.FONT_HERSHEY_SIMPLEX,
                1.9,(0,0,0),3,cv2.LINE_AA)
    output=reader.read(sample,roi_name="opponent_hp_percent")
    if output.engine!=ENGINE_NAME or (output.confidence is not None and
                                      not 0<=output.confidence<=1):
        raise RuntimeError("ONNX_OCR_REAL_INFERENCE_FAILED")
    print("ONNX_OCR_CPU_INFERENCE_OK",reader.provider,
          "recognized_nonempty="+str(bool(output.text)))
    return 0

if __name__=="__main__":
    raise SystemExit(main())
