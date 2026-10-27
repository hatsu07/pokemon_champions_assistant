"""Evidence-preserving local battle-event timeline for the live replay.

Unverified OCR is *never* applied to battle_evidence as if an event had been
observed by a trusted visual decision. A conflicting value is explicitly
marked CONFLICT instead of silently replacing the previous observation.
"""
from __future__ import annotations

from collections import deque
from copy import deepcopy

from .local_event_worker import LocalFieldRead


class LocalBattleStateTracker:
    def __init__(self, max_events: int=36):
        if max_events<1:
            raise ValueError("max_events must be positive")
        self._events: deque[LocalFieldRead]=deque(maxlen=max_events)
        self._latest: dict[str,LocalFieldRead]={}
        self.duplicates=0
        self.conflicts=0

    def ingest(self, records: tuple[LocalFieldRead, ...]) -> int:
        inserted=0
        for row in records:
            if row.status!="CANDIDATE":
                continue
            # Identical text kept across consecutive frames is one event,
            # but a later recurrence is allowed after a different text.
            previous=self._latest.get(row.field)
            if previous and previous.text==row.text and previous.value==row.value:
                self.duplicates+=1
                continue
            status="CANDIDATE"
            if (previous and previous.frame_id==row.frame_id
                    and previous.value!=row.value):
                status="CONFLICT"
                self.conflicts+=1
            if status!=row.status:
                from dataclasses import replace
                row=replace(row,status=status)
            self._events.append(row)
            self._latest[row.field]=row
            inserted+=1
        return inserted

    def snapshot(self, *, at_or_before: float | None=None,
                 limit: int=12, own_active: str | None=None,
                 opponent_active: str | None=None,
                 own_names: tuple[str, ...]=(),
                 opponent_names: tuple[str, ...]=()) -> dict:
        """Associate OCR hints with known actors without certifying them.

        HP overlays belong to the active Pokémon previously verified by
        vision. A candidate does NOT prove that the actor is still active.
        """
        from .terminology import japanese_name
        valid=[r for r in self._events
               if at_or_before is None or r.timestamp<=at_or_before]
        payload=[]
        for row in valid[-limit:]:
            item=row.as_dict()
            actor,side=None,None
            if row.field=="screen_text.own_hp_fraction":
                actor,side=own_active,"own"
            elif row.field=="screen_text.opponent_hp_percent":
                actor,side=opponent_active,"opponent"
            else:
                matches=[(name,"own") for name in own_names
                         if name in row.detected_pokemon]
                matches += [(name,"opponent") for name in opponent_names
                            if name in row.detected_pokemon]
                if len(matches)==1:
                    actor,side=matches[0]
            if actor:
                item["species_candidate"]=japanese_name(
                    "pokemon",str(actor).split("→",1)[0].strip())
                item["candidate_side"]=side
                item["species_source"]="LAST_VERIFIED_ACTIVE_OR_OCR_NAME_UNVERIFIED"
            payload.append(item)
        return {
            "certainty":"UNVERIFIED_LOCAL_ROI_OCR",
            "local_observations":deepcopy(payload),
            "candidate_count":len(valid),
            "conflict_count":sum(r.status=="CONFLICT" for r in valid),
            "no_confirmed_game_mechanics":True,
        }

    def clear(self) -> None:
        self._events.clear()
        self._latest.clear()
        self.duplicates=0
        self.conflicts=0
