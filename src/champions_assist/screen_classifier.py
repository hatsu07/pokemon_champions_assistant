"""Fast, optional local Pokémon Champions screen classifier.

A trained ONNX model is NEVER fabricated or downloaded at runtime. CPU is the
default execution provider. Without verified weights the caller must disclose
the missing classifier and use its explicitly labelled fallback path.

The model predicts UI class only, not Pokémon identity or battle tactics.
"""
from __future__ import annotations

from dataclasses import dataclass
from hashlib import sha256
import json
from pathlib import Path
from time import perf_counter

import cv2
import numpy as np

LABELS=("TEAM_SELECTION","TURN_ACTION","SWITCH_SELECTION","BATTLE_EVENT","OTHER")
# UNKNOWN is a low-confidence inference sentinel, never a training class.
UNDETERMINED="UNKNOWN"
LABEL_SCHEMA_VERSION=2
MODEL_DIR=Path(__file__).resolve().parent / "data"
MODEL_FILE="champions_screen_classifier.onnx"
MANIFEST_FILE="champions_screen_classifier.json"


@dataclass(frozen=True)
class ScreenPrediction:
    label: str
    confidence: float
    margin: float
    latency_ms: float
    source: str = "ONNX_CPU"


class ScreenClassifierError(ValueError):
    pass


class ScreenClassifier:
    """ImageNet-style RGB NCHW logits model with a pinned manifest."""

    def __init__(self, model_path: str | Path, manifest_path: str | Path,
                 *, device: str="cpu", session_factory=None,
                 experimental_for_labelling: bool=False):
        path=Path(model_path)
        info=json.loads(Path(manifest_path).read_text(encoding="utf-8"))
        if not isinstance(info,dict) or info.get("schema_version")!=LABEL_SCHEMA_VERSION:
            raise ScreenClassifierError("INVALID_SCREEN_MODEL_MANIFEST")
        if info.get("validated") is not True and not experimental_for_labelling:
            raise ScreenClassifierError("SCREEN_MODEL_NOT_VALIDATED")
        # Explicit offline-only opt-in. load_bundled_classifier never enables it.
        self.experimental=info.get("validated") is not True
        labels=info.get("labels")
        if (not isinstance(labels,list) or len(labels)!=len(LABELS)
                or set(labels)!=set(LABELS) or len(set(labels))!=len(labels)):
            raise ScreenClassifierError("INVALID_SCREEN_MODEL_LABELS")
        input_size=info.get("input_size")
        if (not isinstance(input_size,list) or len(input_size)!=2
                or any(type(v) is not int or not 32<=v<=512 for v in input_size)):
            raise ScreenClassifierError("INVALID_SCREEN_MODEL_INPUT_SIZE")
        mean=info.get("mean",[0.0,0.0,0.0])
        std=info.get("std",[1.0,1.0,1.0])
        if (not isinstance(mean,list) or not isinstance(std,list)
                or len(mean)!=3 or len(std)!=3
                or any(not isinstance(v,(int,float)) or not np.isfinite(v) for v in mean+std)
                or any(v<=0 for v in std)):
            raise ScreenClassifierError("INVALID_SCREEN_MODEL_NORMALIZATION")
        digest=info.get("sha256")
        if not isinstance(digest,str) or len(digest)!=64:
            raise ScreenClassifierError("INVALID_SCREEN_MODEL_CHECKSUM")
        if sha256(path.read_bytes()).hexdigest()!=digest.lower():
            raise ScreenClassifierError("SCREEN_MODEL_CHECKSUM_MISMATCH")
        if device not in ("cpu","auto","gpu"):
            raise ScreenClassifierError("INVALID_SCREEN_MODEL_DEVICE")
        if session_factory is None:
            import onnxruntime as ort
            session_factory=ort.InferenceSession
            available=ort.get_available_providers()
        else:
            # Tests inject a deterministic session with no inference runtime.
            available=["CPUExecutionProvider","CUDAExecutionProvider"]
        providers=["CPUExecutionProvider"]
        if device=="gpu":
            if "CUDAExecutionProvider" not in available:
                raise ScreenClassifierError("GPU_PROVIDER_UNAVAILABLE")
            providers=["CUDAExecutionProvider","CPUExecutionProvider"]
        # Do not claim GPU is faster for small models; 'auto' deliberately
        # picks the CPU until measured evidence justifies another default.
        session=session_factory(str(path),providers=providers)
        inputs=session.get_inputs()
        if len(inputs)!=1:
            raise ScreenClassifierError("INVALID_SCREEN_MODEL_INPUTS")
        self.session=session
        self.input_name=inputs[0].name
        self.labels=tuple(labels)
        self.size=tuple(input_size)
        self.mean=np.asarray(mean,dtype=np.float32).reshape(1,3,1,1)
        self.std=np.asarray(std,dtype=np.float32).reshape(1,3,1,1)
        self.device="gpu" if providers[0]=="CUDAExecutionProvider" else "cpu"
        self.min_confidence=float(info.get("min_confidence",0.90))
        self.min_margin=float(info.get("min_margin",0.20))
        if (not 0.5<=self.min_confidence<1.0 or
                not 0<=self.min_margin<1.0):
            raise ScreenClassifierError("INVALID_SCREEN_MODEL_THRESHOLDS")

    def predict(self, image: np.ndarray, *, raw_label: bool=False) -> ScreenPrediction:
        if image.ndim!=3 or image.shape[2]!=3 or image.size==0:
            raise ScreenClassifierError("INVALID_SCREEN_IMAGE")
        started=perf_counter()
        # Explicit contract: normalized RGB [0,1], NCHW, logits [1,K].
        scaled=cv2.resize(image,self.size,interpolation=cv2.INTER_AREA)
        tensor=np.transpose(scaled.astype(np.float32)/255.0,(2,0,1))[None,...]
        tensor=(tensor-self.mean)/self.std
        outputs=self.session.run(None,{self.input_name:np.ascontiguousarray(tensor)})
        if not outputs:
            raise ScreenClassifierError("INVALID_SCREEN_MODEL_OUTPUT")
        logits=np.asarray(outputs[0],dtype=np.float32)
        if logits.shape!=(1,len(self.labels)) or not np.isfinite(logits).all():
            raise ScreenClassifierError("INVALID_SCREEN_MODEL_OUTPUT")
        logits=logits[0]-np.max(logits[0])
        probabilities=np.exp(logits)
        probabilities/=np.sum(probabilities)
        order=np.argsort(probabilities)[::-1]
        top=int(order[0])
        confidence=float(probabilities[top])
        margin=confidence-float(probabilities[int(order[1])])
        label=(self.labels[top] if (raw_label or
               (confidence>=self.min_confidence and margin>=self.min_margin))
               else UNDETERMINED)
        return ScreenPrediction(
            label,confidence,margin,(perf_counter()-started)*1000,
            "ONNX_GPU" if self.device=="gpu" else "ONNX_CPU")


