# 実機対戦支援の結果画面・遅延・終了動作 (2026-10-29)

## validated による挙動の差

| 項目 | true | false |
|---|---|---|
| 通常のモデル読み込み | 品質承認済みとして許可 | 拒否。明示的な試験同意が必要 |
| モデル選択時の警告 | 通常 | 未検証・試験利用の確認 |
| 監視開始時の警告 | 通常 | 毎回未検証モデル試験利用を確認 |
| ランタイム表示 | MODEL_READY_* | MODEL_TRIAL_* |
| 画面分類と信頼度閾値 | 6分類schema v3 | 同じ6分類schema v3 |
| RESULT_SCREEN安定検出後 | 直前の戦闘OCRをGPTに送信して勝敗判定 | **同じ**。以前は手動確認分岐で停止していたが修正 |
| GPT根拠不足 | RESULT_UNCONFIRMED | 同じ。勝敗を推測しない |
| 正式リリース品質ゲート | 検証条件の一つ | 未通過。validatedは書き換えない |
| 画面収集アプリのラベル候補 | 利用可 | 実験用として利用可。人手確認が必要 |
| ONNXとJSONのSHA、ラベル順 | 厳密検証 | 厳密検証。省略しない |

## 直前の戦闘OCRからGPTで勝敗判定

RESULT_SCREENを高信頼で2フレーム連続検出 → その時点より前に登録した戦闘メッセージOCRの完了を待機 → 直近最大10件のテキスト・原フレームID・信頼度をGPTに送信 → GPTがプレイヤー視点のWIN/LOSS/UNKNOWNと引用を返す → 根拠とフレームIDが一致する場合だけWIN/LOSSを確定し、ENDEDへ移行する。結果画面の画素を勝敗OCRしたり、Pythonだけで勝敗を推測しない。根拠が不足する場合は「勝敗未確定」と表示する。通常画面の手動勝敗ボタンは非表示にする。

## 遅延の現状と追加計測

実機1試合のログでは、ローカルONNX画面分類は十数ms程度だが、GPTへの判断依頼で20〜50秒台に達する例があり、古い結果が破棄された。OCR候補は最大約380件、PREPは以前のソフト期限後も継続していた。データ量とAPI処理がそれぞれどれだけ遅延したかは確定できない。

今回追加する主要な診断イベント:
- AI_TURN_PAYLOAD_SIZE: GPT入力総バイト、OCR候補件数、未確定質問数。
- AI_API_STAGE / OCR_TURN_TIMING: 通信待機、前後処理、例外型。
- AI_OCR_OWN_ACTIVE_BLOCK_DETAIL: 交代時の空欄・不一致・棄却された状態変更理由。
- PREP_START / PREP_WORKER_RESULT_DIAGNOSTIC: 40秒予算、キャッシュ有無、評価数、入力文字数、モデル応答時間。
- PREP_HARD_DEADLINE_INCOMPLETE / PREP_RESPONSE_AFTER_CUTOFF_DROPPED: 期限超過時の不採用。
- RESULT_SCREEN_OCR_GPT_GATE / RESULT_OCR_BARRIER_WAIT / RESULT_OCR_BARRIER_RELEASED: 判定トリガーと戦闘OCR待ち。
- AUTO_STOP_REQUESTED / SHUTDOWN_REQUESTED / SHUTDOWN_WORKERS_DRAINED / WINDOW_CLOSE_WAITING_FOR_WORKERS / AI_RESPONSE_AFTER_STOP_DROPPED: 停止要求と通信ワーカー終了時間。

## 停止と閉じる処理の境界

「自動送信を停止」は新規キャプチャ／AI依頼を停止し、古い回答を適用しない。同期HTTP通信が進行中の場合、停止操作だけで物理的に即中断できたとは扱わない。ウィンドウを閉じる際はQtワーカーを強制終了せず、UIを先に隠してワーカー終了を待つ。処理の残存時間は次回ログとWindowsタスクマネージャーで確認する。

## 検証

自動テストは実機の時間内成功率を証明しない。Windows実機で、未検証ONNXからのRESULT_SCREEN→GPT→WIN/LOSS、判定不能時の表示、停止・閉じる、40秒PREP、交代時のOCR空欄応答、OCR入力件数とAPI待ちを確認する。元の対戦画像やOCR本文はGitHubに登録しない。
