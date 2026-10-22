# Turn-batched capture (experimental)

This is a **read-only test harness**, not a game controller. It never clicks, types, or saves screenshot files.

## Setup

1. Configure the Win32 window/ADB serial and crop to the game viewport in Settings.
2. Choose the ChatGPT model and **AIの思考の深さ** (reasoning effort); **自動** keeps the model's default.
3. Start a new match so the required 4 frozen masters are actually loaded.
4. Display the real team preview screen and click **選出画面を登録**.
5. During gameplay, display the real move menu and click **技選択画面を登録**. Do the same with the party switch menu and **交代画面を登録**.
6. Start **ゲーム画面を自動キャプチャしてAI支援開始** and explicitly consent to image uploads.
7. If menu detection fails, press **現在までの重要画像をまとめてAIへ送信（手動保険）**.
8. After the selection recommendation, manually confirm the three actually selected Pokémon and the lead. PREP remains a separate explicit operation.

## Timing and transport

- The GUI timer polls scoped, in-memory screenshots approximately every 300 ms; actual capture speed depends on Win32/ADB and the host.
- Compare a calibrated lower-right menu region against templates to detect stable command/party/team screens. Registration must use the actual game resolution and UI; recalibrate after resolution/layout changes.
- Store a bounded rolling set of images on detected changes to the whole viewport; the image buffer is not written to disk.
- On a stable transition to a registered **battle action** menu, send up to 4 chronological images and the frozen match state in **one** ChatGPT Responses request combining observation and proposed action. There is no per-frame API call.
- Team preview remains **two-stage** (recognize opponent six, then query the exact frozen six LIVE matrix rows and choose three/lead). Do not sacrifice LIVE-matrix selection accuracy to force one call.
- The network call is on a separate QThread while local game capture continues on the GUI timer. A pending batch is replaced by the newest trigger. No older recommendation should be promoted after the request times out.
- The proposed move/switch is passed through the original replay legal-action validation. Observations are never automatically treated as confirmed opponent sets.
- Repeated screens in the same menu entry do not cause duplicate requests; the trigger re-arms after the calibrated menu disappears in two consecutive observations.

## Limitations requiring Windows/game verification

- Calibrated template matching is a heuristic. Full-screen effects, changing scene layout, animations, UI scaling, and different backgrounds can lead to false positives or missed events.
- Coarse event sampling cannot guarantee every ephemeral battle message, move name, ability, ability change, or exact HP delta is retained. Manual sending is essential as a fallback.
- The normal match replay still has separate manual/prep/image probe paths; turn-batched capture is specifically used by the auto-capture dialog.
- Team-preview uses two model calls to preserve the exact LIVE-matrix rows; it is not included in the one-request-per-battle-turn optimization. Tactical validity still requires live verification.
- The GUI timer currently performs scoped capture on the UI thread. ADB capture especially may need a separate capture thread after performance measurement.
- API speed/usage depends on the selected model, effort and image sizes; a single multi-image request does not guarantee low image-token consumption or completion within the 45-second turn.
- No real live-battle validation has been performed by the GitHub runner. Validate UI recognition, API deadlines, state accuracy and battle recommendations on the actual Windows/BlueStacks setup.

## Privacy and safety

The app requires an explicit initial start/consent, captures only the configured viewport, and does not manipulate the game. In-memory images are transmitted to the connected AI account only on trigger or manual request. The exported JSON does not include screenshot bytes or OAuth tokens.
