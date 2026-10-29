# Issue #124: two-ruby-band masking for one- and two-line messages

This change only affects the local `battle_message` region and **does not**
interpret battle results or change the OCR engine. The same single PP-OCRv6
read is used whether the message has one or two lines.

## Algorithm and boundaries (reference 960×572)

- Keep the complete existing `battle_message` OCR ROI **X=[141,540),
  Y=[412,505)**. Do not extract/stack/reflow the text lines.
- Apply **both** half-open ruby masks **X=[141,540)**,
  **Y=[423,430)** and **Y=[454,464)**, regardless of how many body lines exist.
- Each thin stripe is replaced using per-column vertical interpolation
  between adjacent, unmasked source rows (not a solid black rectangle).
  Every byte outside the two ruby stripes remains unchanged. In particular,
  the main text rows around Y434–453 and Y466–487 are retained.
- The same normalized masks scale with the existing calibrated ROI and its
  2× cap, including the original `profile.crop` behavior.
- If the first-line anchor is not in its expected location, if an optional
  second-line anchor is misaligned, or if an extra main-text run is detected
  in the inspected region, **fall back to the original ROI** with one OCR.
- The first mask **never touches Y430 or Y431**, which may contain upper
  portions of the first main-text glyphs. Before either mask is applied, the
  **full width X=[141,540)** of both ruby bands is inspected for unusually
  dense, neutral-white body-sized strokes. The brightness baseline is sampled
  from narrow side gutters rather than the potentially ruby-dominated band. A suspicious band (including a
  shifted second line outside the left-side anchor) is not modified; the
  original ROI is read once. This check is intentionally conservative and
  may reject some legitimate, long ruby text. It cannot guarantee every
  subtle offset is detectable: verify with real game images before live opt-in.
  A legitimate single line is allowed; absence of a second line does not
  by itself disable masking.

This is calibrated to the three privately shared game screenshots. It is
not proof for every dynamic UI scale, long text, offscreen line, animation,
or alternate language. The anchor check is conservative but not a complete
independent layout classifier. **Do not weaken it to force masking.**

## Diagnostic in テスト全般

Open **ローカルOCR診断**, select a game screenshot, and run with
**戦闘メッセージのルビ除去を試す** checked and unchecked. The preview
shows the exact OCR input, whether two-band masking was applied,
`ruby_mask_rois`, OCR text and confidence, preprocessing time, OCR
inference time, and combined time. Each run invokes the same OCR engine
once per applicable ROI, sends no AI request, and never promotes the
result to verified match state.

For real-game quality evaluation, compare the one-line messages
`効果は いまひとつだ` and `効果は かなりいまひとつだ` and the
two-line `ハツとの / 勝負に 負けた！` message against baseline OCR.
These example transcriptions are intended as **human checks**, not a
claim of passing real PP-OCRv6 tests. Do not commit original screenshot
bytes, OCR text or personal metadata to GitHub. Also test long text past
the original ROI X=540 before extending the width: the crop can truncate
text that lies outside X=540.

## Live safety and state

The private `screen_fields.json` needs **both**
`validated_on_game_capture=true` and
`ruby_body_rows_validated=true` for live masking. The latter is kept as
a backward-compatible profile name for explicit user calibration;
the output metadata uses `ruby_mask_rois`. Default is false, so live
continues through its previous full-ROI OCR route until actual game
calibration is independently confirmed. Any incompatible geometry,
obvious black bars or misplaced main-text anchor falls back to the
existing OCR route. No duplicate OCR or GPT calls are added.

Raw OCR is still `CANDIDATE` and must not assert WIN/LOSS. Unclear or
conflicting results stay `RESULT_UNCONFIRMED` with manual confirmation.
Do not infer Champions mechanics from an OCR text snippet.

## Tests and remaining acceptance checks

Synthetic regression tests cover one- and two-line ruby removal, mask-only
pixel edits, boundary glyphs at Y430–434, right-side shifted second lines,
rightmost body punctuation, resize normalization,
shifted/extra second lines, one-line live/diagnostic OCR, and fallback
on invalid calibration/aspect/black bars. Windows CI also checks the
bundled GUI and offline OCR integration.

Still required before calling Issue #124 fully complete: real PP-OCRv6
ON/OFF comparisons on private images (ruby inclusion, dropped/incorrect
glyphs and punctuation, latency), game-window scale and offset variants,
longest message endings, and real-game win/loss safety (zero reverse
outcomes). Synthetic `CountingEngine` tests do **not** measure
Japanese recognition accuracy or validate an actual match result.
