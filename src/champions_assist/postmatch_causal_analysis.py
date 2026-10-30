"""P2 #115: strictly after-match, evidence-bounded GPT cause/O-X research.

Model conclusions are hypotheses, never facts/validated tactics. No read is
performed from the live state path; this workflow can only be run once a P1
batch exists and at least three archived independent matches verify cleanly.
"""
from __future__ import annotations

from contextlib import closing
from datetime import datetime, timezone
from hashlib import sha256
import json
from pathlib import Path
import re
import sqlite3
from typing import Callable

from .app_config import ConfigurationError
from .autonomous_improvement import AutonomousImprovementStore
from .matchup_learning_store import MatchupLearningStore
from .offmatch_evidence import MatchEvidenceArchive
from .reasoning_config import reasoning_request_kwargs

SCHEMA = "champions-postmatch-multimatch-causal-v1"
GATE_FILENAME = "Pokemon_Champions_MC_live_decision_gate.txt"
MIN_MATCHES = 3
MAX_MATCHES = 6
MAX_EVENTS_PER_MATCH = 5
MAX_INPUT_CHARS = 23000
MAX_OUTPUT_CHARS = 16000
MAX_FINDINGS = 6
MAX_CALLS_PER_BATCH = 2  # one automatic try + at most one explicit retry
MAX_GATE_RULE_CHARS = 10500
MAX_GATE_SECTION_CHARS = 1400
CAUSES = ("TEAM", "SELECTION", "PLAY", "SUPPORT", "OCR_SYNC", "LUCK", "UNKNOWN")
OWNERS = ("TEAM", "USER", "SUPPORT", "RECOGNITION", "UNDETERMINED")
GATE_STATUSES = ("EXISTING_GATE_CANDIDATE", "UNRESOLVED", "UNKNOWN")
OPTION_STATUS = ("OBSERVED_ONLY", "UNVERIFIED_LEGALITY", "UNKNOWN")
RESEARCH_STATES = ("UNVERIFIED", "HOLD")
# Finite enums and exact immutable fields ensure no fabricated proof can be
# auto-translated into a new LIVE rule or a guaranteed matchup.
_FINDING = {
    "type": "object", "additionalProperties": False,
    "properties": {
        "match_id": {"type": "string"},
        "event_ordinal": {"type": "integer"},
        "cause": {"type": "string", "enum": list(CAUSES)},
        "owner": {"type": "string", "enum": list(OWNERS)},
        "opponent_control": {"type": "string", "enum": ["HUMAN", "CPU_SUSPECTED", "UNKNOWN"]},
        "observation": {"type": "string"},
        "issue": {"type": "string"},
        "gate_rule_id": {"type": "string"},
        "gate_coverage": {"type": "string", "enum": list(GATE_STATUSES)},
        "o_options": {"type": "string"},
        "x_conditions": {"type": "string"},
        "safe_choice": {"type": "string"},
        "expected_value_choice": {"type": "string"},
        "read_choice": {"type": "string"},
        "loss_route": {"type": "string"},
        "option_status": {"type": "string", "enum": list(OPTION_STATUS)},
        "uncertainty": {"type": "string"},
    },
    "required": [
        "match_id", "event_ordinal", "cause", "owner", "opponent_control",
        "observation", "issue", "gate_rule_id", "gate_coverage", "o_options",
        "x_conditions", "safe_choice", "expected_value_choice", "read_choice",
        "loss_route", "option_status", "uncertainty",
    ],
}
_RESPONSE = {
    "type": "object", "additionalProperties": False,
    "properties": {
        "schema": {"type": "string"},
        "research_sha": {"type": "string"},
        "findings": {"type": "array", "items": _FINDING},
        "summary": {"type": "string"},
    },
    "required": ["schema", "research_sha", "findings", "summary"],
}


def _json(value: object) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True,
                      separators=(",", ":"))


def _sha(value: object) -> str:
    return sha256(_json(value).encode("utf-8")).hexdigest()


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _small(value: object, bound: int = 180) -> str:
    if not isinstance(value, str):
        return ""
    return " ".join(value.split())[:bound]


