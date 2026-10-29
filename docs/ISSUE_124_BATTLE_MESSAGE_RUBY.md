# Issue #124: battle message ruby exclusion (provisional)

Only the **battle_message** local ROI is affected. The default path is deliberately
unchanged until the *two body-row bands* are independently validated on the
actual game viewport. This is a cropping/recognition improvement, **not** a
match-result classifier or a new OCR engine.

## Diagnostic, including the three user screenshots

Open **テスト全般 → ローカルOCR診断**, select a local battle screenshot and
enable **戦闘メッセージのルビ除去を試す**. The preview shows the
*actual image sent to the OCR engine*, OCR text/confidence, extraction status,
source ROIs and separate preprocessing, OCR and total execution times. Uncheck and run again to compare the
old full-ROI OCR input. Each execution calls the current PP-OCRv6 ONNX OCR
**once per region** (no combined double-OCR in live or diagnostic). It never
sends to GPT, promotes a win/loss, or saves the raw image/text.

Use all three 960×572 samples: one and two body lines, lines extending as far
as possible right, smaller kana/kanji and final punctuation. Visually compare
both lines and every final glyph. Report deletions/truncation and ruby
inclusions; inspect them **before** enabling live usage. On users' private
copies, note baseline vs processed character error/ruby inclusion and
elapsed milliseconds. No such real-image measurement is claimed by this PR.

Initial *hypotheses* in the 960×572 active viewport:

- Body #1: X [141,540), Y [430,454).
- Body #2: X [141,540), Y [464,489).
- Ruby rows: ~424–429 and 456–462, excluded by construction.
- Two body bands are stacked with a background spacer into a **single** OCR
  image. Relative coordinates follow uniform 1×, 1.5×, 2× resize.
- No automatic OCR fallback comparison or extra GPT request occurs.

The horizontal span [141,540) matches the original battle-message ROI on
the three provided 960×572 screenshots. It avoids the unvalidated previous
X950 extension, which could include moving animations and retrigger OCR.
Longer messages **may be cut off at X540**; do not broaden the ROI until
user-approved long-text screenshots establish a safe width and repeat
the right-edge/noise test.

## Live gating and private calibration

The existing local `screen_fields.json` schema version 1 gains optional
`ruby_body_rows_validated` (default `false`). The live crop applies only when:

1. `validated_on_game_capture` is `true` **and**
   `ruby_body_rows_validated` is `true` (set only after a human checks the
   full source image, cropped body lines, and language recognition);
2. Reference resolution is 960×572; battle-message region is exactly
   `(141,412,540,505)` and is not an independently offset region;
3. Incoming active viewport matches the reference aspect (within tolerance),
   has no obvious full-width/height black bars, and the bright-text left-prefix
   **per-frame body anchor** matches the measured baseline. Missing, shifted,
   scaled or ambiguous glyphs disable this optimization. The visual check
   is a conservative heuristic, not a proof of unchanged UI alignment.

The JSON flag is a manual trusted calibration indicator, **not a promise of
accuracy**. Frames with shifted UI, unrecognized geometry or missing calibration
continue down the existing OCR path; they are not accepted as verified
match results. In particular, black-bar detection is conservative but cannot
prove absence of all kinds of overlay/offset. If the UI scale changes, clear
the flag and recalibrate; never force an ROI if the image appears misaligned.

The live worker records frame ID, observed time, prepared ROI and OCR duration
in the local observation candidate. All local OCR still has `CANDIDATE` status;
winning/losing still requires the independent result-screen and grounded text
checks. In ambiguous, missing or conflicting cases it remains
`RESULT_UNCONFIRMED`, with manual input.

## Test boundaries

`tests/test_battle_message_ruby.py` uses synthetic pixels to confirm scaled
coordinates, right-edge preservation up to X540, ruby exclusion, geometry/black-bar
safety, same-size UI shifts, UI text scale changes, right-side motion,
one read per region, candidate provenance, diagnostic toggling and total timing.
`tests/test_diagnostics_tabs.py` checks the offscreen GUI route.

Windows bundled ONNX inference and real Champions text recognition have to be
tested separately using user-approved private samples. A passing synthetic
test is **not** proof of improved win-detection accuracy, latency or zero
wrong outcomes in production. Do not commit actual screenshot files.
