"""Train an experimental Champions UI classifier from consented local captures.

Run only in the Docker ``trainer`` environment. The five human-labelled PNG
classes are read from ``screens/<LABEL>/<session>__<frame>.png``. The separate
``screens/_UNLABELLED`` JPEG staging area is deliberately never scanned.

Training and validation hold out whole recorded sessions. ``--validate-only``
performs all local dataset checks without importing PyTorch or using a network.
``--pretrained`` is the sole opt-in that lets TorchVision obtain ImageNet
weights; the script never uploads images, labels, models, or metrics.
"""
from __future__ import annotations

import argparse
from collections import Counter
from dataclasses import dataclass
from hashlib import sha256
import json
from pathlib import Path
import random
import sys
from time import monotonic
from typing import Any, Sequence

from PIL import Image, UnidentifiedImageError


from champions_assist.screen_classifier import LABELS, LABEL_SCHEMA_VERSION

# One pinned definition shared with the capture UI and ONNX loader.
FORMATS={".png",".jpg",".jpeg",".webp"}
SIZE=(160,160)
Sample=tuple[Path,int,str]


@dataclass(frozen=True)
class DatasetInspection:
    records: list[Sample]
    report: dict[str,Any]


class DatasetValidationError(ValueError):
    def __init__(self, report: dict[str,Any]):
        self.report=report
        issues=report.get("issues",[])
        super().__init__("Dataset validation failed:\n- "+"\n- ".join(issues))


def _label_summary(records: Sequence[Sample]) -> dict[str,dict[str,int]]:
    counts=Counter(label for _,label,_ in records)
    sessions={index:{session for _,label,session in records if label==index}
              for index in range(len(LABELS))}
    return {name:{"images":counts[index],"sessions":len(sessions[index])}
            for index,name in enumerate(LABELS)}


def inspect_dataset(root: Path, *, progress: bool=False) -> DatasetInspection:
    """Read every labelled candidate and return a JSON-safe local audit."""
    root=Path(root)
    started=monotonic()
    scanned=0
    if progress:
        print("[preflight] checking labelled images",file=sys.stderr,flush=True)
    records: list[Sample]=[]
    issues: list[str]=[]
    # Never silently exclude legacy 7-class PNGs when a user points the new
    # five-class trainer at their original dataset. Copy into a NEW root first.
    for legacy in ("MATCHMAKING","MOVE_MENU","PARTY_MENU","RESULT","UNKNOWN"):
        folder=root/legacy
        if folder.is_dir() and any(
                path.is_file() and path.suffix.lower() in FORMATS
                for path in folder.rglob("*")):
            issues.append(
                f"Legacy seven-class images detected in {folder}. "
                "Run prepare_five_class_screen_dataset.py and review "
                "the ambiguous BATTLE_EVENT/UNKNOWN images before training.")
    for index,name in enumerate(LABELS):
        folder=root/name
        if not folder.is_dir():
            issues.append(f"Missing class directory: {folder}")
            continue
        readable=0
        for file in sorted(folder.rglob("*")):
            if not file.is_file() or file.suffix.lower() not in FORMATS:
                continue
            scanned+=1
            if progress and (scanned==1 or scanned%100==0):
                print(f"[preflight] scanned={scanned} class={name} elapsed={monotonic()-started:.1f}s",
                      file=sys.stderr,flush=True)
            if file.is_symlink():
                issues.append(f"Refusing symlinked training image: {file}")
                continue
            if "__" not in file.stem or not file.stem.split("__",1)[0]:
                issues.append(f"Frame must carry session prefix '__': {file}")
                continue
            try:
                with Image.open(file) as image:
                    image.verify()
                with Image.open(file) as image:
                    image.convert("RGB").load()
            except (OSError,UnidentifiedImageError) as exc:
                issues.append(f"Unreadable image: {file} ({type(exc).__name__})")
                continue
            session=file.stem.split("__",1)[0]
            records.append((file,index,session))
            readable+=1
        if not readable:
            issues.append(f"No readable labelled images for {name}; label more frames from independent matches")
    report={
        "schema_version":LABEL_SCHEMA_VERSION,
        "data_dir":str(root),
        "labels":_label_summary(records),
        "total_images":len(records),
        "total_sessions":len({session for _,_,session in records}),
        "unlabelled_directory_ignored":(root/"_UNLABELLED").is_dir(),
        "issues":issues,
    }
    if progress:
        print(f"[preflight] checked={scanned} readable={len(records)} elapsed={monotonic()-started:.1f}s",
              file=sys.stderr,flush=True)
    return DatasetInspection(records,report)