def _gate_rules_with_audit(gate_text: str) -> tuple[dict[str, str], dict[str, str]]:
    """Extract full SHA-matched rules plus EVERY excluded section and reason.

    No section is silently lost before input-level budget selection. CORE is
    audited separately because its numbered rules are individually extracted.
    """
    sections: dict[str, list[str]] = {}
    rules: dict[str, str] = {}
    omitted: dict[str, str] = {}
    current = ""
    for raw in gate_text.splitlines():
        line = raw.strip()
        section = re.fullmatch(r"\[([A-Z][A-Z0-9_]{1,63})\]", line)
        if section:
            current = section.group(1)
            sections.setdefault(current, [])
            continue
        match = re.match(r"^(R\d{2})\s+(.+)$", line)
        if match:
            rules[match.group(1)] = _small(match.group(2), 200)
        if current and line and not line.startswith("#"):
            sections[current].append(line)
    ranked = sorted(sections, key=lambda sec: (
        0 if sec.endswith("_GATE") else
        1 if sec in ("LIVE_FORCE_ORDER", "LIVE_SPEED", "CORE",
                     "FAINT_ENTRY", "OUTPUT_SELF_CHECK") else 2, sec))
    used = len(_json(rules))
    for name in ranked:
        content = "\n".join(sections[name])
        if name == "CORE":
            omitted[name] = "CORE_SECTION_ONLY_NUMBERED_RULES_EXTRACTED"
        elif not content:
            omitted[name] = "EMPTY_SECTION"
        elif len(content) > MAX_GATE_SECTION_CHARS:
            omitted[name] = "SECTION_TOO_LONG"
        elif used + len(name) + len(content) > MAX_GATE_RULE_CHARS:
            omitted[name] = "EXTRACTION_BUDGET_EXCEEDED"
        else:
            rules[name] = content
            used += len(name) + len(content)
    return rules, omitted


def _frozen_gate_rules(gate_text: str) -> dict[str, str]:
    """Compatibility helper: extracted complete rules (audit via sibling)."""
    return _gate_rules_with_audit(gate_text)[0]


def _confirmed(event: dict) -> bool:
    execution = event.get("execution")
    return bool(isinstance(execution, dict) and
                execution.get("certainty") in ("USER_CONFIRMED", "GAME_EVENT_CONFIRMED") and
                isinstance(execution.get("action"), dict) and execution["action"])


