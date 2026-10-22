#!/usr/bin/env python3
"""Local v2.3 contract/state reconciliation reference, NOT a game detector or strategic AI.

No network calls. This module never chooses Pokémon actions.
"""
from __future__ import annotations
from dataclasses import dataclass
from collections import defaultdict
from copy import deepcopy
import hashlib
import json
from pathlib import Path
from typing import Any

PROTOCOL_VERSION = '2.3.0'
SCHEMA_DIR = Path(__file__).resolve().parent
CONFIRMATION_FIELD = 'confirmation_required'
FIELD_PRIORITY = {'RULE_DERIVED':1, 'BATTLE_TEXT':2, 'UI_DIRECT':3, 'MANUAL_VERIFIED':4}


def canonical_json(obj: Any) -> str:
    return json.dumps(obj, ensure_ascii=False, sort_keys=True, separators=(',', ':'), allow_nan=False)


def sha256_json(obj: Any) -> str:
    return 'sha256:' + hashlib.sha256(canonical_json(obj).encode('utf-8')).hexdigest()


def allowed_action_digest(phase: str, allowed: dict[str, Any]) -> str:
    """Hash exactly the evidence-backed action set; no strategic ranking.

    Rules: sort set-valued candidate lists to ignore display order, reject duplicate labels.
    In production, stable Pokémon identity/form slots should replace ambiguous names.
    """
    keys = ('moves', 'pivot_moves', 'switch_targets', 'team_candidates')
    canon: dict[str, Any] = {
        'protocol_version': PROTOCOL_VERSION,
        'phase': phase,
        'completeness': allowed['completeness'],
        'mega_available': allowed['mega_available'],
        'choice_lock': allowed['choice_lock'],
        'active_form': allowed['active_form'],
    }
    for key in keys:
        options = allowed[key]
        if len(options) != len(set(options)):
            raise ValueError(f'duplicate legal-choice identifier in {key}')
        canon[key] = sorted(options)
    if any(x not in allowed['moves'] for x in allowed['pivot_moves']):
        raise ValueError('pivot_moves must be a subset of moves')
    return sha256_json(canon)


def verify_local_schema(instance: dict, kind: str) -> None:
    """Local jsonschema validation; semantic constraints are enforced separately."""
    from jsonschema import Draft202012Validator, validate
    filename = (
        'Pokemon_Champions_API_request_schema_v2_3.json' if kind == 'request' else
        'Pokemon_Champions_effect_rule_schema_v0_2.json' if kind == 'effect' else
        f'Pokemon_Champions_API_response_{kind.lower()}_schema_v2_3.json'
    )
    schema = json.loads((SCHEMA_DIR / filename).read_text(encoding='utf-8'))
    Draft202012Validator.check_schema(schema)
    validate(instance, schema)


@dataclass(frozen=True)
class Evidence:
    evidence_id: str
    seq: int
    field: str
    value: Any
    source_kind: str  # MANUAL_VERIFIED / UI_DIRECT / BATTLE_TEXT / RULE_DERIVED
    quality: str  # CONFIRMED / CANDIDATE
    supersedes: tuple[str,...] = ()
    effect_instance_id: str | None = None


