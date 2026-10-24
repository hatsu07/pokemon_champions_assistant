"""Offline-safe bridge from frames to append-only observations and the Phase 2 FSM.

This module deliberately has no model adapter dependency.  A caller may run it
while an API request is outstanding; a material observed state update advances
the session version and thereby revokes that old response's display entitlement.
"""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass
from time import perf_counter
from typing import Any, Mapping, Protocol

from .battle_visual_fields import HpBarCalibration, IconCalibration, VisualCandidateStabilizer, estimate_hp_bar, recognize_icon
from .capture import Frame
from .effect_catalog import FrozenEffectCatalog
from .fsm import MatchSession, ObservationEvent, VisualUI
from .observation_events import EventCertainty, MessageEpisodeTracker, ObservedEvent, ObservedEventKind, ParsedOcr, ScreenLexicon, TextObservationParser
from .ocr import OcrEngine, image_digest
from .roi import RoiConfiguration
from .store import BattleStore
from .vision import DetectedUI, RoiChangeDetector, TemplateUiClassifier, UiClassification, UiStabilityGate
from .wire import AllowedActions


class CandidateExtractor(Protocol):
    """Return only explicitly recognized legal choices; never select tactics."""

    def extract(self, frame: Frame, visual_ui: DetectedUI, parsed_rois: Mapping[str, ParsedOcr]) -> AllowedActions | None: ...


@dataclass(frozen=True)
class CandidateTextProfile:
    """Calibration profile whose tokens must all appear before choices are complete."""

    visual_ui: DetectedUI
    moves: tuple[str, ...] = ()
    switch_targets: tuple[str, ...] = ()
    team_candidates: tuple[str, ...] = ()
    pivot_moves: tuple[str, ...] = ()
    mega_available: bool = False
    required_tokens: tuple[str, ...] = ()


class ExactTextCandidateExtractor:
    """Strict, calibration-supplied legal-candidate extractor.

    It returns `UNKNOWN` rather than inventing a missing option.  Profiles are
    configuration/test inputs, not a Champions coordinate or rules database.
    """

    def __init__(self, profiles: tuple[CandidateTextProfile, ...]) -> None:
        self._profiles = {profile.visual_ui: profile for profile in profiles}

    def extract(self, frame: Frame, visual_ui: DetectedUI, parsed_rois: Mapping[str, ParsedOcr]) -> AllowedActions | None:
        profile = self._profiles.get(visual_ui)
        if profile is None:
            return None
        text = "\n".join(
            parsed.text for parsed in parsed_rois.values()
        )
        expected = (*profile.required_tokens, *profile.moves, *profile.switch_targets, *profile.team_candidates)
        complete = bool(expected) and all(token in text for token in expected)
        return AllowedActions(
            completeness="COMPLETE" if complete else "UNKNOWN",
            moves=profile.moves if complete else (),
            pivot_moves=profile.pivot_moves if complete else (),
            switch_targets=profile.switch_targets if complete else (),
            team_candidates=profile.team_candidates if complete else (),
            mega_available=profile.mega_available if complete else False,
        )


@dataclass(frozen=True)
class SyncWatermark:
    capture_seq: int
    observed_event_seq: int
    processed_event_seq: int

    @property
    def is_synced(self) -> bool:
        return self.processed_event_seq >= self.observed_event_seq


@dataclass(frozen=True)
class FrameObservation:
    frame_id: str
    raw_observation_id: str
    classification: UiClassification
    stable_visual_ui: DetectedUI | None
    events: tuple[ObservedEvent, ...]
    watermark: SyncWatermark
    processing_ms: float
    ocr_text: Mapping[str, str]


_FSM_UI = {
    DetectedUI.TEAM_SELECTION: VisualUI.TEAM_SELECTION_UI,
    DetectedUI.COMMAND_MENU: VisualUI.COMMAND_MENU,
    DetectedUI.MOVE_MENU: VisualUI.MOVE_MENU,
    DetectedUI.PARTY_MENU: VisualUI.PARTY_MENU,
    DetectedUI.DETAIL_OVERLAY: VisualUI.DETAIL_OVERLAY,
    DetectedUI.ACTION_RESOLUTION: VisualUI.ACTION_RESOLUTION,
    DetectedUI.RESULT_SCREEN: VisualUI.RESULT_SCREEN,
    DetectedUI.UNKNOWN: VisualUI.UNKNOWN,
}