class PostmatchCausalStore:
    """Research batches are local, immutable-on-success, and never LIVE-ready."""

    def __init__(self, data_root: str | Path):
        self.learning = MatchupLearningStore(data_root)
        self.p1 = MatchEvidenceArchive(data_root)
        self.p1_research = AutonomousImprovementStore(data_root)

    def _db(self) -> sqlite3.Connection:
        db = self.learning._db()
        db.executescript("""
        CREATE TABLE IF NOT EXISTS p2_causal_batches(
            research_sha TEXT PRIMARY KEY,
            team_id TEXT NOT NULL,
            team_signature TEXT NOT NULL,
            p1_batch_sha TEXT NOT NULL,
            evidence_sha TEXT NOT NULL,
            model TEXT NOT NULL,
            gate_sha TEXT NOT NULL,
            status TEXT NOT NULL,
            analysis_json TEXT NOT NULL,
            calls_used INTEGER NOT NULL,
            attempted_at TEXT NOT NULL,
            completed_at TEXT NOT NULL,
            FOREIGN KEY(p1_batch_sha) REFERENCES autonomous_improvement_batches(batch_sha)
        );
        CREATE INDEX IF NOT EXISTS ix_p2_team
          ON p2_causal_batches(team_id,team_signature,attempted_at);
        -- Legacy P2 rows may share a P1 batch but have different prompt
        -- digests. Never destroy old evidence to satisfy a UNIQUE index.
        CREATE INDEX IF NOT EXISTS ix_p2_history_batch
          ON p2_causal_batches(p1_batch_sha);
        CREATE TABLE IF NOT EXISTS p2_batch_budget(
          p1_batch_sha TEXT PRIMARY KEY,
          canonical_research_sha TEXT NOT NULL,
          calls_used INTEGER NOT NULL,
          status TEXT NOT NULL,
          migrated_legacy_rows INTEGER NOT NULL,
          created_at TEXT NOT NULL
        );
        """)
        # Reconstruct a safe canonical budget once; INSERT OR IGNORE
        # preserves subsequent reservations. The earliest legacy digest is
        # the audit referent, duplicates become terminal HOLD. Old P2 rows
        # remain intact and queryable, even if their total usage was >2.
        db.execute("BEGIN IMMEDIATE")
        try:
            historic = db.execute("""
                SELECT p1_batch_sha,research_sha,status,calls_used
                  FROM p2_causal_batches
                 ORDER BY attempted_at ASC,rowid ASC""").fetchall()
            groups: dict[str, list[tuple]] = {}
            for batch, research, status, calls in historic:
                groups.setdefault(batch, []).append((research,status,calls))
            for batch, entries in groups.items():
                used = min(MAX_CALLS_PER_BATCH, sum(
                    max(0, x[2]) if type(x[2]) is int else
                    MAX_CALLS_PER_BATCH for x in entries))
                status = ("LEGACY_DUPLICATE_HOLD" if len(entries) > 1
                          else entries[0][1])
                db.execute("""INSERT OR IGNORE INTO p2_batch_budget(
                     p1_batch_sha,canonical_research_sha,calls_used,status,
                     migrated_legacy_rows,created_at) VALUES(?,?,?,?,?,?)""",
                     (batch,entries[0][0],used,status,len(entries),_now()))
                if len(entries) > 1:
                    db.execute("""UPDATE p2_batch_budget
                           SET status='LEGACY_DUPLICATE_HOLD',
                               calls_used=MAX(calls_used,?),
                               migrated_legacy_rows=MAX(migrated_legacy_rows,?)
                         WHERE p1_batch_sha=?""", (used,len(entries),batch))
            db.commit()
        except Exception:
            db.rollback()
            db.close()
            raise
        return db

    def _prepare(self, *, match_id: str, gate_file: str | Path | None) -> dict:
        with closing(self._db()) as db:
            row = db.execute(
                "SELECT team_id,payload FROM match_history WHERE match_id=?",
                (match_id,)).fetchone()
            if not row:
                return {"status": "NO_FINISHED_MATCH"}
            team_id, raw = row
            try:
                snapshot = json.loads(raw)
                signature = snapshot.get("team_signature")
            except (ValueError, TypeError, AttributeError):
                signature = None
            if not team_id or not isinstance(signature, str) or not signature.strip():
                return {"status": "TEAM_SIGNATURE_UNKNOWN"}
            batches = self.p1_research.history(
                team_id=team_id, team_signature=signature, limit=1)
            if not batches or batches[0]["status"] != "PROVISIONAL_UNVERIFIED":
                return {"status": "NO_REPEATED_PATTERN"}
            p1_batch = batches[0]
            candidates = self.p1_research.candidates(batch_sha=p1_batch["batch_sha"])
            if not candidates:
                return {"status": "NO_REPEATED_PATTERN"}
            # P1 candidate list is ordered by its *hash*, not by recency.
            # Require the triggering match AND three independent matches new
            # to THIS batch to support the chosen issue. Otherwise an old
            # candidate can silently consume a newly earned model budget.
            if match_id not in p1_batch["match_ids"]:
                return {"status": "NOT_IN_RESEARCH_BATCH"}
            previous = self.p1_research.history(
                team_id=team_id, team_signature=signature, limit=2)
            old_ids = set(previous[1]["match_ids"]) if len(previous) > 1 else set()
            fresh_ids = set(p1_batch["match_ids"]) - old_ids
            relevant = []
            for candidate in candidates:
                evidence = set(candidate["evidence_match_ids"])
                new_evidence = evidence & fresh_ids
                if (match_id in new_evidence and
                        len(new_evidence) >= MIN_MATCHES):
                    relevant.append((candidate, new_evidence))
            if not relevant:
                return {"status": "NO_RELEVANT_NEW_PATTERN"}
            # Stable, auditable priority: more fresh evidence first. Old
            # evidence can give context but never replace the fresh minimum.
            relevant.sort(key=lambda pair: (
                -len(pair[1]), -len(pair[0]["evidence_match_ids"]),
                pair[0]["candidate_sha"]))
            candidate, new_evidence = relevant[0]
            selected = [match_id]
            selected.extend(mid for mid in sorted(new_evidence)
                            if mid != match_id)
            selected.extend(mid for mid in sorted(candidate["evidence_match_ids"])
                            if mid not in new_evidence)
            selected = selected[:MAX_MATCHES]

            snapshots = []
            for mid in selected:
                integrity = self.p1.verify(mid)
                if integrity.get("status") != "VALID":
                    return {"status": "P1_EVIDENCE_UNAVAILABLE", "match_id": mid,
                            "integrity": integrity.get("status", "UNKNOWN")}
                archived = self.p1.read(mid)
                if (not archived or archived["status"] != "CAPTURED_UNVERIFIED"
                        or archived["dropped_count"] != 0
                        or archived["team_id"] != team_id
                        or archived["team_sha"] != signature):
                    return {"status": "P1_EVIDENCE_INCOMPLETE", "match_id": mid}
                with closing(self.learning._db()) as check_db:
                    rec = check_db.execute(
                        "SELECT result FROM match_history WHERE match_id=?", (mid,)
                    ).fetchone()
                if not rec or rec[0] not in ("WIN", "LOSS"):
                    return {"status": "MATCH_RESULT_UNKNOWN", "match_id": mid}
                events = archived["events"]
                if not events:
                    return {"status": "NO_FROZEN_EVENTS", "match_id": mid}
                # Keep *chronological* first observations; never hand the
                # model later turns in a supposedly earlier knowledge window.
                compact = []
                for ordinal, event in enumerate(events[:MAX_EVENTS_PER_MATCH]):
                    if not isinstance(event, dict):
                        continue
                    compact.append({
                        "event_ordinal": ordinal,
                        "captured_at": event.get("captured_at"),
                        "phase": event.get("phase"),
                        "frame_id": event.get("frame_id"),
                        "turn_id": event.get("turn_id"),
                        "observation": event.get("observation"),
                        "selection": event.get("selection"),
                        "legal_options": event.get("legal_options"),
                        "proposal": event.get("proposal"),
                        "execution": event.get("execution"),
                        "opponent_control": event.get("opponent_control", "UNKNOWN"),
                        "field_candidate": event.get("field_candidate"),
                    })
                if not compact:
                    return {"status": "NO_FROZEN_EVENTS", "match_id": mid}
                context = archived["context"]
                snapshots.append({
                    "match_id": mid, "evidence_sha": archived["archive_sha"],
                    "team_sha": signature,
                    "tactical_version": context.get("tactical_version", ""),
                    "regulation": context.get("regulation", ""),
                    "regulation_source": context.get("regulation_source", ""),
                    "regulation_observed_at": context.get("regulation_observed_at", ""),
                    "environment_source": context.get("environment_source", ""),
                    "environment_observed_at": context.get("environment_observed_at", ""),
                    "frozen_gate_sha": context.get("frozen_assets_sha", {}).get("gate", ""),
                    "events": compact,
                    "result_after_match": rec[0],
                })
            # Refuse mixed regulations / tactical versions; P1 team SHA alone
            # is not enough to justify comparisons across environments.
            if len({(x["tactical_version"], x["regulation"],
                     x["environment_observed_at"]) for x in snapshots}) != 1:
                return {"status": "ENVIRONMENT_VERSION_MISMATCH"}
            freeze_sha = {x["frozen_gate_sha"] for x in snapshots}
            gate_text = ""
            gate_sha = ""
            if len(freeze_sha) == 1 and next(iter(freeze_sha)):
                try:
                    path = Path(gate_file) if gate_file else None
                    if path and path.is_file() and not path.is_symlink():
                        raw_gate = path.read_bytes()
                        digest = sha256(raw_gate).hexdigest()
                        if digest == next(iter(freeze_sha)):
                            gate_text = raw_gate.decode("utf-8-sig")
                            gate_sha = digest
                except (OSError, UnicodeError, ValueError):
                    pass
            gate_rules, gate_excluded = (
                _gate_rules_with_audit(gate_text) if gate_sha else ({}, {}))
            # A minimum of 3 full *capture-time* event windows is mandatory.
            # Reduce extra match windows BEFORE reducing any event fields.
            # Keep omitted match IDs and rule IDs as an auditable inventory;
            # never silently pretend truncated observations were examined.
            source = {
                "schema": SCHEMA, "team_id": team_id, "team_signature": signature,
                "p1_batch_sha": p1_batch["batch_sha"],
                "pattern": {
                    "candidate_sha": candidate["candidate_sha"],
                    "issue": _small(candidate["issue"]),
                    "opponent": _small(candidate["opponent"]),
                    "our_pokemon": _small(candidate["our_pokemon"]),
                    "certainty": "GPT_HYPOTHESIS_UNVERIFIED",
                },
                "sample_size": len(snapshots),
                "gate_sha": gate_sha or "UNKNOWN",
                "existing_gate_rules": {},
                "gate_omitted_rule_ids": sorted(gate_excluded),
                "gate_omission_reasons": dict(gate_excluded),
                "selection_audit": {
                    "eligible_match_ids": [m["match_id"] for m in snapshots],
                    "excluded_match_ids": [],
                    "reason": "FULL_EVIDENCE_OR_EXPLICIT_BUDGET_REDUCTION",
                },
                "matches": list(snapshots),
            }
            # Reserve a modest space for original mandatory safety rules,
            # but never drop below the 3 independently verified matches.
            min_gate_space = 3100 if gate_rules else 0
            while (len(source["matches"]) > MIN_MATCHES and
                   len(_json(source)) + min_gate_space > MAX_INPUT_CHARS):
                removed = source["matches"].pop()
                source["selection_audit"]["excluded_match_ids"].append(
                    removed["match_id"])
            source["sample_size"] = len(source["matches"])
            if len(_json(source)) > MAX_INPUT_CHARS:
                return {"status": "PROMPT_BUDGET_EXCEEDED",
                        "reason": "MINIMUM_THREE_FULL_EVENT_WINDOWS_TOO_LARGE"}
            # Add COMPLETE gate sections in safety-first order, never a
            # truncated pseudo-rule. Over-budget IDs remain explicit.
            for rule_id, body in gate_rules.items():
                source["existing_gate_rules"][rule_id] = body
                if len(_json(source)) > MAX_INPUT_CHARS:
                    del source["existing_gate_rules"][rule_id]
                    source["gate_omitted_rule_ids"].append(rule_id)
                    source["gate_omission_reasons"][rule_id] = "PROMPT_BUDGET_EXCEEDED"
            if len(_json(source)) > MAX_INPUT_CHARS:
                return {"status": "PROMPT_BUDGET_EXCEEDED",
                        "reason": "AUDIT_METADATA_TOO_LARGE"}
            return {"status": "READY", "source": source,
                    "research_sha": _sha(source), "team_id": team_id,
                    "team_signature": signature, "p1_batch_sha": p1_batch["batch_sha"],
                    "gate_sha": gate_sha}

    @staticmethod
    def _safe_response(response: dict, source: dict) -> dict:
        if (not isinstance(response, dict) or response.get("schema") != SCHEMA
                or response.get("research_sha") != _sha(source)
                or not isinstance(response.get("findings"), list)
                or len(response["findings"]) > MAX_FINDINGS):
            raise ConfigurationError("P2 structured response schema or evidence digest mismatch")
        match_map = {m["match_id"]: m for m in source["matches"]}
        rules = source["existing_gate_rules"]
        valid = []
        for finding in response["findings"]:
            if not isinstance(finding, dict) or set(finding) != set(_FINDING["properties"]):
                raise ConfigurationError("P2 finding shape invalid")
            mid, ordinal = finding["match_id"], finding["event_ordinal"]
            if (mid not in match_map or type(ordinal) is not int
                    or ordinal not in {e["event_ordinal"] for e in match_map[mid]["events"]}
                    or finding["cause"] not in CAUSES or finding["owner"] not in OWNERS
                    or finding["gate_coverage"] not in GATE_STATUSES
                    or finding["option_status"] not in OPTION_STATUS):
                raise ConfigurationError("P2 finding invents unavailable evidence")
            evt = next(e for e in match_map[mid]["events"]
                       if e["event_ordinal"] == ordinal)
            for field in ("observation", "issue", "gate_rule_id", "o_options",
                          "x_conditions", "safe_choice", "expected_value_choice",
                          "read_choice", "loss_route", "uncertainty"):
                if not isinstance(finding[field], str) or len(finding[field]) > 240:
                    raise ConfigurationError("P2 finding text exceeds bound")
            if finding["opponent_control"] not in ("HUMAN", "CPU_SUSPECTED", "UNKNOWN"):
                raise ConfigurationError("P2 unsupported opponent type")
            # Keep AI/OCR guess as UNKNOWN, even if model asserts HUMAN.
            opponent = (evt["opponent_control"]
                        if evt["opponent_control"] in ("HUMAN", "CPU_SUSPECTED")
                        else "UNKNOWN")
            rid = finding["gate_rule_id"]
            # All rule IDs must exist in an SHA-matched frozen version.
            rule_available = (bool(source["gate_sha"] != "UNKNOWN") and rid in rules)
            gate_status = ("EXISTING_GATE_EXECUTION_CANDIDATE"
                           if (finding["gate_coverage"] == "EXISTING_GATE_CANDIDATE"
                               and rule_available and _confirmed(evt)) else
                           "RULE_COVERAGE_UNKNOWN")
            # If the old gate already covered the case, it is NOT a new O/X
            # rule. Otherwise a plausible alternative remains HOLD, not proof
            # that the old gate failed. No numerical O/X weights.
            ox = ("EXISTING_RULE_EXECUTION_HYPOTHESIS" if
                  gate_status == "EXISTING_GATE_EXECUTION_CANDIDATE"
                  else "HOLD_GATE_OR_ACTION_UNVERIFIED")
            validated = {
                "match_id": mid, "event_ordinal": ordinal,
                "evidence_sha": match_map[mid]["evidence_sha"],
                "knowledge_cutoff": evt["captured_at"],
                "cause": finding["cause"],
                "owner": finding["owner"],
                "opponent_control": opponent,
                "observation": _small(finding["observation"]),
                "issue": _small(finding["issue"]),
                "gate_rule_id": rid if rule_available else "",
                "gate_status": gate_status,
                "o_options": _small(finding["o_options"]),
                "x_conditions": _small(finding["x_conditions"]),
                "options": {k: _small(finding[k]) for k in (
                    "safe_choice", "expected_value_choice", "read_choice", "loss_route")},
                "option_legality": "NOT_VERIFIED",
                "status": ox,
                "certainty": "GPT_HYPOTHESIS_UNVERIFIED",
                "uncertainty": _small(finding["uncertainty"]),
            }
            # Even confirmed actions alone do not establish whether an
            # alternative would have won; counterfactual claims remain HOLD.
            valid.append(validated)
        return {"schema": SCHEMA, "research_sha": _sha(source),
                "summary": _small(response.get("summary"), 360),
                "findings": valid, "sample_size": source["sample_size"],
                "source_match_ids": [m["match_id"] for m in source["matches"]],
                "gate_sha": source["gate_sha"], "status": "HOLD",
                "live_applied": False, "certainty": "UNVERIFIED"}

    def analyze_after_review(self, *, match_id: str, model: str,
                             access_token: str, gate_file: str | Path | None = None,
                             reasoning_effort: str = "auto", client_factory=None,
                             cancel: Callable[[], bool] | None = None,
                             allow_retry: bool = False) -> dict:
        """One automatic call per P1 batch; one *explicit* retry at most.

        Usage is unique by P1 batch SHA, never by the gate-dependent prompt
        digest. No automatic retry or research on incomplete evidence.
        """
        if cancel and cancel():
            return {"status": "CANCELLED", "calls_used": 0}
        prepared = self._prepare(match_id=match_id, gate_file=gate_file)
        if prepared["status"] != "READY":
            return {"status": prepared["status"], "calls_used": 0}
        digest = prepared["research_sha"]
        source = prepared["source"]
        if not model or not access_token:
            return {"status": "MODEL_OR_AUTH_UNAVAILABLE", "calls_used": 0}
        attempt_number = 1
        with closing(self._db()) as db:
            # Atomic reservation avoids two workers racing to spend the same
            # batch's budget. Retry requires an explicit caller opt-in.
            db.execute("BEGIN IMMEDIATE")
            existing = db.execute("""
                SELECT canonical_research_sha,status,calls_used
                  FROM p2_batch_budget WHERE p1_batch_sha=?""",
                (prepared["p1_batch_sha"],)).fetchone()
            if existing:
                stored_sha, status, used = existing
                if status == "LEGACY_DUPLICATE_HOLD":
                    db.rollback()
                    return {"status":"LEGACY_DUPLICATE_HOLD",
                            "research_sha":stored_sha,"calls_used":used,
                            "created":False}
                # Historical knowledge, gate file and chosen prompt can
                # change without creating a second automatic API budget.
                # Even explicit retry must use the SAME frozen source, not
                # silently substitute new evidence under an old P1 batch.
                if stored_sha != digest:
                    db.rollback()
                    return {"status": "SOURCE_CHANGED_HOLD",
                            "research_sha": stored_sha, "calls_used": used,
                            "created": False}
                if status == "STORED_HOLD_UNVERIFIED" or not allow_retry:
                    db.rollback()
                    return {"status": status, "research_sha": stored_sha,
                            "calls_used": used, "created": False}
                if status == "RESERVED_UNVERIFIED" or used >= MAX_CALLS_PER_BATCH:
                    db.rollback()
                    return {"status": "BUDGET_EXHAUSTED", "research_sha": stored_sha,
                            "calls_used": used}
                attempt_number = used + 1
                db.execute("""UPDATE p2_causal_batches
                                  SET status='RESERVED_UNVERIFIED',
                                      calls_used=?,attempted_at=?,completed_at=''
                                WHERE p1_batch_sha=? AND research_sha=?""",
                           (attempt_number,_now(),prepared["p1_batch_sha"],stored_sha))
                db.execute("""UPDATE p2_batch_budget SET
                          status='RESERVED_UNVERIFIED',calls_used=?
                          WHERE p1_batch_sha=?""",
                           (attempt_number,prepared["p1_batch_sha"]))
            else:
                db.execute("""INSERT INTO p2_causal_batches(
                    research_sha,team_id,team_signature,p1_batch_sha,
                    evidence_sha,model,gate_sha,status,analysis_json,
                    calls_used,attempted_at,completed_at)
                    VALUES(?,?,?,?,?,?,?,?,?,?,?,?)""",
                    (digest,prepared["team_id"],prepared["team_signature"],
                     prepared["p1_batch_sha"],_sha(source["matches"]),model,
                     source["gate_sha"],"RESERVED_UNVERIFIED","{}",1,_now(),""))
                db.execute("""INSERT INTO p2_batch_budget(
                     p1_batch_sha,canonical_research_sha,calls_used,status,
                     migrated_legacy_rows,created_at) VALUES(?,?,?,?,?,?)""",
                     (prepared["p1_batch_sha"],digest,1,
                      "RESERVED_UNVERIFIED",0,_now()))
            db.commit()
        if cancel and cancel():
            # No network call was dispatched. Close the reservation AND
            # refund its call slot; explicit retry remains possible while
            # ordinary repeat callbacks cannot silently restart research.
            with closing(self._db()) as db:
                with db:
                    db.execute("""UPDATE p2_causal_batches
                        SET status='CANCELLED_HOLD', calls_used=?,
                            completed_at=?
                        WHERE research_sha=? AND status='RESERVED_UNVERIFIED'
                          AND calls_used=?""",
                        (attempt_number - 1, _now(), digest, attempt_number))
                    db.execute("""UPDATE p2_batch_budget
                        SET status='CANCELLED_HOLD',calls_used=?
                        WHERE p1_batch_sha=? AND canonical_research_sha=?
                          AND status='RESERVED_UNVERIFIED'""",
                        (attempt_number - 1,prepared["p1_batch_sha"],digest))
            return {"status": "CANCELLED_HOLD", "research_sha": digest,
                    "calls_used": attempt_number - 1, "dispatched": False}
        instructions = (
            "You are analyzing FINISHED Pokémon Champions singles battles, "
            "never advising during live play. The supplied capture snapshots "
            "are frozen at the listed knowledge_cutoff and contain AI/OCR "
            "candidates, not independently established facts. Do not use "
            "later events or final results to invent earlier observations, "
            "damage, opponent sets, move legality, action execution or speed. "
            "Each finding must cite a provided match_id/event_ordinal. "
            "Separate TEAM/SELECTION/PLAY/SUPPORT/OCR_SYNC/LUCK/UNKNOWN and "
            "user/support/recognition ownership. Never infer support error "
            "merely because the AI recommendation differed from an unknown "
            "executed action. First check existing_gate_rules: name only an "
            "existing rule identifier genuinely applicable on observed facts; "
            "if no exact gate or sufficient execution proof, gate_coverage=UNKNOWN. "
            "CODEX O means options and constraints (choice lock, safe landing, "
            "preserved win routes); X means repeatable execution conditions, "
            "not a numeric score. Compare safe, expected-value, read-dependent "
            "and loss-creating choices ONLY as explicitly unverified hypotheses. "
            "Never invent legality or claimed improvement. If facts are "
            "insufficient, emit empty findings. Respond Japanese, strictly JSON."
        )
        try:
            if client_factory is None:
                from openai import OpenAI
                client_factory = OpenAI
            client = client_factory(
                api_key=access_token, base_url="https://api.openai.com/v1",
                timeout=45, max_retries=0)
            stream = client.responses.create(
                model=model, store=False, stream=True,
                max_output_tokens=3500,
                **reasoning_request_kwargs(reasoning_effort),
                instructions=instructions,
                input=[{"role": "user", "content": [{
                    "type": "input_text",
                    "text": _json({"research_sha":digest, "source":source}),
                }]}],
                text={"format":{"type":"json_schema","name":"champions_p2_causal",
                                "strict":True,"schema":_RESPONSE}})
            parts = []
            ended = False
            if not hasattr(stream, "__enter__"):
                raise ConfigurationError("P2 streaming response unavailable")
            with stream:
                for event in stream:
                    if cancel and cancel():
                        raise InterruptedError("P2 cancelled after match boundary")
                    if event.type == "response.output_text.delta":
                        parts.append(str(event.delta))
                        if sum(map(len, parts)) > MAX_OUTPUT_CHARS:
                            raise ConfigurationError("P2 output budget exceeded")
                    elif event.type == "response.completed":
                        ended = True
                    elif event.type in ("response.failed", "response.incomplete", "error"):
                        raise ConfigurationError("P2 model response incomplete")
            if not ended or not parts:
                raise ConfigurationError("P2 response has no completed payload")
            response = json.loads("".join(parts))
            packet = self._safe_response(response, source)
            with closing(self._db()) as db:
                with db:
                    db.execute("""UPDATE p2_causal_batches
                            SET status='STORED_HOLD_UNVERIFIED', analysis_json=?,
                                completed_at=?
                            WHERE research_sha=? AND status='RESERVED_UNVERIFIED'""",
                            (_json(packet),_now(),digest))
                    db.execute("""UPDATE p2_batch_budget
                        SET status='STORED_HOLD_UNVERIFIED'
                        WHERE p1_batch_sha=? AND canonical_research_sha=?
                          AND status='RESERVED_UNVERIFIED'""",
                        (prepared["p1_batch_sha"],digest))
            return {"status":"STORED_HOLD_UNVERIFIED",
                    "research_sha":digest, "findings":len(packet["findings"]),
                    "sample_size":packet["sample_size"], "calls_used":attempt_number,
                    "live_applied":False}
        except Exception as exc:
            # Reservation is intentional: an interrupted or failed charge
            # never loops silently. Original P1/review rows remain intact.
            with closing(self._db()) as db:
                with db:
                    db.execute("""UPDATE p2_causal_batches
                            SET status=?,completed_at=?
                            WHERE research_sha=? AND status='RESERVED_UNVERIFIED'""",
                            ("CANCELLED_HOLD" if isinstance(exc,InterruptedError)
                             else "FAILED_HOLD",_now(),digest))
                    db.execute("""UPDATE p2_batch_budget SET status=?
                        WHERE p1_batch_sha=? AND canonical_research_sha=?
                          AND status='RESERVED_UNVERIFIED'""",
                        ("CANCELLED_HOLD" if isinstance(exc,InterruptedError)
                         else "FAILED_HOLD",prepared["p1_batch_sha"],digest))
            return {"status": ("CANCELLED_HOLD" if isinstance(exc,InterruptedError)
                               else "FAILED_HOLD"), "research_sha":digest,
                    "reason":type(exc).__name__, "calls_used":attempt_number}

    def read(self, research_sha: str) -> dict | None:
        with closing(self._db()) as db:
            row = db.execute("""SELECT status,analysis_json,calls_used,model,
                                       gate_sha,team_signature
                                  FROM p2_causal_batches WHERE research_sha=?""",
                             (research_sha,)).fetchone()
        if row is None:
            return None
        return {"status":row[0],"analysis":json.loads(row[1]),
                "calls_used":row[2],"model":row[3],"gate_sha":row[4],
                "team_signature":row[5]}
