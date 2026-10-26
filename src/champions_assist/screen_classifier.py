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
EMBEDDED_MANIFEST_KEY="pokemon_champions.screen_classifier.manifest_v2"


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
    """RGB NCHW logits classifier; sidecar or ONNX-embedded v2 contract.

    Unvalidated weights require an explicit trial opt-in. The classifier never
    changes the manifest's validated flag, bypasses a checksum, or relabels
    unknown classes.
    """

    def __init__(self, model_path: str | Path, manifest_path: str | Path | None,
                 *, device: str="cpu", session_factory=None,
                 experimental_for_labelling: bool=False,
                 allow_unvalidated_for_trial: bool=False,
                 gpu_id: int=0, available_providers=None):
        path=Path(model_path)
        if device not in ("cpu","auto","gpu","directml","cuda"):
            raise ScreenClassifierError("INVALID_SCREEN_MODEL_DEVICE")
        if type(gpu_id) is not int or not 0<=gpu_id<=7:
            raise ScreenClassifierError("INVALID_SCREEN_GPU_INDEX")
        if session_factory is None:
            import onnxruntime as ort
            session_factory=ort.InferenceSession
            available=set(ort.get_available_providers())
        else:
            # Injected test sessions never need a driver or actual hardware.
            available=set(available_providers or ("CPUExecutionProvider",))
        if device=="cpu":
            candidates=["CPUExecutionProvider"]
        elif device=="cuda":
            candidates=["CUDAExecutionProvider"]
        elif device=="directml":
            candidates=["DmlExecutionProvider"]
        else:
            # On Windows, DirectML accelerates Intel/AMD/NVIDIA GPUs.
            # CUDA remains an option where its runtime is installed.
            candidates=["DmlExecutionProvider","CUDAExecutionProvider"]
            if device=="auto":
                candidates.append("CPUExecutionProvider")
        last_failure=None
        session=None
        active_provider=None
        for candidate in candidates:
            if candidate not in available:
                continue
            try:
                if candidate=="CPUExecutionProvider":
                    requested=["CPUExecutionProvider"]
                else:
                    requested=[(candidate,{"device_id":gpu_id}),
                               "CPUExecutionProvider"]
                kwargs={"providers":requested}
                if candidate=="DmlExecutionProvider" and session_factory==getattr(
                        locals().get("ort",None),"InferenceSession",None):
                    opts=ort.SessionOptions()
                    opts.enable_mem_pattern=False
                    opts.execution_mode=ort.ExecutionMode.ORT_SEQUENTIAL
                    kwargs["sess_options"]=opts
                attempt=session_factory(str(path),**kwargs)
                # ORT may silently fail to attach a provider and use CPU:
                # never display GPU unless the session really advertises it.
                actual=attempt.get_providers() if hasattr(attempt,"get_providers") else [
                    requested[0][0] if isinstance(requested[0],tuple)
                    else requested[0]]
                if candidate not in actual:
                    last_failure="GPU_PROVIDER_NOT_ACTIVE"
                    continue
                session=attempt
                active_provider=candidate
                break
            except (RuntimeError,ValueError,OSError,TypeError) as exc:
                last_failure=type(exc).__name__
                if device not in ("auto","gpu"):
                    raise ScreenClassifierError(
                        "GPU_SESSION_INIT_FAILED_"+last_failure) from exc
        if session is None:
            if device in ("auto","cpu"):
                raise ScreenClassifierError("CPU_SESSION_UNAVAILABLE")
            raise ScreenClassifierError("GPU_PROVIDER_UNAVAILABLE_"+str(last_failure or "NOT_INSTALLED"))
        if manifest_path is None:
            # Metadata is stored in ONNX's custom_metadata_map, so load the
            # runtime session once to retrieve it. No separate JSON is needed.
            metadata=session.get_modelmeta().custom_metadata_map
            raw=metadata.get(EMBEDDED_MANIFEST_KEY)
            if not raw:
                raise ScreenClassifierError("SCREEN_MODEL_METADATA_MISSING")
            try:
                info=json.loads(raw)
            except (ValueError,TypeError) as exc:
                raise ScreenClassifierError("INVALID_SCREEN_MODEL_EMBEDDED_METADATA") from exc
        else:
            info=json.loads(Path(manifest_path).read_text(encoding="utf-8"))
        if not isinstance(info,dict) or info.get("schema_version")!=LABEL_SCHEMA_VERSION:
            raise ScreenClassifierError("INVALID_SCREEN_MODEL_MANIFEST")
        if info.get("validated") is not True and not (
                experimental_for_labelling or allow_unvalidated_for_trial):
            raise ScreenClassifierError("SCREEN_MODEL_NOT_VALIDATED")
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
        if manifest_path is not None:
            # A sidecar digest pins the exact binary. An embedded SHA-256 would
            # be self-referential; ONNX-only models use the runtime's parser
            # plus their embedded contract instead.
            digest=info.get("sha256")
            if not isinstance(digest,str) or len(digest)!=64:
                raise ScreenClassifierError("INVALID_SCREEN_MODEL_CHECKSUM")
            if sha256(path.read_bytes()).hexdigest()!=digest.lower():
                raise ScreenClassifierError("SCREEN_MODEL_CHECKSUM_MISMATCH")
        inputs=session.get_inputs()
        if len(inputs)!=1:
            raise ScreenClassifierError("INVALID_SCREEN_MODEL_INPUTS")
        self.session=session
        self.input_name=inputs[0].name
        self.labels=tuple(labels)
        self.size=tuple(input_size)
        self.mean=np.asarray(mean,dtype=np.float32).reshape(1,3,1,1)
        self.std=np.asarray(std,dtype=np.float32).reshape(1,3,1,1)
        self.device="gpu" if active_provider!="CPUExecutionProvider" else "cpu"
        self.provider=active_provider
        self.gpu_id=gpu_id if self.device=="gpu" else None
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
            ("ONNX_GPU_DML" if self.provider=="DmlExecutionProvider" else
             "ONNX_GPU_CUDA" if self.provider=="CUDAExecutionProvider" else
             "ONNX_CPU"))


