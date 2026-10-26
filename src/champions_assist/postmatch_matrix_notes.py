"""Project bounded postmatch AI cautions into NEXT match's derived matrix.

Never modify masters or reviewed baseline. Never certify processor, safe
switch, KO or change tactical cells. Applied between matches only.
"""
from __future__ import annotations

from hashlib import sha256
import json
from pathlib import Path

from .app_config import ConfigurationError, atomic_write_text
from .matchup_learning_store import MatchupLearningStore

_MARK=" POSTMATCH_UNVERIFIED:"


def _clean(value: object, n: int) -> str:
    if not isinstance(value,str):
        return ""
    return " ".join("".join(ch for ch in value if ch.isprintable() and ch not in "|<>\r\n").split())[:n]


def publish_postmatch_notes(*, data_root: str | Path, team_id: str,
                            manifest_path: str | Path, matrix_path: str | Path) -> dict:
    matrix_path=Path(matrix_path)
    manifest_path=Path(manifest_path)
    manifest=json.loads(manifest_path.read_text(encoding="utf-8"))
    if manifest.get("status")=="REVIEWED_REUSE":
        return {"postmatch_hint_rows":0,"status":"BASELINE_UNTOUCHED"}
    before=matrix_path.read_bytes()
    if sha256(before).hexdigest()!=manifest.get("matrix_sha256"):
        raise ConfigurationError("LIVEマトリクスの監査SHAが不一致です")
    store=MatchupLearningStore(data_root)
    if not store.db_path.is_file():
        return {"postmatch_hint_rows":0,"status":"NO_FINISHED_REVIEWS"}
    reviews=store.recent_review_notes(team_id,limit=15)
    if not reviews and manifest.get("postmatch_hint_rows",0)==0:
        return {"postmatch_hint_rows":0,"status":"NO_FINISHED_REVIEWS"}
    matched={}
    for review in reviews:
        for item in review.get("findings",[])[:8]:
            opponent=_clean(item.get("opponent"),80)
            if not opponent or opponent in matched:
                continue
            issue=_clean(item.get("issue"),70)
            suggestion=_clean(item.get("suggestion"),65)
            if issue and suggestion:
                matched[opponent]=f"{issue}/{suggestion}"
    raw=before.decode("utf-8-sig")
    output=[]
    appended=0
    for line in raw.splitlines():
        if not line or line.startswith("#"):
            output.append(line)
            continue
        row=line.split("|")
        if len(row)!=14:
            raise ConfigurationError("構築別LIVE行の形式が不正です")
        # Never keep old learning notes if a review was revised or removed.
        original=row[11].split(_MARK,1)[0]
        if original!="-" and not original.startswith("AI_UNVERIFIED:"):
            raise ConfigurationError("未検証以外のLIVE戦術欄は更新しません")
        caution=matched.get(row[0])
        row[11]=(original+(_MARK+_clean(caution,135) if caution else ""))[:720]
        if caution:
            appended+=1
        output.append("|".join(row))
    rendered="\n".join(output)+"\n"
    if rendered!=raw:
        atomic_write_text(matrix_path,rendered)
    updated_sha=sha256(rendered.encode("utf-8")).hexdigest()
    if (manifest.get("matrix_sha256")!=updated_sha
            or manifest.get("postmatch_hint_rows")!=appended):
        manifest["matrix_sha256"]=updated_sha
        manifest["postmatch_hint_rows"]=appended
        manifest["postmatch_hint_source"]="AI_HYPOTHESIS_NOT_OBSERVED_FACT"
        atomic_write_text(manifest_path,json.dumps(
            manifest,ensure_ascii=False,sort_keys=True,indent=2))
    return {"postmatch_hint_rows":appended,
            "status":"NEXT_MATCH_ADVISORY_ONLY"}
