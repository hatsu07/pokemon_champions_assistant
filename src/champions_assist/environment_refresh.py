"""Local and explicitly refreshed Pokémon Champions Singles environment evidence.

The external provider is descriptive usage data, NEVER authoritative move
ownership, opponent set proof, safe-switch, battle results, or tactical grades.
No fetch is performed during a match, 40s PREP, or a turn.
"""
from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
from urllib.request import Request, urlopen

from .app_config import ConfigurationError, atomic_write_text

SOURCE_URL = ("https://raw.githubusercontent.com/Gheist23/pokemonbattledata/"
              "main/data/builder/meta-singles.json")
MAX_BYTES = 8 * 1024 * 1024
MIN_VALID_ENTRIES = 1


def _utc() -> str:
    return datetime.now(timezone.utc).isoformat()


def _safe_text(value: object) -> str:
    if not isinstance(value, str):
        return ""
    return value.strip().replace("|", "/").replace("\n", " ")[:160]


def validate_singles_snapshot(payload: object) -> dict:
    if not isinstance(payload, dict) or payload.get("format") != "Singles":
        raise ConfigurationError("Champions Singles統計ではありません")
    raw = payload.get("pokemon")
    if not isinstance(raw, list) or len(raw) < MIN_VALID_ENTRIES or len(raw) > 1500:
        raise ConfigurationError("Singles統計のポケモン一覧が不正です")
    compiled = []
    seen = set()
    for row in raw:
        if not isinstance(row, dict):
            raise ConfigurationError("Singles統計行が不正です")
        name = _safe_text(row.get("name"))
        position = row.get("position")
        if not name or name in seen or type(position) is not int or position < 1:
            raise ConfigurationError("Singles統計の名称・順位が不正です")
        seen.add(name)
        entry = {"name":name,"position":position}
        for category in ("moves","items","abilities","natures"):
            candidates = row.get(category, [])
            if not isinstance(candidates,list):
                raise ConfigurationError("統計ランキングの形式が不正です")
            values = []
            for pair in candidates[:20]:
                if not (isinstance(pair,list) and len(pair)==2
                        and _safe_text(pair[0])
                        and type(pair[1]) in (int,float)
                        and 0 <= pair[1] <= 100):
                    continue
                values.append([_safe_text(pair[0]),pair[1]])
            entry[category] = values
        # Read spreads only as ranked candidate distributions, never turn them
        # into verified opponent builds or substitute for AP fixed master.
        compiled.append(entry)
    generated_at = _safe_text(payload.get("generatedAt"))
    if not generated_at:
        raise ConfigurationError("外部統計の生成日時がありません")
    return {"format":"Singles","generatedAt":generated_at,"pokemon":compiled}


