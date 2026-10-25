"""Local image-payload diagnostics and controlled off-match recognition benchmarks.

Never write pixels, data URLs, credential values or request/response bodies into
JSONL logs. Pixel dimensions and transmitted image-byte counts are measured
from the exact JPEG data URI produced by the live selection content builder.
"""
from __future__ import annotations

import base64
from hashlib import sha256
from time import perf_counter

import cv2
import numpy as np

from .selection_vision import VISION_SCHEMA, _stream_json, selection_image_content
from .species_name_aliases import selection_base_name


def resize_same_image(rgb: np.ndarray, percent: int) -> np.ndarray:
    """Deterministic downsampling of ONE original screenshot, no upscaling."""
    if percent not in (100, 50):
        raise ValueError("UNSUPPORTED_RESOLUTION_VARIANT")
    if rgb.ndim!=3 or rgb.shape[2]!=3 or rgb.dtype!=np.uint8:
        raise ValueError("INVALID_RGB_IMAGE")
    if percent==100:
        return np.ascontiguousarray(rgb)
    h,w=rgb.shape[:2]
    return cv2.resize(rgb,(max(1,w*percent//100),max(1,h*percent//100)),
                      interpolation=cv2.INTER_AREA)


def payload_samples(image: np.ndarray, *, include_full: bool=False) -> list[dict]:
    """Images as actually encoded by the selection API request (JPEG RGB)."""
    content=selection_image_content(
        image,introduction="Selection opponent-six preview",
        include_full=include_full)
    output=[]
    for entry in content:
        if entry.get("type")!="input_image":
            continue
        uri=entry["image_url"]
        encoded=base64.b64decode(uri.partition(",")[2],validate=True)
        data=cv2.imdecode(np.frombuffer(encoded,dtype=np.uint8),
                          cv2.IMREAD_COLOR)
        if data is None:
            raise ValueError("PREVIEW_JPEG_INVALID")
        h,w=data.shape[:2]
        output.append({
            "jpeg":encoded,
            "width":w,"height":h,
            "jpeg_bytes":len(encoded),
            "data_uri_bytes":len(uri.encode("ascii")),
            "detail":entry["detail"],
        })
    return output


def payload_statistics(rgb: np.ndarray, content: list[dict]) -> dict:
    """Flat, privacy-filterable metadata for first or retry API requests."""
    h,w=rgb.shape[:2]
    sample=[]
    for entry in content:
        if entry.get("type")!="input_image":
            continue
        uri=entry["image_url"]
        encoded=base64.b64decode(uri.partition(",")[2],validate=True)
        mat=cv2.imdecode(np.frombuffer(encoded,dtype=np.uint8),cv2.IMREAD_COLOR)
        if mat is None:
            raise ValueError("INVALID_ENCODED_JPEG")
        ih,iw=mat.shape[:2]
        sample.append((iw,ih,len(encoded),len(uri.encode("ascii"))))
    if not sample:
        raise ValueError("NO_OUTBOUND_IMAGE")
    # Selection uses optional low-detail full frame first, high-detail right
    # crop LAST. Fallback sends exactly one whole frame.
    pw,ph,pbytes,puri=sample[-1]
    cropped=(pw<w and ph==h)
    return {
        "visual_source_width":int(w),"visual_source_height":int(h),
        "visual_crop_x":int(w-pw) if cropped else 0,
        "visual_crop_y":0,
        "visual_crop_width":int(pw),"visual_crop_height":int(ph),
        "visual_primary_jpeg_bytes":int(pbytes),
        "visual_total_jpeg_bytes":sum(item[2] for item in sample),
        "visual_total_data_uri_bytes":sum(item[3] for item in sample),
        "visual_image_count":len(sample),
    }


def truth_slots(value: str) -> tuple[str,...]:
    """User-supplied ground truth for scoring, NEVER included in AI prompts."""
    parts=[v.strip() for v in value.replace("／","/").split("/") if v.strip()]
    if len(parts)!=6:
        raise ValueError("正解6匹は「/」区切りで6匹指定してください")
    canonical=tuple(selection_base_name(x) for x in parts)
    if not all(canonical) or len(set(canonical))!=6:
        raise ValueError("正解6匹の名称に不足・重複があります")
    return canonical


def score_observed_six(observed: list[str], correct: tuple[str,...] | None):
    """Slot-exact score only when independently entered reference is present."""
    if correct is None or len(correct)!=6:
        return None
    return sum(selection_base_name(v)==ref for v,ref in zip(observed,correct))


def run_recognition_comparison(rgb: np.ndarray, *, access_token: str,
                               model: str, assets=None,
                               correct: tuple[str,...] | None = None,
                               client_factory=None, timeout_seconds: float=40.0) -> dict:
    """One real ChatGPT vision call (NO tactical request) on one image variant.

    Failed matrix validation is reported, not normalized into another species.
    The caller must run the same function sequentially for each resolution
    and currently account-listed model.
    """
    from .selection_vision import SelectionBlocked,verify_selection_observation
    start=perf_counter()
    content=selection_image_content(
        rgb,introduction=("Extract ONLY the SIX opponent Pokémon on the RIGHT "
                          "in top-to-bottom order. Own team is preloaded; return "
                          "our_six as []. Mark uncertain opponent positions "
                          "instead of inferring unseen species."))
    stat=payload_statistics(rgb,content)
    report={**stat,"model":model,"status":"ERROR",
            "correct_slots":-1,"comparison_elapsed_ms":0,
            "opponent_recognition_attempts":1,"observed_ui":"UNKNOWN"}
    try:
        if client_factory is None:
            from openai import OpenAI
            client_factory=OpenAI
        client=client_factory(
            api_key=access_token,base_url="https://api.openai.com/v1",
            max_retries=0,timeout=timeout_seconds)
        observed=_stream_json(
            client,model=model,schema=VISION_SCHEMA,
            name="champions_opponent_six_comparison",
            reasoning_effort="low",
            instructions=("Recognize only the right-hand six visible Pokémon, "
                          "top to bottom, from Pokémon Champions team selection. "
                          "Return unknown positions instead of guessing. "
                          "Do not use any ground truth: none is provided."),
            content=content)
        raw=observed.get("opponent_six")
        names=[n for n in raw[:6] if isinstance(n,str)] if isinstance(raw,list) else []
        report["opponent_six"]=names
        report["observed_ui"]=str(observed.get("visual_ui","UNKNOWN"))
        if correct is not None:
            report["correct_slots"]=score_observed_six(names,correct)
        if assets is not None:
            try:
                verify_selection_observation(observed,assets)
                report["status"]="RECOGNIZED"
                report["reason"]="NONE"
            except SelectionBlocked as exc:
                report["status"]="BLOCKED"
                report["reason"]=str(exc)
        else:
            report["status"]="UNVERIFIED"
            report["reason"]="NO_CHAMPIONS_MATRIX"
    except Exception as exc:
        report["reason"]=type(exc).__name__
    report["comparison_elapsed_ms"]=round((perf_counter()-start)*1000)
    return report