class EvidenceLedger:
    """Append-only ledger + deterministic reconciliation. Does not mutate sent snapshots."""
    def __init__(self) -> None:
        self.events: list[Evidence] = []
        self.ids: set[str] = set()
        self.observation_revision = 0
        self.tactical_state_version = 0
        self._projection: dict[str,dict] = {}
        self.snapshots_sent: list[dict] = []
        self.effect_ids_committed: set[str] = set()
        self.pending_event_ids: set[str] = set()

    def append(self, item: Evidence) -> bool:
        if item.evidence_id in self.ids:
            return False
        if item.source_kind not in FIELD_PRIORITY or item.quality not in ('CONFIRMED','CANDIDATE'):
            raise ValueError('invalid evidence category')
        if item.seq <= 0:
            raise ValueError('seq must be positive')
        if item.supersedes and any(x not in self.ids for x in item.supersedes):
            raise ValueError('cannot supersede missing evidence')
        before = deepcopy(self._projection)
        self.events.append(item)
        self.ids.add(item.evidence_id)
        self.observation_revision += 1
        self._projection = self.replay()
        # A change from CONFIRMED to UNKNOWN/CONFLICT is tactical too.
        if before != self._projection:
            self.tactical_state_version += 1
        return True

    def replay(self) -> dict[str,dict]:
        voided = {v for e in self.events for v in e.supersedes}
        by_field: dict[str,list[Evidence]] = defaultdict(list)
        for e in self.events:
            if e.evidence_id not in voided:
                by_field[e.field].append(e)
        projection: dict[str,dict] = {}
        for field, entries in by_field.items():
            confirmed = [e for e in entries if e.quality == 'CONFIRMED']
            if not confirmed:
                projection[field] = {'value':None,'certainty':'CANDIDATE',
                                      'candidates':[e.value for e in entries],
                                      'evidence_ids':[e.evidence_id for e in entries],
                                      'disagreement':False}
                continue
            highest = max(FIELD_PRIORITY[e.source_kind] for e in confirmed)
            winners = [e for e in confirmed if FIELD_PRIORITY[e.source_kind] == highest]
            vals = {canonical_json(e.value) for e in winners}
            allvals = {canonical_json(e.value) for e in confirmed}
            disagree = len(allvals)>1
            if len(vals)>1:
                projection[field] = {'value':None,'certainty':'CONFLICT',
                                      'candidates':[e.value for e in winners],
                                      'evidence_ids':[e.evidence_id for e in entries],
                                      'disagreement':True}
            else:
                winner = max(winners,key=lambda e:e.seq)
                projection[field] = {'value':winner.value,'certainty':'CONFIRMED',
                                      'candidates':[],
                                      'evidence_ids':[e.evidence_id for e in entries],
                                      'disagreement':disagree}
        return projection

    def mark_pending_event(self, event_id: str) -> bool:
        """Unresolved material event invalidates a previously frozen tactical snapshot."""
        if event_id in self.pending_event_ids:
            return False
        self.pending_event_ids.add(event_id)
        self.observation_revision += 1
        self.tactical_state_version += 1
        return True

    def resolve_pending_event(self, event_id: str) -> bool:
        if event_id not in self.pending_event_ids:
            return False
        self.pending_event_ids.remove(event_id)
        self.observation_revision += 1
        self.tactical_state_version += 1
        return True

    def current_state(self) -> dict[str,dict]:
        return deepcopy(self._projection)

    def freeze_snapshot(self, payload: dict) -> dict:
        frozen = deepcopy(payload)
        self.snapshots_sent.append(frozen)
        return frozen

    def commit_effect_once(self, effect_instance_id: str, confirmed: bool) -> bool:
        """Idempotent effect confirmation marker, not a mechanics calculator.

        Real implementation must transact this marker with append-only event and
        the reducer state update in one SQLite transaction.
        """
        if not confirmed or effect_instance_id in self.effect_ids_committed:
            return False
        self.effect_ids_committed.add(effect_instance_id)
        return True


def assess_snapshot(phase: str, *, window_confirmed: bool, action_completeness: str,
                    own_team_ready: bool, own_active_known: bool,
                    unprocessed_event_ids: list[str], critical_unknowns: list[str],
                    deadline_pressure: bool) -> tuple[str, list[str]]:
    """Prepare a snapshot, not a strategy. Deadline pressure never bypasses critical gates."""
    critical = list(critical_unknowns)
    if not window_confirmed:
        critical.append('decision_window_not_confirmed')
    if phase != 'REVIEW' and action_completeness != 'COMPLETE':
        critical.append('legal_choices_incomplete')
    if not own_team_ready:
        critical.append('own_team_not_verified')
    if phase == 'NORMAL_COMMAND' and not own_active_known:
        critical.append('our_active_unknown')
    if critical:
        return 'BLOCKED', sorted(set(critical))
    if unprocessed_event_ids:
        return ('READY_WITH_UNCERTAINTY' if deadline_pressure else 'WAIT_FOR_SYNC'), list(unprocessed_event_ids)
    return 'READY_CONFIRMED', []