def load_bundled_classifier(*, model_dir: str | Path | None=None,
                            device: str="cpu", session_factory=None):
    """Return (model or None, diagnostic), never download model weights."""
    directory=Path(model_dir) if model_dir is not None else MODEL_DIR
    model=directory / MODEL_FILE
    manifest=directory / MANIFEST_FILE
    if not model.is_file() or not manifest.is_file():
        return None,"MODEL_NOT_BUNDLED"
    try:
        classifier=ScreenClassifier(model,manifest,device=device,
                                    session_factory=session_factory)
        return classifier,"MODEL_READY_"+classifier.device.upper()
    except (OSError,ValueError,ImportError,RuntimeError) as exc:
        # Broken/untrusted model is never an excuse to return guessed UI facts.
        return None,"MODEL_LOAD_FAILED_"+type(exc).__name__


class ClassifiedMenuGate:
    """Debounce UI class predictions and send at most once per command view.

    OTHER/low-confidence inference rearms the gate; phase transitions reset it.
    Visual model inference is never used as a tactical recommendation.
    """
    def __init__(self, *, stable_frames=2):
        self.stable_frames=stable_frames
        self.reset()

    def reset(self):
        self.phase=None
        self.candidate=None
        self.streak=0
        self.armed=True
        self.misses=0

    def acknowledge(self, kind: str, phase: str) -> None:
        """Explicit or template-triggered dispatch disarms duplicate model hits."""
        self.reset()
        self.phase=phase
        self.candidate=kind
        self.streak=self.stable_frames
        self.armed=False

    def observe(self, prediction: ScreenPrediction, phase: str) -> str | None:
        if phase!=self.phase:
            self.reset()
            self.phase=phase
        allowed={"MATCHMAKING":{"TEAM_SELECTION"},
                 "PREP":{"TURN_ACTION","SWITCH_SELECTION","BATTLE_EVENT"},
                 "BATTLE":{"TURN_ACTION","SWITCH_SELECTION","BATTLE_EVENT"}}.get(phase,set())
        kind=prediction.label if prediction.label in allowed else None
        if kind is None:
            self.misses+=1
            self.candidate=None
            self.streak=0
            if self.misses>=2:
                self.armed=True
            return None
        self.misses=0
        if kind!=self.candidate:
            self.candidate=kind
            self.streak=1
        else:
            self.streak+=1
        if self.armed and self.streak>=self.stable_frames:
            self.armed=False
            return kind
        return None