class ObserverPipeline:
    """Process one frame at a time without waiting for model/API work."""

    def __init__(
        self,
        *,
        session: MatchSession,
        store: BattleStore,
        roi_config: RoiConfiguration,
        ui_classifier: TemplateUiClassifier,
        ocr_engine: OcrEngine,
        text_parser: TextObservationParser,
        candidate_extractor: CandidateExtractor | None = None,
        effect_catalog: FrozenEffectCatalog | None = None,
        ocr_roi_names: tuple[str, ...] = (
            "ROI_BATTLE_LOG",
            "ROI_STATUS",
            "ROI_UI_CONTROLS",
            "ROI_MOVE_CARDS",
            "ROI_PARTY_CARDS",
            "ROI_OPPONENT_TEAM",
            "ROI_RESULT",
        ),
        classification_roi_name: str | None = None,
        change_threshold: float = 2.0,
        stable_frames: int = 2,
        recent_frame_limit: int = 120,
        hp_calibrations: tuple[HpBarCalibration, ...] = (),
        icon_calibrations: tuple[IconCalibration, ...] = (),
    ) -> None:
        if session.store is not store:
            raise ValueError("observer pipeline and session must use the same BattleStore")
        if recent_frame_limit < 1:
            raise ValueError("recent_frame_limit must be positive")
        self.session = session
        self.store = store
        self.roi_config = roi_config
        self.ui_classifier = ui_classifier
        self.ocr_engine = ocr_engine
        frozen_lexicon = ScreenLexicon.from_frozen_assets(session.assets, opponent_names=session.opponent_preview_six or ())
        configured = text_parser.lexicon
        merged_lexicon = ScreenLexicon(
            pokemon_names=tuple(dict.fromkeys((*frozen_lexicon.pokemon_names, *configured.pokemon_names))),
            move_names=tuple(dict.fromkeys((*frozen_lexicon.move_names, *configured.move_names))),
            statuses=configured.statuses,
            hp_pattern=configured.hp_pattern,
            remaining_turn_pattern=configured.remaining_turn_pattern,
        )
        self.text_parser = TextObservationParser(text_parser.patterns, lexicon=merged_lexicon)
        self.candidate_extractor = candidate_extractor
        self.effect_catalog = effect_catalog
        self.ocr_roi_names = ocr_roi_names
        self.classification_roi_name = classification_roi_name
        self.change_detector = RoiChangeDetector(threshold=change_threshold)
        self.stability_gate = UiStabilityGate(required_frames=stable_frames)
        self.episodes = MessageEpisodeTracker()
        self._latest_parsed_rois: dict[str, ParsedOcr] = {}
        self._observed_event_seq = 0
        self._processed_event_seq = 0
        self._recent_frames: deque[Frame] = deque(maxlen=recent_frame_limit)
        self._reanalysis_seq = 0
        self.hp_calibrations = hp_calibrations
        self.icon_calibrations = icon_calibrations
        self.visual_stabilizer = VisualCandidateStabilizer()

    def process_frame(self, frame: Frame, *, now_ms: int | None = None, actual_ui_remaining_ms: int | None = None) -> FrameObservation:
        started = perf_counter()
        effective_now = frame.captured_at_ms if now_ms is None else now_ms
        self._recent_frames.append(frame)
        raw_observation_id = f"{self.session.match_id}:frame:{frame.capture_seq}:{frame.frame_id}"
        classification_image = frame.image if self.classification_roi_name is None else self.roi_config.crop(self.classification_roi_name, frame.image)
        classification = self.ui_classifier.classify(frame.image if classification_image is None else classification_image)
        parsed_rois: dict[str, ParsedOcr] = {}
        raw_rois: dict[str, dict[str, Any]] = {}
        pending_events: list[ObservedEvent] = []
        for roi_name in self.ocr_roi_names:
            crop = self.roi_config.crop(roi_name, frame.image)
            if crop is None:
                continue
            changed, difference = self.change_detector.changed(roi_name, crop)
            raw_rois[roi_name] = {"changed": changed, "difference": difference}
            if not changed:
                continue
            reading = self.ocr_engine.read(crop, roi_name=roi_name)
            raw_rois[roi_name].update({"text": reading.text, "confidence": reading.confidence, "engine": reading.engine})
            episode = self.episodes.observe(roi_name=roi_name, image_signature=image_digest(crop), text=reading.text)
            if episode is None:
                continue
            parsed = self.text_parser.parse(reading, roi_name=roi_name, raw_observation_id=raw_observation_id, episode_id=episode)
            raw_rois[roi_name]["parsed"] = {
                "pokemon": list(parsed.detected_pokemon),
                "moves": list(parsed.detected_moves),
                "statuses": list(parsed.detected_statuses),
                "hp_values": [list(value) for value in parsed.hp_values],
                "remaining_turns": [list(value) for value in parsed.remaining_turns],
            }
            parsed_rois[roi_name] = parsed
            self._latest_parsed_rois[roi_name] = parsed
            pending_events.extend(parsed.events)

        self.store.append_raw_observation(
            self.session.match_id,
            raw_observation_id,
            {
                "frame_id": frame.frame_id,
                "capture_seq": frame.capture_seq,
                "captured_at_ms": frame.captured_at_ms,
                "source": frame.source,
                "reference": frame.reference,
                "visual_ui": classification.visual_ui.value,
                "ui_confidence": classification.confidence,
                "ui_scores": classification.scores,
                "rois": raw_rois,
            },
            capture_seq=frame.capture_seq,
        )

        self._observe_visual_fields(frame, raw_observation_id)
        applied_events: list[ObservedEvent] = []
        for event in pending_events:
            self._observed_event_seq += 1
            if self._apply_event(event, now_ms=effective_now):
                applied_events.append(event)
            self._processed_event_seq += 1

        stable = self.stability_gate.observe(classification)
        if stable is None and classification.visual_ui is DetectedUI.UNKNOWN:
            self.session.present_ui(VisualUI.UNKNOWN, None, now_ms=effective_now)
        elif stable is not None:
            actions = None
            if self.candidate_extractor is not None:
                actions = self.candidate_extractor.extract(frame, stable, self._latest_parsed_rois)
                if actions is not None:
                    # Candidate changes affect both legality and an in-flight response's freshness.
                    self.session.update_observed_state(
                        f"{raw_observation_id}:legal-actions:{stable.value}",
                        {"observed_legal_actions": actions.as_wire()},
                        source_kind="UI_DIRECT",
                        quality="CONFIRMED" if actions.completeness == "COMPLETE" else "CANDIDATE",
                    )
            self.session.present_ui(_FSM_UI[stable], actions, now_ms=effective_now, actual_ui_remaining_ms=actual_ui_remaining_ms)

        return FrameObservation(
            frame_id=frame.frame_id,
            raw_observation_id=raw_observation_id,
            classification=classification,
            stable_visual_ui=stable,
            events=tuple(applied_events),
            watermark=SyncWatermark(frame.capture_seq, self._observed_event_seq, self._processed_event_seq),
            processing_ms=(perf_counter() - started) * 1000.0,
            ocr_text={name: parsed.text for name, parsed in parsed_rois.items()},
        )


    def _observe_visual_fields(self, frame: Frame, raw_observation_id: str) -> None:
        """Persist calibrated local image observations without confirming gameplay.

        Calibration is opt-in and empty by default. Values remain CANDIDATE,
        and the v2.3 tactical snapshot receives an explicit uncertainty rather
        than silently replacing a confirmed HP/species/ability.
        """
        candidates = []
        for cal in self.hp_calibrations:
            item = estimate_hp_bar(frame.image, cal, frame_id=raw_observation_id)
            if item is not None:
                candidates.append(item)
        for cal in self.icon_calibrations:
            item = recognize_icon(frame.image, cal, frame_id=raw_observation_id)
            if item is not None:
                candidates.append(item)
        for candidate in candidates:
            repeated = self.visual_stabilizer.observe(candidate)
            if repeated is None:
                continue
            evidence_id = f"{raw_observation_id}:visual:{candidate.field}"
            applied = self.store.append_evidence(
                self.session.match_id, evidence_id,
                field=candidate.field, value=candidate.value,
                source_kind="UI_DIRECT", quality="CANDIDATE",
            )
            if not applied:
                continue
            existing = list(self.session.state_facts.get("uncertainties", []))
            note = f"{candidate.field}: {candidate.value} ({candidate.reason}; evidence={','.join(repeated.evidence_ids)})"
            # Bound the request size; observations remain append-only in SQLite.
            existing.append(note)
            self.session.update_observed_state(
                evidence_id + ":uncertain",
                {"uncertainties": existing[-20:]},
                source_kind="UI_DIRECT", quality="CANDIDATE",
            )

    def recent_frames(self) -> tuple[Frame, ...]:
        """Return the in-memory replay buffer; source pixels are never exported here."""
        return tuple(self._recent_frames)

    def reanalyze_buffered_frame(
        self,
        frame_id: str,
        *,
        parser: TextObservationParser | None = None,
        now_ms: int | None = None,
    ) -> tuple[ObservedEvent, ...]:
        """Append late OCR interpretations for one retained frame.

        This deliberately does not overwrite the original observation.  It is
        for a calibrated parser retry after a missed event and never creates a
        decision window by itself.
        """
        frame = next((item for item in reversed(self._recent_frames) if item.frame_id == frame_id), None)
        if frame is None:
            raise KeyError(f"frame is not retained for reanalysis: {frame_id}")
        self._reanalysis_seq += 1
        effective_now = frame.captured_at_ms if now_ms is None else now_ms
        raw_observation_id = f"{self.session.match_id}:reanalysis:{self._reanalysis_seq}:{frame.capture_seq}"
        parser = self.text_parser if parser is None else parser
        events: list[ObservedEvent] = []
        raw_rois: dict[str, dict[str, Any]] = {}
        for roi_name in self.ocr_roi_names:
            crop = self.roi_config.crop(roi_name, frame.image)
            if crop is None:
                continue
            reading = self.ocr_engine.read(crop, roi_name=roi_name)
            raw_rois[roi_name] = {"text": reading.text, "confidence": reading.confidence, "engine": reading.engine}
            # The reanalysis sequence makes this a distinct evidence episode.
            parsed = parser.parse(reading, roi_name=roi_name, raw_observation_id=raw_observation_id, episode_id=self._reanalysis_seq)
            events.extend(parsed.events)
        self.store.append_raw_observation(
            self.session.match_id,
            raw_observation_id,
            {"kind": "OCR_REANALYSIS", "original_frame_id": frame.frame_id, "capture_seq": frame.capture_seq, "rois": raw_rois},
            capture_seq=frame.capture_seq,
        )
        applied: list[ObservedEvent] = []
        for event in events:
            self._observed_event_seq += 1
            if self._apply_event(event, now_ms=effective_now):
                applied.append(event)
            self._processed_event_seq += 1
        return tuple(applied)

    def apply_manual_correction(
        self,
        correction_id: str,
        *,
        field: str,
        value: Any,
        supersedes: tuple[str, ...] = (),
    ) -> bool:
        """Append a verified correction and update the live projection safely."""
        applied = self.store.append_evidence(
            self.session.match_id,
            correction_id,
            field=field,
            value=value,
            source_kind="MANUAL_VERIFIED",
            quality="CONFIRMED",
            supersedes=supersedes,
        )
        if applied:
            self.session.update_observed_state(
                f"manual:{correction_id}", {field: value}, source_kind="MANUAL_VERIFIED", quality="CONFIRMED"
            )
        return applied

    def _apply_event(self, event: ObservedEvent, *, now_ms: int) -> bool:
        payload = event.as_payload()
        self._attach_catalog_rule(payload)
        applied = self.store.append_event(self.session.match_id, event.event_id, "NOTE", {"kind": "OBSERVER_EVENT", **payload})
        if not applied:
            return False

        effect_id = event.effect_instance_id
        if event.kind in {ObservedEventKind.MOVE_USED, ObservedEventKind.ABILITY_ACTIVATED, ObservedEventKind.ITEM_ACTIVATED}:
            effect_id = effect_id or f"{event.event_id}:activation"
            self.store.append_event(self.session.match_id, f"{event.event_id}:pending", "PENDING_EFFECT", {"effect_instance_id": effect_id})
        if event.confirm_effect and effect_id and event.certainty is EventCertainty.CONFIRMED:
            self.store.append_event(self.session.match_id, f"{event.event_id}:confirmed", "CONFIRMED_EFFECT", {"effect_instance_id": effect_id})
        elif event.kind is ObservedEventKind.MOVE_RESOLVED and effect_id:
            self.store.append_event(self.session.match_id, f"{event.event_id}:resolved", "RESOLVED_EFFECT", {"effect_instance_id": effect_id})
        elif event.kind in {ObservedEventKind.MOVE_FAILED, ObservedEventKind.MOVE_MISSED} and effect_id:
            self.store.append_event(self.session.match_id, f"{event.event_id}:failed", "FAILED_EFFECT", {"effect_instance_id": effect_id})

        if event.duration is not None:
            self._apply_duration(event)
        if event.kind is ObservedEventKind.TURN_END_CONFIRMED and event.certainty is EventCertainty.CONFIRMED:
            turn_id = str(event.facts.get("turn_id", event.event_id))
            self.session.confirm_turn_end(f"{event.event_id}:turn-end", turn_id)
        self._update_session_facts(event, effective_effect_id=effect_id)
        self._apply_fsm_event(event, now_ms=now_ms)
        return True

    def _attach_catalog_rule(self, payload: dict[str, Any]) -> None:
        if self.effect_catalog is None:
            return
        facts = payload["facts"]
        source_type = facts.get("effect_source_type")
        source_key = facts.get("effect_source_key")
        if isinstance(source_type, str) and isinstance(source_key, str):
            rule = self.effect_catalog.rule_for(source_type, source_key)
            if rule is not None:
                payload["effect_rule"] = {
                    "rule_id": rule.rule_id,
                    "verification_status": rule.verification_status,
                    "confirmation_required": list(rule.confirmation_required),
                    "activation_confirmed": bool(payload["confirm_effect"]),
                }

    def _apply_duration(self, event: ObservedEvent) -> None:
        duration = event.duration or {}
        operation = duration.get("operation")
        duration_id = duration.get("duration_id")
        if not isinstance(duration_id, str) or not duration_id:
            return
        if operation == "START":
            field = duration.get("field")
            candidates = duration.get("remaining_candidates")
            if isinstance(field, str) and isinstance(candidates, list) and all(isinstance(item, int) and item >= 0 for item in candidates):
                self.store.append_event(
                    self.session.match_id,
                    f"{event.event_id}:duration-start",
                    "DURATION_STARTED",
                    {"duration_id": duration_id, "field": field, "remaining_candidates": candidates},
                )
        elif operation == "CLEAR":
            self.store.append_event(
                self.session.match_id,
                f"{event.event_id}:duration-clear",
                "DURATION_CLEARED",
                {"duration_id": duration_id},
            )

    def _update_session_facts(self, event: ObservedEvent, *, effective_effect_id: str | None) -> None:
        facts = dict(event.facts)
        current = self.session.state_facts
        recent = list(current.get("recent_confirmed_events", []))
        unresolved = list(current.get("unresolved_event_ids", []))
        if event.certainty is EventCertainty.CONFIRMED:
            recent.append({"event_id": event.event_id, "kind": event.kind.value})
            facts["recent_confirmed_events"] = recent[-20:]
            quality = "CONFIRMED"
        else:
            unresolved.append(effective_effect_id or event.event_id)
            facts["unresolved_event_ids"] = sorted(set(str(item) for item in unresolved))[-20:]
            quality = "CANDIDATE"
        if event.kind in {ObservedEventKind.MOVE_RESOLVED, ObservedEventKind.MOVE_FAILED, ObservedEventKind.MOVE_MISSED} and effective_effect_id:
            facts["unresolved_event_ids"] = [item for item in unresolved if item != effective_effect_id]
        if event.certainty in {EventCertainty.UNKNOWN, EventCertainty.CONFLICT}:
            uncertainties = list(current.get("uncertainties", []))
            uncertainties.append({"event_id": event.event_id, "kind": event.kind.value, "certainty": event.certainty.value})
            facts["uncertainties"] = uncertainties[-20:]
        source = "UI_DIRECT" if event.roi_name in {"ROI_STATUS", "ROI_RESULT"} else "BATTLE_TEXT"
        self.session.update_observed_state(event.event_id, facts, source_kind=source, quality=quality)

    def _apply_fsm_event(self, event: ObservedEvent, *, now_ms: int) -> None:
        if event.certainty is not EventCertainty.CONFIRMED:
            return
        mapping = {
            ObservedEventKind.EFFECT_SWITCH_CHOICE_AVAILABLE: ObservationEvent.EFFECT_SWITCH_CHOICE_AVAILABLE,
            ObservedEventKind.RANDOM_FORCED_SWITCH: ObservationEvent.RANDOM_FORCED_SWITCH,
            ObservedEventKind.EFFECT_AUTO_SWITCH: ObservationEvent.EFFECT_AUTO_SWITCH,
            ObservedEventKind.MATCH_FINISHED: ObservationEvent.MATCH_END_CONFIRMED,
        }
        event_to_apply = mapping.get(event.kind)
        if event.kind is ObservedEventKind.FAINTED and event.facts.get("side") == "ours":
            event_to_apply = ObservationEvent.OUR_FAINTED
        elif event.kind is ObservedEventKind.SWITCH_OCCURRED and event.facts.get("reason") == "pivot_succeeded":
            event_to_apply = ObservationEvent.OUR_PIVOT_SUCCEEDED
        if event_to_apply is not None:
            self.session.observe_event(event.event_id, event_to_apply, now_ms=now_ms)
