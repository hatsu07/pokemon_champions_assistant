# Local calibrated battle field observation (incremental implementation)

The observer now optionally recognizes HP-bar *intervals* and icon *candidates*
from RGB screenshots. **No Champions screenshot calibration or real-game
accuracy claim is bundled.** Without explicit calibrations the existing observer
does not perform these recognitions.

## What is implemented
- `HpBarCalibration`: an ROI around the bar fill and an HSV color range,
  producing a conservative percentage interval. Refuses fragmented/no fill;
  0 HP or an exact percentage cannot be concluded from a missing bar.
- `IconCalibration`: per-slot ROI and **locally supplied** named RGB image
  exemplars. A unique strong match produces an identity *candidate*, not proof
  of a species, form, or battle mechanic. Low scores and ties yield no match.
- `VisualCandidateStabilizer`: requires observations from separate frames;
  this is **not** independent confirmation of a game fact.
- `ObserverPipeline(..., hp_calibrations=..., icon_calibrations=...)`:
  opt-in observation; append-only SQLite evidence with `CANDIDATE`
  certainty; no overwriting known HP, active Pokémon or forms.
- Candidate descriptions are passed as `battle_state.uncertainties`; when
  present, `snapshot_quality` becomes `READY_WITH_UNCERTAINTY`, never
  `READY_CONFIRMED`. A frame alone does not trigger a model action.
- Same unchanged candidate is not persistently rewritten each frame.
- Synthetic tests for fill estimation, fragmentation, icon ambiguity,
  duplicate evidence, and v2.3 uncertainty propagation.

## Needed real screenshots and calibration
Please provide unmodified RGB screenshots in the same resolution and viewport
used during live capture:
1. Selection screen with opponent six.
2. Command/4-move screen and switch picker.
3. Both HP bars at multiple percentages, ideally sequential frames during
   and after the animation, including 0 HP/fainting.
4. Pokémon icons in team preview and battle, including distinguishable forms.
5. Several consecutive frames of battle messages and transitions.

Annotate bar **inner fill bounds**, green/yellow/red HSV reference colors, and
icon slot ROIs. Store exemplars in private local calibration storage rather
than committing private captures to the repository.

## Explicit limitations
- The integrated main `match_replay_dialog.py` continues to use its existing
  image-batch-to-OpenAI path. The calibrated field observer is integrated in
  `ObserverPipeline`, not yet enabled automatically in that GUI. Linking it to
  the unified live panel and replacing the image-heavy path awaits calibrated
  game screenshots, end-to-end state reconciliation and safety tests.
- Per-message OCR extraction already exists in `TextObservationParser` but
  battle message layouts and real-image recognition are unvalidated.
- Exact HP, species/form certification, icon training set, UI location,
  5-class classifier weights, and gameplay turnaround time are unverified.
- No Pokémon tactical choices are made in this module and no game inputs
  are automated. LIVE source preload remains once per match.
