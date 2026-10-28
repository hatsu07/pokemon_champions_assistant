# Issue #44 – Historical real-game evidence and independent tactical roles

> **履歴補足:** 本書の `WINDOWS_BLUESTACKS` は当時の記録形式です。現在はBlueStacksを必須とせず、`WINDOWS_REAL_CAPTURE` を新しい汎用実機証拠ラベルとして扱います。実際の映像取得方式はユーザーの確認まで未確定であり、合成・リプレイは実戦証拠に昇格しません。

This stage implements an **offline evidence validator**, not an automated battle player or an actual device test. Synthetic Windows CI passing must never be represented as a successful real ranked match. Issue #44 remains open until real evidence and Champions mechanic verification exist.

## Device evidence import

In the battle-support app, before a match, use Settings → Team → 実機対戦90/40/45秒ログを検証（対戦前）. Supply a private JSON packet with schema champions-live-e2e-device-evidence-v1, exact registered team_id, team_fingerprint, regulation, platform WINDOWS_BLUESTACKS, human_reviewed true, and a matches array.

Each match must contain a unique match_id; source_kind REAL_CAPTURE; opponent_kind HUMAN_CONFIRMED; result WIN or LOSS; observed_opponent_count 6; prep_matchups_count 18; prep_win_roles_count 3; screen_recognition_errors and stale_advice_errors (both explicit integers); capture.path to the *actual* local private recording; capture.sha256 of the file; and a timeline.

Example timeline values below are **illustrative only and never evidence of a completed match**:

~~~json
{
  "selection_visible_ms": 0,
  "selection_displayed_ms": 35000,
  "selection_confirmed_ms": 50000,
  "prep_finished_ms": 72000,
  "opponent_lead_visible_ms": 79000,
  "turns": [
    {"action_screen_ms": 81000, "recommendation_visible_ms": 94000},
    {"action_screen_ms": 115000, "recommendation_visible_ms": 131000}
  ]
}
~~~

All times are monotonic milliseconds with a common arbitrary origin. Selection must finish within 90s, PREP from confirmation to completion within 40s and before opponent lead, and **every turn** from command screen to visible action advice within 45s. At least five distinct real human-vs-human matches, distinct actual capture files and SHA256, no screen or stale-response errors, and all deadlines are required for the device evidence threshold. Five matches alone never prove long-run win rate. The battle-support GUI now exports bounded monotonic **timing candidates** inside the user-initiated match JSON export, covering detected screen candidates, visible AI responses, actual-selection confirmation, PREP start/outcome and timeouts. These timestamps are marked UNVERIFIED_LOCAL_GUI_MONOTONIC_CLOCK, and recognition may be delayed or wrong. They are **not** the device evidence packet itself: review timestamps against real screen video + logs, fill the verified device packet, and never copy synthetic test data.

To generate the exact team_fingerprint, open generated matchup-evidence.json and call the existing stable_hash helper on a JSON object with members equal to member_signatures and regulation equal to catalog.regulation. The validator rejects wrong builds and unverifiable files.

Validated summaries are stored locally under data_root/e2e-real-battle/TEAM_ID/device-quality-audit.json. **Even if device_evidence_passed is true, live_ready remains false.** The tool checks structural and local file-hash integrity, not authenticity of a screenshot, tactical correctness or game identity. Nothing is uploaded.

## Processor and safe-switch observations are separate

Human review JSON now allows an optional role_observations object in each review. Each observation SHA must also be present in its sources list.

- processor: source_sha256, observed_our_move, opponent_hp_before (integer), opponent_hp_after (integer), our_action_before_opponent true
- safe_switch: source_sha256, hard_switch_performed true, observed_opponent_move, own_hp_before_switch (integer), own_hp_after_entry (integer)

The results are OBSERVED_ONE_CONDITIONAL_ATTACK and OBSERVED_ONE_CONDITIONAL_HARD_SWITCH. These are **not** certification of a reliable processor against all possible moves or a safe switch to an unknown four-move set. Never copy processor conclusions to safe_switch or promote either into new LIVE matrix matchup grades without corroborated mechanics and separate condition-specific review.

## Still blocking full Issue #44 closure

1. Actual in-game validation of Pokémon Champions specific effectiveness, damage, effects, abilities, held items and Mega/form behavior; the 2026-10-25 web-research candidates are not confirmed.
2. Independently reviewed game recordings for the 90/40/45 deadlines and screen-recognition accuracy for actual matches; synthetic CI cannot establish these facts.
3. Conditional verification of processor and safe landing independently for priority, offensive coverage, weather, setup, Mega and other common win paths.
4. A separate documented quality acceptance decision for full LIVE_READY across new builds only when gameplay and mechanics are corroborated.

Keep the original four LIVE masters frozen during matches, never insert research candidates into real-time decisions, and do not close Issue #44 on offline tests alone.
