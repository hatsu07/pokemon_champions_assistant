"""Human-labelled, local-only training frames for the screen classifier.

Dataset layout matches scripts/train_screen_classifier.py:
    screens/<LABEL>/<session-id>__<frame-id>.png
No frame is written until the user explicitly saves labelled candidates.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4
import os
import re

import cv2
import numpy as np

from .screen_classifier import LABELS


_SESSION_RE=re.compile(r"[a-zA-Z0-9][a-zA-Z0-9_-]{5,79}\Z")


def new_session_id() -> str:
    return datetime.now(timezone.utc).strftime("match%Y%m%dT%H%M%S") + "_" + uuid4().hex[:8]


def validate_session(session_id: str) -> str:
    if not isinstance(session_id,str) or not _SESSION_RE.fullmatch(session_id):
        raise ValueError("INVALID_TRAINING_SESSION_ID")
    return session_id


@dataclass(frozen=True)
class TrainingFrame:
    id: str
    session_id: str
    timestamp: str
    image: np.ndarray


class ReviewBuffer:
    """Bounded memory; keep at most max_frames RGB frames per collection run."""

    def __init__(self, *, max_frames: int=150, max_edge: int=960):
        if not 1<=max_frames<=500 or not 160<=max_edge<=2048:
            raise ValueError("INVALID_BUFFER_LIMIT")
        self.max_frames=max_frames
        self.max_edge=max_edge
        self.frames: list[TrainingFrame]=[]
        self.dropped=0
        self.session_id=new_session_id()

    def new_session(self) -> str:
        self.frames.clear()
        self.dropped=0
        self.session_id=new_session_id()
        return self.session_id

    def append(self, image: np.ndarray) -> TrainingFrame:
        if image.ndim!=3 or image.shape[2]!=3 or image.dtype!=np.uint8 or not image.size:
            raise ValueError("INVALID_TRAINING_IMAGE")
        height,width=image.shape[:2]
        ratio=min(1.0,self.max_edge/max(height,width))
        rgb=(cv2.resize(image,(max(1,round(width*ratio)),max(1,round(height*ratio))),
                        interpolation=cv2.INTER_AREA) if ratio<1.0 else image.copy())
        sample=TrainingFrame(
            id=uuid4().hex[:14],session_id=self.session_id,
            timestamp=datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ"),
            image=np.ascontiguousarray(rgb))
        if len(self.frames)>=self.max_frames:
            # Deliberately stop accepting additional data instead of dropping
            # a user's unlabelled review candidates without warning.
            self.dropped+=1
            raise BufferError("REVIEW_BUFFER_FULL")
        self.frames.append(sample)
        return sample

    def remove(self, ids: set[str]) -> None:
        self.frames=[frame for frame in self.frames if frame.id not in ids]


class LabelledScreenDataset:
    def __init__(self, root: str | Path):
        self.root=Path(root).expanduser()

    def save(self, frame: TrainingFrame, label: str) -> Path:
        if label not in LABELS:
            raise ValueError("INVALID_SCREEN_LABEL")
        validate_session(frame.session_id)
        if frame.image.ndim!=3 or frame.image.shape[2]!=3 or frame.image.dtype!=np.uint8:
            raise ValueError("INVALID_TRAINING_IMAGE")
        success,encoded=cv2.imencode(".png",cv2.cvtColor(frame.image,cv2.COLOR_RGB2BGR))
        if not success:
            raise ValueError("TRAINING_PNG_ENCODING_FAILED")
        parent=self.root/label
        parent.mkdir(parents=True,exist_ok=True)
        filename=f"{frame.session_id}__{frame.timestamp}_{frame.id}.png"
        destination=parent/filename
        # O_EXCL prevents overwriting any existing human-labelled original.
        descriptor=os.open(destination,os.O_WRONLY|os.O_CREAT|os.O_EXCL,0o600)
        try:
            with os.fdopen(descriptor,"wb") as stream:
                stream.write(encoded.tobytes())
                stream.flush()
                os.fsync(stream.fileno())
        except BaseException:
            destination.unlink(missing_ok=True)
            raise
        return destination

    def relabel(self, file: str | Path, target_label: str) -> Path:
        if target_label not in LABELS:
            raise ValueError("INVALID_SCREEN_LABEL")
        path=Path(file)
        # Do not accept arbitrary filesystem destinations to move.
        if (path.suffix.lower()!=".png" or path.parent.name not in LABELS
                or path.parent.parent.resolve()!=self.root.resolve()
                or not path.is_file() or path.is_symlink()):
            raise ValueError("NOT_A_DATASET_FRAME")
        if path.parent.name==target_label:
            return path
        destination=self.root/target_label/path.name
        destination.parent.mkdir(parents=True,exist_ok=True)
        if destination.exists():
            raise FileExistsError("DATASET_FRAME_ALREADY_EXISTS")
        # Avoid data loss from cross-volume move; all labels share the root.
        path.rename(destination)
        return destination

    def audit(self) -> dict:
        counts={label:0 for label in LABELS}
        sessions={label:set() for label in LABELS}
        if not self.root.is_dir():
            return {"counts":counts,"session_counts":{label:0 for label in LABELS},
                    "total":0,"all_sessions":0}
        for label in LABELS:
            directory=self.root/label
            if not directory.is_dir():
                continue
            for path in directory.glob("*.png"):
                if path.is_symlink() or "__" not in path.stem:
                    continue
                session=path.stem.split("__",1)[0]
                try:
                    validate_session(session)
                except ValueError:
                    continue
                counts[label]+=1
                sessions[label].add(session)
        return {"counts":counts,
                "session_counts":{label:len(sessions[label]) for label in LABELS},
                "total":sum(counts.values()),
                "all_sessions":len(set().union(*sessions.values()))}
