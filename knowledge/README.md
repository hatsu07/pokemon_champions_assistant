# 対戦ノウハウ・出典管理（v2.7）

## 読み方と正本
- **LIVE判断の唯一正本**は `masters/Pokemon_Champions_MC_live_decision_gate.txt`。
- `masters/Pokemon_Champions_MC_live_selection_matrix.txt`は相手6行→18対面の基礎評価、`masters/Pokemon_Champions_MC_60sec_lookup_master.txt`は重大不足のみ補完、`masters/Pokemon_Champions_MC_realtime_support_master.txt`は非LIVE時の詳細資料。
- `masters/PROJECT_INSTRUCTION_live_matrix_addition.txt`は出典・役割を定義した**元ソース実ファイル**。LIVEゲート自体と競合する時はゲートに従う。
- `docs/Pokemon_Champions_Project_User_Guidance_Reconstructed_v2_7.md`はCodex向けの再構成。元設定画面の全文エクスポートではない。

## 収録されたノウハウの範囲
1. 対面勝ち負けより選出3匹全体、終盤役温存、安全な受け出し、積みエースの停止を優先。
2. こだわり技固定、双方の先制技、観測済みS、メガ後のタイプ/特性/フォーム変更、行動保証を忘れない。
3. PREP18対面と勝ち筋3点の完成条件、40秒打ち切り、ルックアップの限定使用。
4. ターン数と入力窓は別。共通ポケモンメニューの原因判定は状態管理、効果二重適用禁止。
5. レビューは結果論を避け、支援ミス/ユーザーミス/構築問題の原因を分離。複数試合の再現性を確認。
6. CODEX O/Xは分析補助。改善候補・検証・採否の履歴を残す。

## 未収録・確認できないもの
- ChatGPTのプロジェクト設定画面に書かれた**元指示全文の逐語エクスポート**。
- 本会話に添付されていない全過去チャットの完全な会話ログ、各試合の詳細な発話履歴。
- 実機の画面録画・スクリーンショット・OCR校正済み日本語メッセージ辞書。
- ソースコードによる有効性が実機検証されたAI判断改善ルール（今あるのは設計および参考マスター）。

## 静的・変動情報の分離
- `knowledge/Pokemon_Champions_environment_snapshot_2026-10-21_archive.txt`は**古い時点の資料**。最新順位と見なさない。スナップショットの中の取得日が別に明記されていればそちらを出典日として扱う。
- 非LIVE環境確認は `knowledge/Pokemon_Champions_environment_data_source_policy.md` を参照。使用率%の未公表部分を推定補完しない。
- `private_history/*.xlsx` は元のバイナリをそのまま再命名したもの。ログの真偽やデータ更新日の保証ではなく、受領した静的スナップショット。
- ランクマの対戦中に `private_history` もWebも開かない。試合前の学習/レビューに限定。

## 次にCodexへ実装させるべきノウハウ移行
- ログ用スキーマ（試合ID、双方6/3匹、選出、各入力機会、観測イベント、ユーザー選択とAI推奨の差、レビュー、支援原因、O/X候補）を設計。
- `private_history/*.xlsx` は行・列の定義を検証したうえで、**読み取り専用かつオフライン**でSQLiteへ移す。元ファイルを変更しない。改行/日本語/日付/数式/空欄を保護。
- 重複インポート防止には `(source_sha256, workbook_sheet, row_ref)` 等の複合キーを用いる。元シートの値を残し、欠落・列定義不明は「要確認」として報告。
- データを使って実戦補正候補を作る際は、結果・記録が揃った複数試合で照合し、反例と不確実性を明記する。