def collect(root: Path) -> list[Sample]:
    inspection=inspect_dataset(root)
    if inspection.report["issues"]:
        raise DatasetValidationError(inspection.report)
    return inspection.records


def _partition_summary(records: Sequence[Sample]) -> dict[str,Any]:
    return {
        "images":len(records),
        "sessions":len({session for _,_,session in records}),
        "labels":_label_summary(records),
    }


def split_by_session(samples: Sequence[Sample], *, seed: int=42,
                     fraction: float=0.2) -> tuple[list[Sample],list[Sample],set[str]]:
    """Create a deterministic, session-disjoint split with every label on both sides."""
    if not 0.0<fraction<1.0:
        raise ValueError("Validation fraction must be between zero and one")
    sessions=sorted({record[2] for record in samples})
    if len(sessions)<4:
        raise ValueError(f"Need at least four distinct recorded matches; found {len(sessions)}")
    by_label={index:{session for _,label,session in samples if label==index}
              for index in range(len(LABELS))}
    missing=[LABELS[index] for index,values in by_label.items() if not values]
    if missing:
        raise ValueError("Missing labelled samples for: "+", ".join(missing))
    insufficient=[LABELS[index] for index,values in by_label.items() if len(values)<2]
    if insufficient:
        raise ValueError("Need at least two independent sessions for each label; "
                         "record another match containing: "+", ".join(insufficient))

    labels_by_session={session:{label for _,label,item_session in samples
                                if item_session==session}
                       for session in sessions}
    shuffled=list(sessions)
    random.Random(seed).shuffle(shuffled)
    priority={session:index for index,session in enumerate(shuffled)}
    held_out: set[str]=set()
    uncovered=set(range(len(LABELS)))

    def can_hold_out(session: str) -> bool:
        candidate=held_out|{session}
        return all(not by_label[label].issubset(candidate)
                   for label in labels_by_session[session])

    while uncovered:
        choices=[session for session in sessions if session not in held_out
                 and can_hold_out(session)
                 and labels_by_session[session]&uncovered]
        if not choices:
            names=", ".join(LABELS[index] for index in sorted(uncovered))
            raise ValueError("Could not create a session-disjoint validation split for: "+names)
        session=max(choices,key=lambda item:(len(labels_by_session[item]&uncovered),
                                             -priority[item]))
        held_out.add(session)
        uncovered-=labels_by_session[session]

    target=max(1,round(len(sessions)*fraction))
    for session in sorted(sessions,key=lambda item:priority[item]):
        if len(held_out)>=target:
            break
        if session not in held_out and can_hold_out(session):
            held_out.add(session)

    training=[record for record in samples if record[2] not in held_out]
    validation=[record for record in samples if record[2] in held_out]
    for part,values in (("training",training),("validation",validation)):
        counts=Counter(label for _,label,_ in values)
        absent=[LABELS[index] for index in range(len(LABELS)) if not counts[index]]
        if absent:
            raise ValueError(f"Missing label in {part}: "+", ".join(absent))
    if {record[2] for record in training}&{record[2] for record in validation}:
        raise AssertionError("Session leakage between training and validation")
    return training,validation,held_out


def validate_dataset(root: Path, *, seed: int=42,
                     fraction: float=0.2, progress: bool=False) -> dict[str,Any]:
    """Report whether a local dataset is safe to train without touching Torch."""
    inspection=inspect_dataset(root,progress=progress)
    report=dict(inspection.report)
    if report["issues"]:
        report["ready"]=False
        return report
    try:
        training,validation,held_out=split_by_session(inspection.records,seed=seed,
                                                        fraction=fraction)
    except ValueError as exc:
        report["issues"]=[str(exc)]
        report["ready"]=False
        return report
    report["split"]={
        "training":_partition_summary(training),
        "validation":_partition_summary(validation),
        "held_out_sessions":len(held_out),
    }
    report["ready"]=True
    return report


def _resolve_device(args,torch):
    if args.device=="cuda" and not torch.cuda.is_available():
        raise ValueError("CUDA requested but unavailable inside Docker. Run --gpu --check-gpu.")
    if args.device=="cpu":
        return torch.device("cpu")
    return torch.device("cuda" if torch.cuda.is_available() else "cpu")


