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

# On-disk staging is separate from the seven training labels.  A staged file
# can never reach training until a human explicitly confirms its class.
from shutil import disk_usage

MIB = 1024*1024


class StagingLimitReached(OSError):
    """Stop collecting rather than silently delete unreviewed screenshots."""


class UnlabelledScreenStaging:
    """Opt-in, disk-bounded JPEG staging with per-match session directories."""

    def __init__(self, root: str | Path, *, quota_bytes: int=2*1024**3,
                 reserve_bytes: int=512*MIB, max_edge: int=960):
        self.root=Path(root).expanduser()
        self.stage_root=self.root/"_UNLABELLED"
        if not 1<=quota_bytes<=50*1024**3 or not 0<=reserve_bytes<=100*1024**3:
            raise ValueError("INVALID_STAGING_QUOTA")
        if not 160<=max_edge<=2048:
            raise ValueError("INVALID_STAGING_IMAGE_SIZE")
        self.quota_bytes=quota_bytes
        self.reserve_bytes=reserve_bytes
        self.max_edge=max_edge
        self._cached_bytes=None

    def session_files(self, session_id: str) -> list[Path]:
        validate_session(session_id)
        folder=self.stage_root/session_id
        if not folder.is_dir():
            return []
        return sorted((p for p in folder.glob("*.jpg") if p.is_file() and not p.is_symlink()),
                      key=lambda p:p.name)

    def sessions(self) -> list[str]:
        if not self.stage_root.is_dir():
            return []
        return sorted((p.name for p in self.stage_root.iterdir()
                       if p.is_dir() and not p.is_symlink()
                       and _SESSION_RE.fullmatch(p.name)),reverse=True)

    def used_bytes(self) -> int:
        if self._cached_bytes is None:
            self._cached_bytes=sum(p.stat().st_size for session in self.sessions()
                                   for p in self.session_files(session))
        return self._cached_bytes

    def _encode(self, image: np.ndarray) -> bytes:
        if (not isinstance(image,np.ndarray) or image.ndim!=3
                or image.shape[2]!=3 or image.dtype!=np.uint8 or image.size==0):
            raise ValueError("INVALID_TRAINING_IMAGE")
        height,width=image.shape[:2]
        factor=min(1.0,self.max_edge/max(height,width))
        scaled=(cv2.resize(image,(max(1,round(width*factor)),
                                 max(1,round(height*factor))),
                           interpolation=cv2.INTER_AREA)
                if factor<1 else image)
        ok,encoded=cv2.imencode(".jpg",cv2.cvtColor(scaled,cv2.COLOR_RGB2BGR),
                                [cv2.IMWRITE_JPEG_QUALITY,85])
        if not ok:
            raise ValueError("STAGING_JPEG_ENCODING_FAILED")
        return encoded.tobytes()

    def save_unlabelled(self, image: np.ndarray, session_id: str) -> Path:
        validate_session(session_id)
        data=self._encode(image)
        folder=self.stage_root/session_id
        folder.mkdir(parents=True,exist_ok=True)
        if self.used_bytes()+len(data)>self.quota_bytes:
            raise StagingLimitReached("STAGING_QUOTA_REACHED")
        if disk_usage(folder).free-len(data)<self.reserve_bytes:
            raise StagingLimitReached("DISK_SPACE_RESERVE_REACHED")
        path=folder/(datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
                     +"_"+uuid4().hex[:14]+".jpg")
        # Never overwrite an existing review candidate; a half-written image
        # is removed if a write or fsync fails.
        descriptor=os.open(path,os.O_CREAT|os.O_EXCL|os.O_WRONLY,0o600)
        try:
            with os.fdopen(descriptor,"wb") as file:
                file.write(data)
                file.flush()
                os.fsync(file.fileno())
        except BaseException:
            path.unlink(missing_ok=True)
            raise
        self._cached_bytes=(self._cached_bytes or 0)+len(data)
        return path

    def remove_staged(self, file: str | Path) -> None:
        path=self.validate_staged_path(file)
        size=path.stat().st_size
        path.unlink()
        if self._cached_bytes is not None:
            self._cached_bytes=max(0,self._cached_bytes-size)

    def validate_staged_path(self, file: str | Path) -> Path:
        path=Path(file)
        if (path.suffix.lower()!=".jpg" or path.is_symlink()
                or not path.is_file() or not _SESSION_RE.fullmatch(path.parent.name)
                or path.parent.parent.resolve()!=self.stage_root.resolve()):
            raise ValueError("NOT_AN_UNLABELLED_STAGED_FRAME")
        return path

    def label(self, file: str | Path, target: str,
              dataset: LabelledScreenDataset) -> Path:
        """Convert one checked JPG into a human-labelled PNG before removing it."""
        path=self.validate_staged_path(file)
        if target not in LABELS:
            raise ValueError("INVALID_SCREEN_LABEL")
        bgr=cv2.imdecode(np.fromfile(path,dtype=np.uint8),cv2.IMREAD_COLOR)
        if bgr is None:
            raise ValueError("INVALID_STAGED_JPEG")
        frame=TrainingFrame(
            id=path.stem.split("_")[-1],session_id=path.parent.name,
            timestamp=path.stem.split("_")[0],
            image=cv2.cvtColor(bgr,cv2.COLOR_BGR2RGB))
        result=dataset.save(frame,target)
        self.remove_staged(path)
        return result
