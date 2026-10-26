"""Evidence-bounded, per-team LIVE assets, prepared strictly *between* matches.

This is not a matchup simulator. A column is reused only when its actual
registered Pokémon set exactly matches the reviewed baseline set. All other
matchups remain UNVERIFIED, never invented as a safe switch or processor.
"""
from __future__ import annotations

import csv
from dataclasses import dataclass
from hashlib import sha256
from io import StringIO
import json
from pathlib import Path
import re

from .app_config import ConfigurationError, atomic_write_text
from .species_name_aliases import selection_base_name
from .matchup_evidence import build_candidate_catalog, certified_review_counts, validate_review_packet


MATRIX = "Pokemon_Champions_MC_live_selection_matrix.txt"
LOOKUP = "Pokemon_Champions_MC_60sec_lookup_master.txt"
BASE_TEAM = "Pokemon_Champions_MC_team_2026-10-03.csv"
GENERATED_VERSION = "team-assets-v2-candidate-evidence"
SET_FIELDS = ("name", "item", "ability", "nature", "ap_h_a_b_c_d_s",
              "move1", "move2", "move3", "move4", "choice_lock",
              "form_change", "priority", "setup", "recovery", "action_guarantee")
UNKNOWN_CELL = "?,?,?,?,U,0"   # Explicitly unknown; never a processor or safe switch.


def _jsonhash(value: object) -> str:
    return sha256(json.dumps(value, ensure_ascii=False, sort_keys=True,
                             separators=(",", ":")).encode("utf-8")).hexdigest()


def _parsed_team(source: Path) -> tuple[dict[str, str], ...]:
    rows = tuple(r for r in csv.DictReader(source.read_text(encoding="utf-8-sig").splitlines())
                 if r.get("record") == "MON")
    if len(rows) != 6:
        raise ConfigurationError("LIVE構築は6匹のMON行が必要です")
    names = [selection_base_name(m["name"].split("→")[-1].strip()) for m in rows]
    if any(not n for n in names) or len(set(names)) != 6:
        raise ConfigurationError("LIVE構築のポケモン名が不足・重複しています")
    return rows


def _signature(member: dict[str, str]) -> tuple[str, ...]:
    # Slots are compared by meaningful on-field data, never by species alone.
    fields = tuple((member.get(field) or "").strip() for field in SET_FIELDS)
    return (selection_base_name(fields[0].split("→")[-1].strip()), *fields[1:])


def _pipe_rows(body: str, expected: int) -> tuple[list[str], dict[str, list[str]]]:
    comments = []
    rows = {}
    for line in body.splitlines():
        if not line.strip():
            continue
        if line.startswith("#"):
            comments.append(line)
            continue
        cols = line.split("|")
        if len(cols) != expected or not cols[0] or cols[0] in rows:
            raise ConfigurationError("LIVEマスターに列数不整合・重複があります")
        rows[cols[0]] = cols
    if not rows:
        raise ConfigurationError("LIVEマスターに行がありません")
    return comments, rows


@dataclass(frozen=True)
class PreparedTeamAssets:
    matrix_path: Path
    lookup_path: Path
    manifest_path: Path
    status: str
    reviewed_members: int
    opponent_rows: int
    cache_hit: bool