def build_training_manifest(
    *, model_path: Path, mean: Sequence[float], std: Sequence[float],
    metrics: dict[str, Any], held_out_sessions: int,
    quality_gate_passed: bool, approve_if_thresholds_pass: bool,
    confidence: float, device: str, pretrained: bool,
) -> dict[str, Any]:
    """Create the shared five-label model contract without importing Torch.

    A good dataset and a schema-compatible artifact are separate conditions.
    The validated flag is never set unless both the quality gate and explicit
    approval were satisfied.
    """
    if set(metrics) != set(LABELS):
        raise ValueError("training metrics must contain exactly the five screen labels")
    if not 0.5 <= confidence < 1:
        raise ValueError("invalid model confidence threshold")
    return {
        "schema_version": LABEL_SCHEMA_VERSION,
        "labels": list(LABELS),
        "input_size": list(SIZE),
        "mean": list(mean),
        "std": list(std),
        "sha256": sha256(model_path.read_bytes()).hexdigest(),
        "min_confidence": confidence,
        "min_margin": 0.20,
        "validated": bool(quality_gate_passed and approve_if_thresholds_pass),
        "holdout_sessions": held_out_sessions,
        "metrics": metrics,
        "training_device": device,
        "pretrained": bool(pretrained),
        "provenance": "locally labelled Pokémon Champions sessions",
    }


def train_model(args):
    import torch
    from torch import nn
    from torch.utils.data import DataLoader,Dataset
    from torchvision import models,transforms

    device=_resolve_device(args,torch)
    print(f"[device] selected={device} torch={torch.__version__} cuda_runtime={torch.version.cuda}",flush=True)
    inspection=inspect_dataset(args.data_dir,progress=True)
    if inspection.report["issues"]:
        raise DatasetValidationError(inspection.report)
    try:
        training,testing,held_out=split_by_session(inspection.records,seed=args.seed)
    except ValueError as exc:
        raise DatasetValidationError({**inspection.report,"issues":[str(exc)]}) from exc
    print(f"[split] train={len(training)} validation={len(testing)} held_out_sessions={len(held_out)}",flush=True)
    random.seed(args.seed)
    torch.manual_seed(args.seed)
    if device.type=="cuda":
        torch.cuda.manual_seed_all(args.seed)
    if args.pretrained:
        # Explicit opt-in; this is the only path that may download third-party weights.
        weights=models.MobileNet_V3_Small_Weights.IMAGENET1K_V1
        mean=[0.485,0.456,0.406]
        std=[0.229,0.224,0.225]
    else:
        weights=None
        mean=[0.0]*3
        std=[1.0]*3

    def pipeline(training: bool):
        steps=[transforms.Resize((SIZE[1],SIZE[0]))]
        if training:
            steps.append(transforms.ColorJitter(brightness=0.08,contrast=0.08))
        steps += [transforms.ToTensor(),transforms.Normalize(mean,std)]
        return transforms.Compose(steps)

    class ScreenData(Dataset):
        def __init__(self,items,augmentation):
            self.items=items
            self.transform=pipeline(augmentation)
        def __len__(self): return len(self.items)
        def __getitem__(self,index):
            path,label,_=self.items[index]
            with Image.open(path) as img:
                return self.transform(img.convert("RGB")),label

    training_loader=DataLoader(ScreenData(training,True),batch_size=args.batch_size,
                               shuffle=True,num_workers=0)
    test_loader=DataLoader(ScreenData(testing,False),batch_size=args.batch_size,
                           shuffle=False,num_workers=0)
    model=models.mobilenet_v3_small(weights=weights)
    in_features=model.classifier[-1].in_features
    model.classifier[-1]=nn.Linear(in_features,len(LABELS))
    model.to(device)
    optimizer=torch.optim.AdamW(model.parameters(),lr=args.lr)
    objective=nn.CrossEntropyLoss()
    for epoch in range(args.epochs):
        model.train()
        loss_sum=0.0
        started=monotonic()
        for step,(image,label) in enumerate(training_loader,start=1):
            image,label=image.to(device),label.to(device)
            optimizer.zero_grad()
            loss=objective(model(image),label)
            loss.backward()
            optimizer.step()
            loss_sum+=float(loss.item())*len(label)
            if step==1 or step%5==0 or step==len(training_loader):
                print(f"[train] epoch={epoch+1}/{args.epochs} batch={step}/{len(training_loader)} "
                      f"elapsed={monotonic()-started:.1f}s",flush=True)
        print(f"epoch={epoch+1}/{args.epochs} train_loss={loss_sum/len(training):.4f}",flush=True)

    model.eval()
    correct=Counter()
    total=Counter()
    predicted=Counter()
    fp=Counter()
    with torch.no_grad():
        started=monotonic()
        for step,(image,label) in enumerate(test_loader,start=1):
            guesses=model(image.to(device)).argmax(1).cpu().tolist()
            if step==1 or step%5==0 or step==len(test_loader):
                print(f"[validation] batch={step}/{len(test_loader)} elapsed={monotonic()-started:.1f}s",
                      flush=True)
            for expected,guess in zip(label.tolist(),guesses):
                total[expected]+=1
                predicted[guess]+=1
                if expected==guess:
                    correct[expected]+=1
                else:
                    fp[guess]+=1
    metrics={
        LABELS[index]:{
            "support":total[index],
            "recall":round(correct[index]/total[index],5) if total[index] else 0,
            "precision":round(correct[index]/predicted[index],5) if predicted[index] else 0,
            "false_positives":fp[index],
        } for index in range(len(LABELS))
    }
    selected=metrics["TEAM_SELECTION"]
    validated=(len(held_out)>=5 and selected["support"]>=100
               and selected["recall"]>=0.99
               and selected["precision"]>=0.995)
    validated=validated and all(
        item["support"]>=30 and item["recall"]>=0.95 for item in metrics.values())
    args.output_dir.mkdir(parents=True,exist_ok=True)
    model_path=args.output_dir/"champions_screen_classifier.onnx"
    model_cpu=model.cpu()
    torch.onnx.export(
        model_cpu,torch.zeros(1,3,SIZE[1],SIZE[0]),str(model_path),
        input_names=["rgb"],output_names=["logits"],opset_version=17)
    manifest=build_training_manifest(
        model_path=model_path, mean=mean, std=std,
        metrics=metrics, held_out_sessions=len(held_out),
        quality_gate_passed=validated,
        approve_if_thresholds_pass=args.approve_if_thresholds_pass,
        confidence=args.confidence, device=str(device),
        pretrained=bool(args.pretrained),
    )
    # Future training outputs are directly selectable ONNX files with their
    # own metadata. Keep the JSON for older clients and SHA-256 verification.
    from scripts.embed_screen_classifier_metadata import embed_manifest
    embed_manifest(model_path,manifest)
    manifest["sha256"]=sha256(model_path.read_bytes()).hexdigest()
    manifest_path=args.output_dir/"champions_screen_classifier.json"
    manifest_path.write_text(json.dumps(manifest,ensure_ascii=False,indent=2),encoding="utf-8")
    result={
        "training_images":len(training),
        "held_out_images":len(testing),
        "held_out_sessions":len(held_out),
        "metrics":metrics,
        "quality_gate_passed":validated,
        "manifest_validated":manifest["validated"],
        "device":str(device),
        "pretrained":bool(args.pretrained),
        "output":str(args.output_dir),
    }
    print(json.dumps(result,ensure_ascii=False,indent=2))
    if not validated:
        print("EXPERIMENTAL ONLY: thresholds or held-out sample counts were not met.")
    return result


