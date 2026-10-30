# Issue #131 — live GPT payload minimization

## Invariants

- Raw OCR, confidence, timestamps, display IDs and frames remain untouched in
  `LocalBattleStateTracker` and `OcrSentLedger`; the model receives only
  `id`, `field` and `text`. Each ID resolves only against the current
  request's selected local records. This **does not certify** the OCR.
- Existing `compact_ocr_transport` groups adjacent rows only when the
  visual `display_id` and field agree and the text is equal after strict
  normalization or cosmetic punctuation normalization. Important changes to
  names, numeric values, stat directions and move names never get fuzzy merged.
  Different display IDs remain separate even if text is identical.
- Legacy proof validation runs after ID resolution. No change to the local
  strategic decision policy or Champions master data.
- Duplicate request fields `our_registered_moves`,
  `known_hp_by_pokemon`, and `recent_history` are omitted. Selected
  registered moves, full battle evidence, battlefield, opponent candidate six,
  current matchup PREP and meaningful uncertainty remain in each independent
  turn request. No presumption of server-side conversation persistence.
- The GPT turn stream records first SSE event, first text output, and complete
  output (when present), separately from pre-API and validation time.
- These changes do not create an extra API call. Actual speed and game accuracy
  require real capture/API experiments. Windows CI tests do not prove them.

## Scope / future refinements

Local PREP cache and frozen match assets are already available before live
turns; continue extracting only the relevant current matchup without rereading
masters. More aggressive event-variant merging, schema shortening, conversation
state and prompt caching are deferred pending measured accuracy/latency and
privacy review. An OCR whose display continuity cannot be verified is **not**
fuzzy merged. Compression does not mean acknowledgment or state confirmation.
