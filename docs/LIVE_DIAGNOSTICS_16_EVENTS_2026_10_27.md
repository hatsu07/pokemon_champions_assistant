# ライブ対戦16項目診断ログ

対象: 「マリルリが場にいるのにマリルリへの交代が再表示された」等の状態同期・表示誤りの原因特定。
1試合1ファイル `debug-logs/capture-<id>.jsonl`。セッションID、`turn_epoch`、`frame_seq`、`request_id`、`state_revision` を共通キーとして結合する。時刻はUTCと単調計時 `at_ms`。これはゲーム内ターンの直接測定ではない。

## 必須P0（9項目）
1. 画面分類：`SCREEN_CLASSIFIER_CHANGED` / `SCREEN_GATE_DISPATCH`（分類・信頼度・フレーム）
2. OCR生結果：`OCR_RAW`（ROI名・認識原文・OCR信頼度・撮影／完了時刻・検出名）
3. OCR解析：`OCR_RAW.detail.parsed` と `OCR_DECISION.kind`（解釈結果は生文字と区別）
4. 採否：`OCR_DECISION`、`OCR_DEDUP`（採用／保留／却下の理由）
5. 状態差分：`OCR_STATE_UPDATE` / `AI_STATE_UPDATE`（before/after、現在のポケモン・HP・こだわり固定等）
6. AI入力：`ACTION_REQUEST` / `AI_INPUT`（対面・3匹・技・交代候補・HP・フィールド・OCR件数。**完全なプロンプト本文は保存しない**）
7. AIの検証前応答：`AI_RESPONSE_RAW`（行動種別・技・交代先・警告、および画像観測の主要フィールド。**API通信本文は保存しない**）
8. 行動検証：`AI_VALIDATION`（採否・検証コード・提案内容）
9. GUI最終指示：`GUI_DISPLAY`（実際の表示文・出典）、`SWITCH_REUSED`（直接の再表示）

## 補助P1（7項目）
10. 交代履歴：`ACTIVE_TRANSITION`（変更前後、出典、観測フレーム）
11. アクティブ確度：`ACTIVE_PROVENANCE`（名前ROI確認）と `ACTIVE_TRANSITION.verified`
12. ターン／リクエスト：`TURN_SCREEN_OBSERVED`、`ACTION_REQUEST`、`AI_REQUEST_DISPATCH`
13. 非同期・古い結果：`OCR_NEW_STATE_INVALIDATED_OLD_AI`、`OCR_REVISION_RETRY_QUEUED`、`STALE_TURN_ADVICE_DROPPED`
14. 交代指示再利用：`SWITCH_REUSED`（元ターン・表示ターン・交代先）
15. 情報源の衝突：`SOURCE_CONFLICT`、`OCR_DECISION`（OCR原文を確定情報へ無条件昇格させない）
16. 性能・欠落：`LOCAL_OCR_TURN_SUMMARY`、`AI_WORKER_FINISHED`、`DIAGNOSTIC_QUEUE_SUMMARY`

## 保存・性能・安全
- 保存はゲーム操作を伴わず、通常の対戦経路では最大2048件のメモリキューに`put_nowait`して専用スレッドでJSONL追記。ログ欠落時もAI判断を止めない。終了処理時にキューを排出する。
- 1ファイル3 MiB以下、直近20ファイル。容量超過時は追記を停止。キューあふれ件数は正常終了時の`DIAGNOSTIC_QUEUE_SUMMARY`に記録する。
- OCR文字列は原文に近い診断抜粋として800文字以内、構造化フィールドは明示許可キーのみ。資格情報・メール・アクセストークンらしき値はマスクする。
- ピクセル／スクリーンショット／認証データ／プロンプト全文／未知のモデルフィールドは保存しない。実機のOCR認識精度とゲーム内時計に対する処理時間は別途E2E検証が必要。
- `state_revision`は内部状態版、`turn_epoch`はローカル画面判定由来であり、ゲームの公式な事実とみなさない。AIの提案は実際にゲーム内で行動した証拠にはならない。
