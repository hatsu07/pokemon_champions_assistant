# 子Issue #115 / P2 — 試合外の複数試合・原因分析とCODEX O/X

## 目的と安全境界

- P1で**凍結・整合性検証済み**の同じ構築版による独立3試合以上と、反復した試合後GPTの仮説が集まった場合に限り、既存の試合後レビュー処理の**後**で追加GPT分析を1回実行する。
- `src/champions_assist/postmatch_causal_analysis.py` の `PostmatchCausalStore` は試合外専用。新たなLIVE判断ゲート・追加ルックアップ・技変更・チームCSV/マトリクス更新・対戦操作は**一切行わない**。
- 反実仮想の数値重みは未検証。O（選択肢の優位性）とX（実行の安定性）は補助ラベルであり、勝率向上、確定KO、安全受け、新LIVEルール採用の証明ではない。

## 分析する情報

1. **P1研究バッチ**: 同一 `team_id` / `team_signature` で反復した `GPT_HYPOTHESIS_UNVERIFIED` のみ選択。直前の研究バッチと比較し、今回新たに追加された3独立試合以上に裏付けられ、トリガー試合を含む候補のみ対象（最大6試合、選定候補SHAを監査）。候補SHA順の先頭だけを分析することはない。新しい反復候補がなければAPI通信せず `NO_RELEVANT_NEW_PATTERN`。
2. **試合時点の知識**: `MatchEvidenceArchive.verify() == VALID` が全件必須。構築SHAとレギュレーション、戦術版・環境日付が一致する場合のみ扱う。P1イベントは各試合の最初から最大5件に制限し、報告の `captured_at`、画面候補、選出、登録技（現在の合法択とは限らない）、GPT推奨、**確認された実行/UNKNOWN**を分離して送る。結果は `result_after_match` に別欄保存し、当時の観測として過去へ格上げしない。
3. **LIVE判断ゲートの優先**: P1が試合開始時にメモリで凍結した `gate` SHA と、試合外に参照する唯一正本 `Pokemon_Champions_MC_live_decision_gate.txt` の実バイトSHAが完全一致した場合だけ、既存 `Rxx` 規則に加えて `SWITCH_GATE`、`HP_LOW_GATE`、`ROLE_END_GATE`、`ATTACK_GATE`、`PRIORITY_GATE` 等の番号なし節を長さ制限付きでGPTへ渡す。節を途中切断して正しい規則として扱うことはせず、収まらない節は使わない。ゲートの版が不明・変化した場合は `UNKNOWN`。モデルが既存ルール違反を主張しても、ルールID実在と**実行確認**を伴わなければ `RULE_COVERAGE_UNKNOWN`。ルールが見つかった場合も `EXISTING_GATE_EXECUTION_CANDIDATE`（仮説）であって証明ではない。
4. **カテゴリーの分離**: TEAM / SELECTION / PLAY / SUPPORT / OCR_SYNC / LUCK / UNKNOWN、owner=TEAM / USER / SUPPORT / RECOGNITION / UNDETERMINED。人間戦・CPU疑い・不明は凍結済み候補を越えて補完しない。
5. **O/Xの比較**: 安全択・高期待値択・読み択・負け筋を作る択を説明可能な範囲だけ比較し、合法性・対面効果・ダメージ/行動保証の裏付けを欠く場合は `NOT_VERIFIED` / `HOLD_GATE_OR_ACTION_UNVERIFIED` とする。既存ルールで防げたと疑われる場合は `EXISTING_RULE_EXECUTION_HYPOTHESIS`、**新規O/Xルールを自動生成・昇格しない**。

P1にはゲーム内で実際に押された技を毎ターン確定できるとは限らない制限がある。したがってP2は、**実行未確認の試合をあたかも確定したプレイミスと判定しない**。またGPTへの入力に同じ試合の複数イベントが存在すると、後時点の情報が漏れ得る。モデルへ各イベントの知識カットオフを指示しているが、モデルの内部利用を機械的に保証できないため全候補は `HOLD` のまま維持する。独立反事実の採否検証は子Issue #117 / P4で実施する。

## 出力・費用・復旧

- 構造化Responses API `champions_p2_causal`、`store=False`、`max_output_tokens=3500`、1件ずつ最大240文字の所見、最大6所見。モデルのID参照・イベント番号を実データと照合。架空のmatch ID・event番号は保存せず `FAILED_HOLD`。
- プロンプト上限23000文字、最大3～6試合・1試合5イベント。結果/メモリが不足している場合は通信せず保留。**画像、OCR生テキスト、シークレット、未制限の全対戦ログは送信しない**。
- バッチSHAで予約・冪等保存。自動通信は1バッチ1回まで、明示的な `allow_retry=True` 時だけもう1回可能（最大2回）。`BEGIN IMMEDIATE` で同時ワーカーの二重通信を抑制。**予約後、通信開始前のキャンセルは `CANCELLED_HOLD` に遷移し、未使用の通信枠を返す。** `RESERVED_UNVERIFIED` に放置しない。明示的再試行は可能で、自動リトライはしない。通信開始後のモデル通信失敗は予算を消費したとして記録する。費用金額が取得できない場合は推測表示しない。
- バトル開始時、前試合のワーカーが残っていればQtのinterruption flagを設定。P2のモデルstreamは中断を確認し、通信を止めて `CANCELLED_HOLD` へ。単発の試合後GPTレビューやP1の保存を巻き戻さない。
- `matchup-learning.sqlite3` の `p2_causal_batches` は未検証研究の読戻し用。結果を `STORED_HOLD_UNVERIFIED` に固定、`live_applied=False`。研究結果はUIの既存「対戦後レビュー」に件数と保留を表示する。自動次戦反映は子Issue #118 / P5まで禁止する。

## 受入検証

`tests/test_postmatch_causal_analysis.py`: 合成3試合のゲートSHA照合、既存ルール仮説・O/X保留、架空試合ID拒否、構築/環境版不一致、P1欠損/不完全、通信予算・冪等性・明示再試行、APIを呼ばないケース、非公開ログ非送信、異常時の既存レビュー保持を検査する。`tests/test_autonomous_improvement.py` と既存のGUI・試合後レビューE2EはWindows CIで引き続き検証する。

**重要**: 子Issue #115はCIと人間のPRレビューが揃うまでcloseしない。親Issue #92の統合ブランチをbaseとし、勝手にマージしない。ユーザーのレビューを待つ。