def response_valid_for_window(request: dict, response: dict, *, actual_current: dict) -> tuple[bool,str]:
    """Validate phase/schema/identity/legality/freshness; no strategic fallback."""
    phase = request['phase']
    kind = 'battle' if phase in ('NORMAL_COMMAND','PIVOT_TARGET','FAINT_REPLACEMENT','EFFECT_REPLACEMENT') else phase.lower()
    try:
        verify_local_schema(response,kind)
    except Exception:
        return False,'RESPONSE_SCHEMA_INVALID'
    if request['protocol_version'] != PROTOCOL_VERSION:
        return False,'REQUEST_PROTOCOL_MISMATCH'
    keys = ('protocol_version','request_id','match_id','decision_window_id','tactical_state_version','allowed_action_digest')
    if any(request[k] != response[k] for k in keys):
        return False,'REQUEST_RESPONSE_MISMATCH'
    if any(request[k] != actual_current[k] for k in ('match_id','decision_window_id','tactical_state_version','allowed_action_digest')):
        return False,'STALE_DECISION'
    if response['decision_status'] == 'NO_DECISION':
        return False,'NO_DECISION'
    acts = request['allowed_actions']
    if kind == 'battle':
        ak = response['action_kind']
        if ak == 'MOVE':
            if phase != 'NORMAL_COMMAND' or response['move'] not in acts['moves'] or response['switch_target'] is not None:
                return False,'ILLEGAL_MOVE'
            is_pivot = response['move'] in acts['pivot_moves']
            if is_pivot and acts['switch_targets'] and response['planned_pivot_target'] is None:
                return False,'PIVOT_TARGET_MISSING'
            if not is_pivot and response['planned_pivot_target'] is not None:
                return False,'UNEXPECTED_PIVOT_TARGET'
        elif ak == 'SWITCH':
            if response['switch_target'] not in acts['switch_targets'] or response['move'] is not None or response['mega'] or response['planned_pivot_target'] is not None:
                return False,'ILLEGAL_SWITCH'
        else:
            return False,'UNSUPPORTED_ACTION_KIND'
        if response['mega'] and not acts['mega_available']:
            return False,'ILLEGAL_MEGA'
        if response['planned_pivot_target'] is not None and response['planned_pivot_target'] not in acts['switch_targets']:
            return False,'ILLEGAL_PIVOT_TARGET'
        if response['keep_back'] is not None:
            if phase != 'FAINT_REPLACEMENT' or response['keep_back'] not in request['battle_state']['our_remaining'] or response['keep_back'] == response['switch_target']:
                return False,'INVALID_KEEP_BACK'
    elif kind == 'select':
        selected = response['selected_three']
        if len(selected)!=3 or len(set(selected))!=3 or any(p not in acts['team_candidates'] for p in selected) or response['lead'] not in selected:
            return False,'ILLEGAL_TEAM_SELECTION'
    elif kind == 'prep':
        if response['prep_status'] != 'COMPLETE':
            return False,'PREP_INCOMPLETE'
        pairs={(a['our_pokemon'],a['opponent_pokemon']) for a in response['matchups_18']}
        expected={(own,opp) for own in request['context']['selected_three'] for opp in request['context']['opponent_preview_six']}
        if len(response['matchups_18'])!=18 or pairs != expected or not all(response[k] for k in ('preserve','stopper','closer')):
            return False,'PREP_MISSING_MATCHUPS_OR_WIN3'
        # Minimize PREP output latency. Each matchup returns a 1-line compact code.
        # These tags represent the requirements of the existing LIVE 40-second PREP.
        lead = request['context']['lead']
        lead_tags = ('ACT','KO','DPS','PRI','S','OUR','OPP','SAFE','PROC','LOCK','MEGA')
        bench_tags = ('S','OUR','OPP','PRI','SAFE','PROC')
        for matchup in response['matchups_18']:
            parsed = {}
            for part in matchup['eval_code'].split('|'):
                k, sep, value = part.partition('=')
                if not sep or not k or not value or k in parsed:
                    return False,'PREP_EVAL_CODE_INVALID'
                parsed[k] = value
            required = lead_tags if matchup['our_pokemon'] == lead else bench_tags
            if any(k not in parsed for k in required):
                return False,'PREP_LEAD_SIX_INCOMPLETE' if matchup['our_pokemon']==lead else 'PREP_BENCH_TWELVE_INCOMPLETE'
            if parsed.get('PROC') in ('?', '未判定', 'SKIPPED'):
                return False,'PREP_PROCESSOR_NOT_ASSESSED'
            if matchup['our_pokemon']==lead and any(parsed.get(k) in ('?', '未判定','SKIPPED') for k in ('ACT','DPS')):
                return False,'PREP_FIRST_ACTION_NOT_ASSESSED'
    return True,'OK'

