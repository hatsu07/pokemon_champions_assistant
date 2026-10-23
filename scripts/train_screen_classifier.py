"""Train an *experimental* Champions UI classifier from consented local captures.

Run only in the Docker development environment (torch/torchvision/onnx are
optional training dependencies, NEVER installed on the user's Windows host).

Layout:
  private-training/screens/TEAM_SELECTION/session01__001.png
  private-training/screens/MOVE_MENU/session01__002.png
  ...
Each filename prefix before '__' is a recording/session ID. Train/validation
splits hold out whole sessions to prevent near-identical video frame leakage.

This script does not label or acquire data, download screenshots, or certify
a model. --pretrained opts into downloading third-party weights explicitly.
"""
from __future__ import annotations

import argparse
from collections import Counter
from hashlib import sha256
import json
from pathlib import Path
import random
from typing import Sequence

LABELS=("MATCHMAKING","TEAM_SELECTION","MOVE_MENU","PARTY_MENU",
        "BATTLE_EVENT","RESULT","UNKNOWN")
FORMATS={".png",".jpg",".jpeg",".webp"}
SIZE=(160,160)


def collect(root: Path):
    samples=[]
    for name in LABELS:
        folder=root/name
        if not folder.is_dir():
            raise ValueError(f"Missing class directory: {folder}")
        for file in folder.rglob("*"):
            if file.suffix.lower() not in FORMATS:
                continue
            if "__" not in file.stem:
                raise ValueError(f"Frame must carry session prefix '__': {file}")
            session=file.stem.split("__",1)[0]
            if not session:
                raise ValueError(f"Missing session ID: {file}")
            samples.append((file,LABELS.index(name),session))
    if not samples:
        raise ValueError("No training images available")
    return samples


def split_by_session(samples, *, seed=42, fraction=0.2):
    sessions=sorted({r[2] for r in samples})
    if len(sessions)<4:
        raise ValueError("Need at least four distinct recorded matches")
    random.Random(seed).shuffle(sessions)
    holdout=set(sessions[:max(1,round(len(sessions)*fraction))])
    train=[r for r in samples if r[2] not in holdout]
    test=[r for r in samples if r[2] in holdout]
    for part,values in (("training",train),("validation",test)):
        counts=Counter(x[1] for x in values)
        if any(counts[i]==0 for i in range(len(LABELS))):
            raise ValueError(f"Missing label in {part}: {dict(counts)}")
    return train,test,holdout


def train_model(args):
    import torch
    from PIL import Image
    from torch import nn
    from torch.utils.data import DataLoader,Dataset
    from torchvision import models,transforms

    records=collect(args.data_dir)
    training,testing,held_out=split_by_session(records,seed=args.seed)
    random.seed(args.seed)
    torch.manual_seed(args.seed)
    if torch.cuda.is_available():
        torch.cuda.manual_seed_all(args.seed)
    if args.pretrained:
        # Explicit opt-in; downloads/cache and weight license should be
        # evaluated by the maintainer before redistribution.
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
        def __len__(self):return len(self.items)
        def __getitem__(self,index):
            path,label,_=self.items[index]
            with Image.open(path) as img:
                rgb=img.convert("RGB")
                return self.transform(rgb),label

    training_loader=DataLoader(ScreenData(training,True),batch_size=args.batch_size,
                               shuffle=True,num_workers=0)
    test_loader=DataLoader(ScreenData(testing,False),batch_size=args.batch_size,
                           shuffle=False,num_workers=0)
    model=models.mobilenet_v3_small(weights=weights)
    in_features=model.classifier[-1].in_features
    model.classifier[-1]=nn.Linear(in_features,len(LABELS))
    device=torch.device("cuda" if args.device=="auto" and torch.cuda.is_available()
                        else args.device if args.device!="auto" else "cpu")
    model.to(device)
    optimizer=torch.optim.AdamW(model.parameters(),lr=args.lr)
    objective=nn.CrossEntropyLoss()
    for epoch in range(args.epochs):
        model.train()
        loss_sum=0.0
        for image,label in training_loader:
            image,label=image.to(device),label.to(device)
            optimizer.zero_grad()
            loss=objective(model(image),label)
            loss.backward()
            optimizer.step()
            loss_sum+=float(loss.item())*len(label)
        print(f"epoch={epoch+1}/{args.epochs} train_loss={loss_sum/len(training):.4f}")

    model.eval()
    correct=Counter()
    total=Counter()
    predicted=Counter()
    fp=Counter()
    with torch.no_grad():
        for image,label in test_loader:
            guesses=model(image.to(device)).argmax(1).cpu().tolist()
            for expected,guess in zip(label.tolist(),guesses):
                total[expected]+=1
                predicted[guess]+=1
                if expected==guess:
                    correct[expected]+=1
                else:
                    fp[guess]+=1
    metrics={
        LABELS[i]:{
            "support":total[i],
            "recall":round(correct[i]/total[i],5) if total[i] else 0,
            "precision":round(correct[i]/predicted[i],5) if predicted[i] else 0,
            "false_positives":fp[i],
        } for i in range(len(LABELS))
    }
    selected=metrics["TEAM_SELECTION"]
    validated=(len(held_out)>=5 and selected["support"]>=100
               and selected["recall"]>=0.99
               and selected["precision"]>=0.995)
    # Other labels must be evaluated before any real release; accepting one
    # class alone is deliberately forbidden by the optional guard.
    validated=validated and all(
        m["support"]>=30 and m["recall"]>=0.95 for m in metrics.values())
    args.output_dir.mkdir(parents=True,exist_ok=True)
    model_path=args.output_dir/"champions_screen_classifier.onnx"
    model_cpu=model.cpu()
    torch.onnx.export(
        model_cpu,torch.zeros(1,3,SIZE[1],SIZE[0]),str(model_path),
        input_names=["rgb"],output_names=["logits"],opset_version=17)
    manifest={
        "schema_version":1,
        "labels":list(LABELS),
        "input_size":list(SIZE),
        "mean":mean,
        "std":std,
        "sha256":sha256(model_path.read_bytes()).hexdigest(),
        "min_confidence":args.confidence,
        "min_margin":0.20,
        "validated":bool(validated and args.approve_if_thresholds_pass),
        "holdout_sessions":len(held_out),
        "metrics":metrics,
        "provenance":"locally labelled Pokémon Champions sessions",
    }
    manifest_path=args.output_dir/"champions_screen_classifier.json"
    manifest_path.write_text(json.dumps(manifest,ensure_ascii=False,indent=2),encoding="utf-8")
    print(json.dumps({
        "training_images":len(training),
        "held_out_images":len(testing),
        "held_out_sessions":len(held_out),
        "metrics":metrics,
        "quality_gate_passed":validated,
        "manifest_validated":manifest["validated"],
        "output":str(args.output_dir),
    },ensure_ascii=False,indent=2))
    if not validated:
        print("EXPERIMENTAL ONLY: thresholds or held-out sample counts were not met.")


if __name__=="__main__":
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
    train_model(parser.parse_args())
