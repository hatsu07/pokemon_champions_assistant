"""Persistent local PP-OCRv6 GPU worker for the packaged Windows GUI.

Input/output is JSON-lines over stdio, not a network service. The worker
installs no game-state logic and never makes any AI/API request.
"""
from __future__ import annotations

import base64
import json
import sys

import cv2
import numpy as np

from .ocr_gpu import PaddleV6SmallGpuOcr


def main():
    engine=PaddleV6SmallGpuOcr()
    for line in sys.stdin:
        try:
            request=json.loads(line)
            request_id=str(request.get("request_id") or "")
            if request.get("type")=="warmup":
                engine._load()
                response={"request_id":request_id,"ready":True}
            else:
                raw=base64.b64decode(request["image"],validate=True)
                image=cv2.imdecode(np.frombuffer(raw,dtype=np.uint8),cv2.IMREAD_COLOR)
                if image is None:
                    raise ValueError("INVALID_OCR_PNG")
                image=cv2.cvtColor(image,cv2.COLOR_BGR2RGB)
                result=engine.read(image,roi_name=str(request.get("roi") or ""))
                response={"request_id":request_id,"text":result.text,
                          "confidence":result.confidence}
        except Exception as exc:
            response={"request_id":locals().get("request_id",""),
                      "error":getattr(exc,"code","GPU_OCR_WORKER_FAILURE")}
        print(json.dumps(response,ensure_ascii=False),flush=True)


if __name__=="__main__":
    main()