def load_bundled_classifier(*, model_dir: str | Path | None=None,
                            model_path: str | Path | None=None,
                            device: str="auto", session_factory=None,
                            allow_unvalidated_for_trial: bool=False,
                            gpu_id: int=0, available_providers=None):
    """Load selected ONNX + optional same-name JSON, or ONNX-only metadata.

    The default caller remains strict. Trial use must be an explicit opt-in;
    status exposes the original reason instead of hiding ScreenClassifierError.
    """
    if model_path is not None:
        model=Path(model_path)
        if model.suffix.lower()!=".onnx":
            return None,"MODEL_PATH_NOT_ONNX"
        manifest=model.with_suffix(".json")
    else:
        directory=Path(model_dir) if model_dir is not None else MODEL_DIR
        model=directory / MODEL_FILE
        manifest=directory / MANIFEST_FILE
    if not model.is_file():
        return None,"MODEL_NOT_BUNDLED"
    try:
        classifier=ScreenClassifier(
            model,manifest if manifest.is_file() else None,device=device,
            session_factory=session_factory,
            allow_unvalidated_for_trial=allow_unvalidated_for_trial,
            gpu_id=gpu_id,available_providers=available_providers)
        prefix="MODEL_TRIAL_" if classifier.experimental else "MODEL_READY_"
        suffix=("GPU_DML_"+str(classifier.gpu_id) if classifier.provider=="DmlExecutionProvider"
                else "GPU_CUDA_"+str(classifier.gpu_id) if classifier.provider=="CUDAExecutionProvider"
                else "CPU")
        return classifier,prefix+suffix
    except ScreenClassifierError as exc:
        return None,"MODEL_LOAD_FAILED_"+str(exc)
    except (OSError,ValueError,ImportError,RuntimeError) as exc:
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
        self.last_emitted=None

    def acknowledge(self, kind: str, phase: str) -> None:
        """Explicit or template-triggered dispatch disarms duplicate model hits."""
        self.reset()
        self.phase=phase
        self.candidate=kind
        self.streak=self.stable_frames
        self.armed=False
        self.last_emitted=kind

    def rearm_after_resolution(self) -> None:
        """Permit one next-turn command after a confirmed resolution screen.

        Action menus are still deduplicated by the panel until BATTLE_EVENT
        has been observed. OTHER animation alone never rearms a new turn.
        """
        self.armed=True
        self.last_emitted=None
        self.candidate=None
        self.streak=0

    def observe(self, prediction: ScreenPrediction, phase: str) -> str | None:
        if phase!=self.phase:
            self.reset()
            self.phase=phase
        allowed={"MATCHMAKING":{"TEAM_SELECTION"},
                 # A real command may arrive while the selection API is still
                 # pending; keep the newer candidate for deferred dispatch.
                 "SELECTION":{"TURN_ACTION","SWITCH_SELECTION","BATTLE_EVENT"},
                 "PREP":{"TURN_ACTION","SWITCH_SELECTION","BATTLE_EVENT"},
                 "BATTLE":{"TURN_ACTION","SWITCH_SELECTION","BATTLE_EVENT"}}.get(phase,set())
        kind=prediction.label if prediction.label in allowed else None
        if kind is None:
            self.misses+=1
            self.candidate=None
            self.streak=0
            if self.misses>=2:
                self.armed=True
                self.last_emitted=None
            return None
        self.misses=0
        if kind!=self.candidate:
            self.candidate=kind
            self.streak=1
        else:
            self.streak+=1
        # Event and action categories can follow one another directly.
        # Deduplicate steady frames of one category until the kind changes
        # or two unrelated frames permit the same category again.
        # Time-critical menus are dispatched on their FIRST high-confidence
        # classifier frame. Selection and uncertain predictions retain the
        # normal debounce. The local classifier never chooses a move.
        strong_action=(kind in ("TURN_ACTION","SWITCH_SELECTION")
                       and prediction.confidence>=0.95
                       and prediction.margin>=0.50)
        required=1 if strong_action else self.stable_frames
        if self.streak>=required and (self.armed or kind!=self.last_emitted):
            self.armed=False
            self.last_emitted=kind
            return kind
        return None