def build_parser() -> argparse.ArgumentParser:
    parser=argparse.ArgumentParser()
    parser.add_argument("--data-dir",type=Path,required=True)
    parser.add_argument("--output-dir",type=Path,required=True)
    parser.add_argument("--epochs",type=int,default=12)
    parser.add_argument("--batch-size",type=int,default=32)
    parser.add_argument("--lr",type=float,default=0.0003)
    parser.add_argument("--seed",type=int,default=42)
    parser.add_argument("--confidence",type=float,default=0.90)
    parser.add_argument("--pretrained",action="store_true")
    parser.add_argument("--approve-if-thresholds-pass",action="store_true")
    parser.add_argument("--device",choices=["auto","cpu","cuda"],default="auto")
    parser.add_argument("--validate-only",action="store_true",
                        help="inspect labelled local data and session split without training")
    return parser


def main(argv: Sequence[str] | None=None) -> int:
    args=build_parser().parse_args(argv)
    if args.validate_only:
        report=validate_dataset(args.data_dir,seed=args.seed,progress=True)
        print(json.dumps(report,ensure_ascii=False,indent=2))
        return 0 if report["ready"] else 2
    try:
        train_model(args)
    except DatasetValidationError as exc:
        print(json.dumps(exc.report,ensure_ascii=False,indent=2))
        return 2
    except ValueError as exc:
        print(f"TRAINING_ERROR: {exc}",file=sys.stderr,flush=True)
        return 2
    return 0


if __name__=="__main__":
    raise SystemExit(main())
