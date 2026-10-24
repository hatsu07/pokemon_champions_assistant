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

## 2026-10-24 screenshot-grounded ROI calibration (960 x 572)

Eight user-supplied in-game PNGs were inspected locally. **None of those
source images has been committed to the repository.** The region coordinates
in `calibrated_screen_fields.py` are from those images only:

| Observed field | Approx. source pixels (x0,y0,x1,y1) | Caution |
|---|---|---|
| Our current/max HP OCR | 126,526,218,562 | Fraction appears near the active Pokémon's lower-left card |
| Opponent displayed HP percent | 864,53,938,93 | Numeric text partly overlaps the green HP bar |
| Opponent name/status | 777,14,877,49 | Same top-right card; names may change |
| Battle-log main text | 141,412,540,505 | Battle resolution only, not a command prompt |
| Opponent ability popup | 754,195,941,284 | Example showed 「マスカーニャの へんげんじざい」 |
| Our item popup | 58,194,267,279 | Example showed 「ドドゲザンの オボンのみ」 |
| Opponent selection list | 766,88,941,477 | Icon-only entries need separate image templates |
| Switch/detail screen | 320,58,729,508 | Contains inspected moves/ability/item; reason for switching is unknown |

Real numeric examples in the supplied screenshots: our HP `82/207`,
`117/131`, `112/112`, opponent HP `97%`, `100%`. The 8
screenshots were not a time-aligned independent ground truth for accuracy
scoring; they are a layout reference. HP bar color is **not** authoritative
when a displayed number covers part of the bar.

## Integrated GUI behavior

When the existing GUI receives a trusted stable five-class screen label, or
the user-triggered menu-template registration emits a matching label, the Qt
batch worker may run **bounded ROI OCR** against the latest 960x572 RGB frame.
The result is included in the **existing** image-batch OpenAI request under
`local_field_candidates_unverified` and in the local report. Screenshots
remain in memory. The OCR branch is never run on the GUI polling thread.
If the viewport size differs, the classifier is unknown, or Tesseract fails,
the existing image-to-ChatGPT fallback is retained without made-up values.

This is an **incremental hybrid path**: image sending still happens.
Calibration from eight still frames has not justified disabling the visual
fallback, sending screenshots as structured data alone, or automatically
trusting the popup contents as a battle mechanic. The separate Phase 2
`ObserverPipeline` still stores opt-in HP/icon CANDIDATE evidence via
`battle_visual_fields.py`; the unified GUI does not instantiate a second FSM.

## Remaining validation

1. Collect a few time-aligned consecutive frames during HP animation,
   fainting, messages, mega transformations and turn changes.
2. Measure exact ROIs on the actual Windows capture viewport, including
   letterboxing and scaling. Validate Tesseract Japanese traineddata on
   Windows, then move regions to the user's private calibration profile.
3. Generate private icon reference crops per form and verify top-1/top-2
   separation on independent matches. No icon templates are bundled yet.
4. Measure per-field false-positive/false-negative rates, OCR p95 time,
   API latency and action deadline misses before reducing image payloads.
5. Finish a single durable v2.3 state-reconciliation engine for the unified
   GUI without introducing a duplicate match FSM, then test real turn
   boundaries, choices and stale-response safety.

**Neither exact species/form classification nor HP recognition performance
has been validated on real capture hardware.** No local Python decisions,
automatic game inputs, LIVE re-reads, or web lookups are added.