def prepare_team_assets(*, master_dir: Path | str, data_root: Path | str,
                        team_id: str, team_path: Path | str,
                        environment_snapshot: Path | str | None = None) -> PreparedTeamAssets:
    """Build/cache immutable, per-team two-source files, with full provenance.

    No network IO, no AI call, no file writes to bundled masters. Caller must
    invoke before starting match/capture, never inside PREP or a battle.
    """
    if not re.fullmatch("[a-f0-9]{32}", team_id):
        raise ConfigurationError("不正なチームID")
    master = Path(master_dir)
    source = Path(team_path)
    registered = _parsed_team(source)
    baseline = _parsed_team(master / BASE_TEAM)
    matrix_bytes = (master / MATRIX).read_bytes()
    lookup_bytes = (master / LOOKUP).read_bytes()
    matrix_comments, matrix_rows = _pipe_rows(matrix_bytes.decode("utf-8-sig"), 14)
    lookup_comments, lookup_rows = _pipe_rows(lookup_bytes.decode("utf-8-sig"), 12)
    if set(matrix_rows) != set(lookup_rows):
        raise ConfigurationError("LIVEマトリクスとルックアップの相手行が一致しません")
    own_line = next((c for c in matrix_comments if c.startswith("# OWN={")), "")
    original_names = re.findall(r"(?:^|,)[A-Z]:([^,}]+)", own_line[len("# OWN={"):-1])
    if len(original_names) != 6 or len(set(original_names)) != 6:
        raise ConfigurationError("元のLIVEマトリクスの自軍定義が不正です")
    original_by_name = {selection_base_name(x): i for i, x in enumerate(original_names)}
    baseline_by_name = {_signature(m)[0]:m for m in baseline}
    names = [_signature(m)[0] for m in registered]
    # A canonical/target Mega name may intentionally contain a different form
    # alias: never reuse without the exact full battle set.
    matched_columns = []
    for member in registered:
        sig = _signature(member)
        original = baseline_by_name.get(sig[0])
        matched_columns.append(
            original_by_name.get(sig[0])
            if original is not None and _signature(original) == sig
            else None
        )
    snapshot_sha = ""
    env_rows: dict[str, dict] = {}
    env_date = ""
    env_kind = "NONE"
    if environment_snapshot is not None and Path(environment_snapshot).is_file():
        env_bytes = Path(environment_snapshot).read_bytes()
        snapshot_sha = sha256(env_bytes).hexdigest()
        env = json.loads(env_bytes)
        if env.get("format") != "Singles" or not isinstance(env.get("pokemon"), list):
            raise ConfigurationError("シングル統計スナップショット形式が不正です")
        env_date = str(env.get("generatedAt", ""))[:32]
        receipt_path=Path(environment_snapshot).with_name("singles-provenance.json")
        if receipt_path.is_file():
            receipt=json.loads(receipt_path.read_text(encoding="utf-8"))
            if receipt.get("sha256") == _jsonhash(env) or receipt.get("sha256") == snapshot_sha:
                env_kind=str(receipt.get("source_kind","UNKNOWN"))[:64]
        for entry in env["pokemon"]:
            if isinstance(entry, dict) and isinstance(entry.get("name"), str):
                env_rows[selection_base_name(entry["name"])] = entry
    review_path = Path(data_root)/"matchup-reviews"/(team_id+".json")
    review_bytes = review_path.read_bytes() if review_path.is_file() else b""
    if len(review_bytes) > 2*1024*1024:
        raise ConfigurationError("レビューJSONのサイズ上限超過")
    fingerprint = _jsonhash({
        "version":GENERATED_VERSION, "form_evidence_revision":"exact-form-v2",
        "team":registered, "master_matrix":sha256(matrix_bytes).hexdigest(),
        "master_lookup":sha256(lookup_bytes).hexdigest(),
        "baseline":_jsonhash(baseline), "environment":snapshot_sha,
        "environment_kind":env_kind,"reviews_sha256":sha256(review_bytes).hexdigest(),
    })
    directory = Path(data_root)/"live-team-assets"/team_id/fingerprint
    matrix_path, lookup_path, manifest_path = directory/"matrix.txt", directory/"lookup.txt", directory/"manifest.json"
    evidence_path=directory/"matchup-evidence.json"
    if all(p.is_file() for p in (matrix_path,lookup_path,manifest_path,evidence_path)):
        saved = json.loads(manifest_path.read_text(encoding="utf-8"))
        if (saved.get("fingerprint")==fingerprint
                and sha256(matrix_path.read_bytes()).hexdigest()==saved.get("matrix_sha256")
                and sha256(lookup_path.read_bytes()).hexdigest()==saved.get("lookup_sha256")
                and sha256(evidence_path.read_bytes()).hexdigest()==saved.get("candidate_sha256")):
            return PreparedTeamAssets(matrix_path,lookup_path,manifest_path,
                                      saved["status"],saved["reviewed_members"],
                                      saved["opponent_rows"],True)
    full = all(i is not None for i in matched_columns)
    team_reg=(registered[0].get("reg") or "UNDECLARED").strip()
    catalog=build_candidate_catalog(
        master_dir=master,team=registered,matrix_rows=matrix_rows,
        fields=SET_FIELDS,regulation=team_reg)
    review_counts={"CONFIRMED":0,"CONDITIONAL":0}
    reviews={}
    review_warning=""
    if review_bytes:
        try:
            packet=json.loads(review_bytes)
            approved=validate_review_packet(
                packet,team_id=team_id,regulation=catalog["regulation"],
                signatures=catalog["member_signatures"],
                opponents=set(matrix_rows))
            reviews={(item["opponent"],item["slot"]):item for item in approved}
            review_counts={
                "CONFIRMED":sum(item["status"]=="CONFIRMED" for item in approved),
                "CONDITIONAL":sum(item["status"]=="CONDITIONAL" for item in approved),
            }
        except (ConfigurationError,ValueError,TypeError) as exc:
            # A modified team or stale/corrupt review must never block the next
            # four-file preloading. It contributes zero trusted evaluations.
            review_warning="STALE_OR_INVALID_REVIEW_IGNORED:"+type(exc).__name__
    for entry in catalog["cells"]:
        slot=entry["slot"]
        reviewed=reviews.get((entry["opponent"],slot))
        if reviewed is not None:
            entry["evaluation"]=reviewed["status"]
            entry["review_summary"]={
                "reviewed_at":reviewed["reviewed_at"],
                "source_count":len(reviewed["sources"]),
                "rationale":reviewed["rationale"],
                "conditions":reviewed["conditions"],
                "processor":"UNVERIFIED","safe_switch":"UNVERIFIED",
                "role_observations":reviewed["role_observations"],
                "role_guarantee":"CONDITIONAL_ONLY_NOT_CERTIFIED",
            }
        elif matched_columns[slot] is not None:
            entry["evaluation"]="CONDITIONAL"
            entry["review_summary"]={
                "source_file":MATRIX,
                "source_sha256":sha256(matrix_bytes).hexdigest(),
                "note":"LEGACY_EVALUATION_REUSED_EXACT_BATTLE_SET; NO_NEW_KO_CERTIFICATION",
            }
    catalog["counts"]={
        "CONFIRMED":sum(x["evaluation"]=="CONFIRMED" for x in catalog["cells"]),
        "CONDITIONAL":sum(x["evaluation"]=="CONDITIONAL" for x in catalog["cells"]),
        "UNKNOWN":sum(x["evaluation"]=="UNKNOWN" for x in catalog["cells"]),
    }
    catalog["quality_gate"]={
        "evidence_complete":catalog["counts"]["UNKNOWN"]==0,
        "master_missing_cells":sum(bool(x["missing_source"]) for x in catalog["cells"]),
        "independent_real_battle_e2e":"NOT_VALIDATED",
        "champions_damage_engine":"NOT_VERIFIED",
        "live_ready":False,
        "legacy_matrix_reuse_allowed":full,
        "reason":"CANDIDATE_EVIDENCE_NEVER_AUTOMATICALLY_CERTIFIES_LIVE_READY",
    }
    status = "REVIEWED_REUSE" if full else "PROVISIONAL_UNVERIFIED"
    # Only verified human-supplied observation *markers* may enter LIVE.
    # Even two independently referenced observations are NOT permission to
    # infer an on-field processor, safe hard switch, damage or speed order.
    # Model suggestions never feed this path.
    def review_marker(opponent: str) -> str:
        flags = []
        for slot in range(6):
            item = reviews.get((opponent, slot))
            if item is not None:
                flag = "2根拠観測" if item["status"] == "CONFIRMED" else "条件付観測"
                flags.append(chr(65 + slot) + ":" + flag)
        return "人間確認(" + ",".join(flags) + ";処理受け未認定)" if flags else ""
    annotation_count = len(reviews)
    # Verified only against Champions move/learnset masters; an available
    # priority move is not proof the opponent actually has or selects it.
    priority_candidates = {}
    for entry in catalog["cells"]:
        if entry["slot"] == 0 and not any(
            reason in entry["missing_source"] for reason in
            ("OPPONENT_SPECIES_MASTER_NOT_FOUND","OPPONENT_LEARNSET_NOT_FOUND")
        ):
            moves = entry.get("opponent_priority_candidates", [])
            if moves:
                priority_candidates[entry["opponent"]] = moves[:3]
    # Preserve fixed source verbatim if the exact same six battle sets and
    # slot order. Even a reordered team gets new OWN/FAST6 column ordering.
    original_order = list(range(6))
    if full and matched_columns == original_order and not env_rows and not reviews:
        matrix_text = matrix_bytes.decode("utf-8-sig")
        lookup_text = lookup_bytes.decode("utf-8-sig")
    else:
        marker = "# DERIVED_STATUS="+status+"; UNVERIFIED_CELLS_ARE_NOT_PROCESSORS_OR_SAFE_SWITCHES"
        own_map = ",".join(chr(65+i)+":"+name for i,name in enumerate(names))
        header = [
            "# TEAM_SCOPED_MATRIX v1; source: "+MATRIX,
            "# OWN={"+own_map+"}", marker,
            "# CELL=rate,spd,move,x,recv,ins; ?=UNVERIFIED; U=NOT_SAFE_TO_SWITCH",
            "# PROVENANCE=EXACT_REGISTERED_SET_ONLY; NO_CROSS_GAME_INFERENCE",
            "# ENV_GENERATED_AT="+env_date+"; ENV_SOURCE_KIND="+env_kind,
            "# ENV_USAGE_IS_NOT_OPPONENT_SET_PROOF",
            "# HUMAN_REVIEW=OBSERVATION_ONLY; DOES_NOT_CERTIFY_PROCESSOR_OR_SAFE_SWITCH",
            "# AUTO_CHAMPIONS_PRIORITY=LEARNSET_POSSIBILITY_ONLY; OPPONENT_SET_UNKNOWN",
        ]
        # Reuse only opponent-only type/base-speed/warning/risk and reviewed
        # original cells. Recompute processor summary solely from reused cells.
        derived_matrix = []
        derived_lookup = []
        lookup_header = [
            "# TEAM_SCOPED_LOOKUP v1; source: "+LOOKUP,
            "# DERIVED_STATUS="+status,
            "# UNVERIFIED_PROCESSORS=NONE; NO_SAFETY_CLAIMS",
            "# ENV_GENERATED_AT="+env_date+"; ENV_STATS_ARE_NON_DETERMINISTIC",
            "# HUMAN_REVIEW=OBSERVATION_ONLY; UNKNOWN_TACTICAL_CELLS_REMAIN_UNKNOWN",
        ]
        for key, old in matrix_rows.items():
            original_lookup = lookup_rows[key]
            original_proc = [x.strip() for x in old[9].split("/") if x.strip()]
            cells = [old[3+idx] if idx is not None else UNKNOWN_CELL
                     for idx in matched_columns]
            verified = {name for name,idx in zip(names,matched_columns)
                        if idx is not None}
            processors = [x for x in original_proc if x in verified]
            fast = old[12].split("/")
            hints = [fast[idx] if idx is not None and idx < len(fast) else "??"
                     for idx in matched_columns]
            # Keep only general opponent warnings, not old team's scenario notes.
            env_extra = env_rows.get(selection_base_name(key), {})
            common = env_extra.get("moves", [])
            top_moves = []
            if isinstance(common, list):
                for pair in common[:2]:
                    if (isinstance(pair, list) and len(pair)==2
                            and isinstance(pair[0],str)
                            and isinstance(pair[1], (int,float))):
                        top_moves.append(pair[0]+"@"+str(pair[1])+"%")
            warn = old[10]
            if top_moves:
                warn += ";環境技(型未確定):"+",".join(top_moves)
            for category, label in (("items","持ち物"),("abilities","特性")):
                entries=env_extra.get(category,[])
                if isinstance(entries,list) and entries:
                    pair=entries[0]
                    if (isinstance(pair,list) and len(pair)==2
                            and isinstance(pair[0],str)
                            and isinstance(pair[1],(int,float))):
                        name=pair[0].replace("|","/").replace("\n"," ")[:36]
                        warn += ";環境"+label+"(未確定):"+name+"@"+str(pair[1])+"%"
            # Pipe and line delimiters never originate from external untrusted
            # source text. Silently drop them from any optional stat labels.
            marker = review_marker(key)
            if marker:
                warn = warn[:320] + ";" + marker
            names = priority_candidates.get(key, ())
            if names:
                cleaned = [x.replace("|", "/").replace("\n", " ")[:35] for x in names]
                hint = "Champions習得可能な優先度+技(採用未確定):" + ",".join(cleaned)
                warn = warn[:max(0, 499-len(hint))] + ";" + hint
            warn = warn.replace("|", "/").replace("\n", " ")[:500]
            derived_matrix.append("|".join([
                key,old[1],old[2],*cells,
                "/".join(processors) if processors else "-",
                warn,"-", "/".join(hints),old[13],
            ]))
            # Processors/hits/plans refer to the old 6; do not transpose unless
            # all six exact members were reused.
            if full:
                lookup = list(original_lookup)
                lookup[10] = warn
            else:
                lookup = [key,original_lookup[1],original_lookup[2],
                          "-","-","-","-",original_lookup[7],
                          original_lookup[8],"-",warn,"L"]
            derived_lookup.append("|".join(lookup))
        matrix_text = "\n".join(header+derived_matrix)+"\n"
        lookup_text = "\n".join(lookup_header+derived_lookup)+"\n"
    # Pre-validate complete rows before making the manifest visible.
    _pipe_rows(matrix_text,14)
    _pipe_rows(lookup_text,12)
    candidate_text=json.dumps(catalog,ensure_ascii=False,sort_keys=True,separators=(",",":"))
    directory.mkdir(parents=True,exist_ok=True)
    atomic_write_text(matrix_path,matrix_text)
    atomic_write_text(lookup_path,lookup_text)
    atomic_write_text(evidence_path,candidate_text)
    manifest = {
        "schema":GENERATED_VERSION,"team_id":team_id,"fingerprint":fingerprint,
        "status":status,"reviewed_members":sum(i is not None for i in matched_columns),
        "opponent_rows":len(matrix_rows),"unverified_cells":len(matrix_rows)*sum(i is None for i in matched_columns),
        "team_file":source.name,"matrix_source":MATRIX,"lookup_source":LOOKUP,
        "environment_source_sha256":snapshot_sha,"environment_generated_at":env_date,
        "environment_source_kind":env_kind,
        "matrix_sha256":sha256(matrix_text.encode("utf-8")).hexdigest(),
        "lookup_sha256":sha256(lookup_text.encode("utf-8")).hexdigest(),
        "rules":"NO_AUTOMATIC_PROMOTION_OF_UNVERIFIED_PROCESSOR_OR_SAFE_SWITCH",
        "review_input_sha256":sha256(review_bytes).hexdigest() if review_bytes else None,
        "review_status_counts":review_counts,
        "live_observation_markers":annotation_count,
        "auto_master_priority_warning_rows":len(priority_candidates),
        "auto_master_priority_status":"LEARNSET_ONLY_NOT_OPPONENT_SET",
        "live_grade_certified_from_reviews":0,
        "review_warning":review_warning,
        "candidate_file":"matchup-evidence.json",
        "candidate_sha256":sha256(candidate_text.encode("utf-8")).hexdigest(),
        "candidate_status_counts":catalog["counts"],
        "unknown_rate":round(catalog["counts"]["UNKNOWN"]/catalog["total_cells"],6),
        "quality_gate":catalog["quality_gate"],
    }
    atomic_write_text(manifest_path,json.dumps(manifest,ensure_ascii=False,sort_keys=True,indent=2))
    return PreparedTeamAssets(matrix_path,lookup_path,manifest_path,status,
                              manifest["reviewed_members"],len(matrix_rows),False)