class SinglesEnvironmentStore:
    def __init__(self, data_root: str | Path) -> None:
        self.root=Path(data_root)/"battle-evidence"
        self.snapshot=self.root/"singles-usage.json"
        self.receipt=self.root/"singles-provenance.json"
        self.local_battles=self.root/"observed-matches.jsonl"

    def import_snapshot(self, payload: dict, *, source_url: str,
                        source_kind: str) -> dict:
        if source_kind not in ("PUBLIC_CHAMPIONS_STATS","USER_INGAME_CONFIRMED"):
            raise ConfigurationError("不明なSinglesデータソースです")
        normalized = validate_singles_snapshot(payload)
        if source_kind=="PUBLIC_CHAMPIONS_STATS" and self.receipt.is_file():
            old=json.loads(self.receipt.read_text(encoding="utf-8"))
            if (old.get("source_kind")=="USER_INGAME_CONFIRMED"
                    and old.get("generated_at","") >= normalized["generatedAt"]):
                raise ConfigurationError(
                    "ゲーム内確認情報の方が新しいため、古い公開統計で上書きしません"
                )
        raw = json.dumps(normalized,ensure_ascii=False,sort_keys=True)
        if len(raw.encode("utf-8")) > MAX_BYTES:
            raise ConfigurationError("Singles統計が容量上限を超えています")
        receipt = {
            "source_url":source_url,"source_kind":source_kind,
            "fetched_at":_utc(),"generated_at":normalized["generatedAt"],
            "format":"Singles","regulation":"UNDECLARED_BY_SOURCE",
            "number_of_pokemon":len(normalized["pokemon"]),
            "sha256":hashlib.sha256(raw.encode("utf-8")).hexdigest(),
            "warning":"USAGE_RATE_DOES_NOT_PROVE_OPPONENT_ITEM_MOVE_ABILITY_OR_SET",
        }
        self.root.mkdir(parents=True,exist_ok=True)
        # snapshot is replaced only after full validation, preserving old
        # last-good data whenever refresh errors occur.
        atomic_write_text(self.snapshot,raw)
        atomic_write_text(self.receipt,json.dumps(receipt,ensure_ascii=False,sort_keys=True,indent=2))
        return receipt

    def refresh_public_singles(self, *, fetch=None) -> dict:
        """Explicit network refresh from allowlisted public Champions source."""
        if fetch is None:
            def fetch() -> bytes:
                req=Request(SOURCE_URL,headers={"User-Agent":"PokemonChampionsAssistant/1.0"})
                with urlopen(req,timeout=12) as response:
                    body=response.read(MAX_BYTES+1)
                return body
        body=fetch()
        if not isinstance(body,bytes) or len(body)>MAX_BYTES:
            raise ConfigurationError("公開Singles統計が上限を超えたか取得できません")
        try:
            payload=json.loads(body.decode("utf-8"))
        except (UnicodeDecodeError,json.JSONDecodeError) as exc:
            raise ConfigurationError("Singles統計JSONを解析できません") from exc
        return self.import_snapshot(payload,source_url=SOURCE_URL,source_kind="PUBLIC_CHAMPIONS_STATS")

    def import_user_json(self, file_path: str | Path) -> dict:
        source=Path(file_path)
        if source.stat().st_size > MAX_BYTES:
            raise ConfigurationError("ゲーム内統計JSONが容量上限を超えています")
        data=json.loads(source.read_text(encoding="utf-8-sig"))
        return self.import_snapshot(data,source_url="USER_SUPPLIED_FILE:"+source.name,
                                    source_kind="USER_INGAME_CONFIRMED")

    def summary(self) -> dict:
        if not self.snapshot.is_file() or not self.receipt.is_file():
            return {"status":"MISSING"}
        receipt=json.loads(self.receipt.read_text(encoding="utf-8"))
        if not isinstance(receipt,dict):
            raise ConfigurationError("環境データの出典記録が不正です")
        return {"status":"CACHED",**receipt}

    def record_finished_match(self, *, match_id: str, state: dict,
                              human_or_cpu: str = "UNKNOWN") -> bool:
        """Append only a bounded, nonsecret game result; no inferred winner."""
        if state.get("phase")!="ENDED" or state.get("result") not in ("WIN","LOSS"):
            return False
        if human_or_cpu not in ("UNKNOWN","HUMAN","CPU_SUSPECTED"):
            raise ConfigurationError("対戦相手区分が不正です")
        record={
            "match_id":_safe_text(match_id),"observed_at":_utc(),
            "result":state["result"],"opponent_six":state.get("opponent_six",[])[:6],
            "selected_three":state.get("selected_three",[])[:3],
            "lead":_safe_text(state.get("lead")),
            "opponent_type":human_or_cpu,
            "source":"LOCAL_VISUAL_AI_RESULT_UNVERIFIED_BY_HUMAN",
        }
        if not record["match_id"]:
            return False
        self.root.mkdir(parents=True,exist_ok=True)
        # Private file stores only concise metadata and is never bundled.
        # Same match id must not be counted twice on repeated GUI signals.
        index=self.root/"match-ids.json"
        try:
            seen=set(json.loads(index.read_text(encoding="utf-8")))
        except FileNotFoundError:
            seen=set()
        if record["match_id"] in seen:
            return False
        if self.local_battles.is_file() and self.local_battles.stat().st_size > 5_000_000:
            return False
        with self.local_battles.open("a",encoding="utf-8") as handle:
            handle.write(json.dumps(record,ensure_ascii=False)+"\n")
        seen.add(record["match_id"])
        atomic_write_text(index,json.dumps(sorted(seen),ensure_ascii=False))
        return True
