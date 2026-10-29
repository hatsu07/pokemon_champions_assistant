# 親Issue #92 / 子Issue #114 — P1: データ基盤・対戦時点証拠保存

**P1の実装範囲:** 元の構築/4 LIVEファイルを変更しない形で、試合前の構築・戦術・環境出典を凍結し、実際に画面から得た情報とGPTの助言を時刻付きで区別する。試合中はメモリのみ使い、対戦終了後にSQLiteへ一括保存する。複数試合の未検証研究履歴も作る。

P2～P6（原因分類、構築/戦術候補生成、独立品質評価、検証済み候補の自動採用とロールバック、管理UI）は **まだ未実装**。P1で保存されたAIの説明を正しい実戦知識とみなしてはならない。勝率向上も未実証。

## 1. 凍結する情報と出典

開始前 `DesktopMainWindow._prepare_unified_match()` で構築CSVから以下を凍結する。

- team_id、構築CSVのSHA、6匹と4技・特性・持ち物・性格・AP・フォーム情報、選択モデル。
- 自軍登録CSVにあるレギュレーションと出典 `REGISTERED_TEAM_CSV_UNVERIFIED`（公式認証とはみなさない）。
- prepared manifestにある環境スナップショットの種類と生成日時。出典不明や日付なしのときは補完しない。
- 構築別LIVEマトリクス・ルックアップのSHA、derived team tactical fingerprint、凍結時刻。

試合中 `MatchEvidenceBuffer.capture` は最大256件、1件3072バイトまで、以下の*型が限定された値のみ*メモリへ記録する。

- `captured_at`（画面へ報告が到達した時刻）、ステップ番号、ターン識別子（取れなければUNKNOWN）、フレームID（取れなければUNKNOWN）、フェーズ、画面分類、HP/場の効果/相手の種別などの情報候補、情報源。
- 相手・自軍の現在表示、選出3匹と初手の確定／仮定の区別。モデルの選出提案は現実の選出とは別の欄へ保存。
- 登録された4技、控え候補、画面から読めた技候補と `REGISTERED_NOT_CURRENTLY_LEGALITY_VERIFIED` ラベル。PP・選出枠・こだわり固定など実際の合法性が不明な場合、登録情報だけでその場の合法手と断定しない。
- GPTの提案技／交代と表示時刻。実際に押された技や交代は **明示的に `USER_CONFIRMED` または `GAME_EVENT_CONFIRMED` の証拠がある場合だけ** 別欄に記録。確認が取れない限り `execution.certainty=UNKNOWN`。
- `AI_VISUAL_CANDIDATE` / `OCR_CANDIDATE` / `UNKNOWN` を観測事実として自動昇格しない。追加のGPT通信やゲーム画面の自動操作はしない。

**現在のキャプチャ経路の限界:** AIの観測レポートからは、実際にプレイヤーが押したボタンを毎ターン確定できない。したがって実行確認が存在しない対戦ではUNKNOWNを記録する。保存される時刻はレポートがUIに到達した時点であり、画像撮影時刻の確証ではない。元のフレーム時刻が与えられた場合も無断推測・書換えしない。

## 2. 終了時のみ非破壊保存

- `MATCH_END` かつ `WIN/LOSS` の既存結果記録が保存されてから `MatchEvidenceArchive.save_ended()` が呼ばれる。
- `matchup-learning.sqlite3` に `p1_evidence_match` / `p1_evidence_events` を非破壊作成する。既存 `match_history` / `postmatch_reviews` / 元構築CSV/固定マスターへの書き換えをしない。
- 移行時、元DBに記録がある場合は最初のP1テーブル追加前に端末内へ `matchup-learning.pre-p1-backup.sqlite3` を1回だけ作成する。自動復元はせず、復旧作業はゲーム停止中にバックアップの整合性を検証して行う。
- 同一match_idと同じアーカイブSHAは重複保存しない。既存match_idへ異なる後出しデータを上書きしようとしたらエラーにする。各証拠のcapture時指紋とDBの追記禁止トリガーで改変防止。
- `match_history` の保存済みteam_id/構築SHAと凍結メタデータが違えば `VERSION_MISMATCH` として拒否する。復元用の `read()`、整合性チェック `verify()` を備える。
- イベント上限を超えたら古い分を捨て、`dropped_events` と `PARTIAL_CAPTURE` を明記。アーカイブの1MBサイズ上限などに違反した場合は保存しない。DB書込はトランザクションで一括処理し、エラーで部分保存しない。
- 生スクリーンショット、OCR全文、プロンプト、アクセストークン、秘密情報、未制限の`history`をP1証拠テーブルにコピーしない。GitHub/CIには合成データしか使わない。

## 3. GPTレビュー横断研究（追加通信なし）

既存の試合後GPTレビューのSQLite保存後、試合外の同一ワーカーで `AutonomousImprovementStore.aggregate_after_review` を呼ぶ。追加GPTリクエストは発生しない。最大40試合の同じteam_id・team_signatureについて、少なくとも3つの独立した終了済みレビューが揃い、反復した問題だけを未検証候補として保存する。同一試合内の複数指摘は別試合扱いしない。追加バッチには前回から3試合必要。

- 研究用テーブル `autonomous_improvement_batches` / `autonomous_improvement_candidates` は `PROVISIONAL_UNVERIFIED` / `GPT_HYPOTHESIS_UNVERIFIED` / `DEFERRED_UNVERIFIED` / `NOT_AUTHORIZED`。
- `history()` と `candidates()` は閲覧用。正式なLIVEマトリクスや構築CSVへ自動昇格しない。
- 研究中に失敗しても保存済み対戦後レビューを失敗にしない。候補が3試合分に達しない場合は追加課金も更新も行わない。

## 4. P1の検証・残るシステム上の制約

合成テスト `tests/test_offmatch_evidence.py` では時点凍結とその場の合法性未確認、GPT推奨/実行の分離、版不一致拒否、重複/改変拒否、サイズ/件数上限、既存DBのバックアップと非破壊性、旧レビューレコード保持、試合中の追加SQLite不使用を確認する。`tests/test_autonomous_improvement.py` では実際の試合後ワーカー接続と3独立試合・再処理禁止・仮説のLIVE非反映を検証する。

対戦中のAI反応時間は既存の機能と異なり、このP1のメモリ記録以外では増やさない。Windows CIの合格は**実ゲームの認識精度・行動確認・勝率改善の検証ではない**。

P2の当時利用可能だった全情報の厳格な反事実比較には、後から「実行が確認された」証拠と撮影時刻が不足するケースが残る。その場合は改善の優位性を推測して補完せず、P4でHOLDにする。検証不能な安全受け・確定KO・処理役の昇格は行わない。