FOUR_REQUIRED_MASTERS = frozenset({
    'Pokemon_Champions_MC_live_decision_gate.txt',
    'Pokemon_Champions_MC_live_selection_matrix.txt',
    'Pokemon_Champions_MC_60sec_lookup_master.txt',
    'Pokemon_Champions_MC_team_2026-10-03.csv',
})


def validate_wire_input(payload: dict) -> tuple[bool,str]:
    """Local hard gate. Incomplete data MUST NOT be filled in by guessed Pokémon facts."""
    try:
        verify_local_schema(payload,'request')
    except Exception as exc:
        return False,f'REQUEST_SCHEMA_INVALID: {type(exc).__name__}'
    if payload['allowed_action_digest'] != allowed_action_digest(payload['phase'],payload['allowed_actions']):
        return False,'ACTION_DIGEST_MISMATCH'
    if payload['snapshot_quality']['readiness'] == 'BLOCKED':
        return False,'SNAPSHOT_BLOCKED'
    if payload['snapshot_quality']['critical_unknowns']:
        return False,'CRITICAL_UNKNOWN'
    if payload['allowed_actions']['completeness']!='COMPLETE' and payload['phase']!='REVIEW':
        return False,'LEGAL_ACTIONS_NOT_CONFIRMED'
    manifest={r['name']:r['sha256'] for r in payload['source_manifest']['files']}
    if not FOUR_REQUIRED_MASTERS.issubset(manifest):
        return False,'FOUR_FILES_NOT_PRESENT_IN_MANIFEST'
    if any(len(x) != 64 or any(c not in '0123456789abcdef' for c in x) for x in manifest.values()):
        return False,'INVALID_MANIFEST_SHA256'
    context=payload['context']; phase=payload['phase']
    if phase == 'SELECT':
        if len(context['own_team'])!=6 or len(context['opponent_preview_six'])!=6 or len(context['live_matrix_rows_six'])!=6:
            return False,'SELECT_MISSING_SIX_ROWS'
        if any(len(member['moves'])!=4 for member in context['own_team']):
            return False,'SELECT_FOUR_MOVES_MISSING'
    elif phase not in ('REVIEW',):
        if len(context['selected_three']) != 3 or len(context['opponent_preview_six']) != 6:
            return False,'MISSING_SELECTED_THREE_OR_OPPONENT_SIX'
        if context['lead'] not in context['selected_three']:
            return False,'LEAD_NOT_IN_SELECTED_THREE'
        if len(context['own_team']) < 3:
            return False,'OWN_TEAM_NOT_PROVIDED'
        by_name={r['name']:r for r in context['own_team']}
        for p in context['selected_three']:
            if p not in by_name or len(by_name[p]['moves'])!=4:
                return False,'OWN_SELECTED_FOUR_MOVES_MISSING'
    if phase in ('NORMAL_COMMAND','PIVOT_TARGET','FAINT_REPLACEMENT','EFFECT_REPLACEMENT'):
        if len(context['live_matrix_rows_six'])!=6:
            return False,'LIVE_MATRIX_ROWS_MISSING'
        if not payload['battle_state']['our_remaining']:
            return False,'OUR_REMAINING_MISSING'
        if phase=='NORMAL_COMMAND' and not payload['battle_state']['our_active']:
            return False,'OWN_ACTIVE_NOT_IDENTIFIED'
    return True,'OK'
