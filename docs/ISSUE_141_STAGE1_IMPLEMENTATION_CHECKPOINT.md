# Issue #141: 第1段階②〜⑥ — ソフトウェア実装／未認証境界

関連: [#141](https://github.com/hatsu07/pokemon_champions_assistant/issues/141), [PR #202](https://github.com/hatsu07/pokemon_champions_assistant/pull/202), [PR #204](https://github.com/hatsu07/pokemon_champions_assistant/pull/204)。
**第2段階（Android/Windows実ゲーム・独立動画受け入れ）は [#203](https://github.com/hatsu07/pokemon_champions_assistant/issues/203) に分離。**

## 第1段階で実装したソフトウェア

### ② 固定Championsマスターからの実エンジン静的チェック

- `static_move_compatibility.py`：現行登録構築6匹・24技を、凍結済みChampions技・習得技・使用可能ポケモンマスターとSHA照合し、型/威力/命中/PP/優先度を抽出。曖昧・改竄・未対応の場合は全体拒否。
- `scripts/report_phase2_static_move_compat.py`：試合外CLI。 `local_simulator/pinned_move_static_comparison.cjs`：固定Showdown Champions Dexと24技の静的値を比較。実エンジンSHAをGitHub Actionsで固定。
- 静的属性が一致しても、技の追加効果・メガルカリオZの変化後仕様・実ゲーム合法性は証明しない。登録CSVへ推測で6+24のShowdown IDを書き込まない。

### ③ 1ターンの条件付き計算

- 既存の `observed_full_grid_diagnostic.py`、`shadow_runner.py` と `one_turn.py` が、**明示された完全な合成Battle**とjoint仮説、エンジン発行の相手選択肢×観測された自軍合法手を全枝比較。見落とし・期限切れ・revision変化・不明な状態は局面全体 `NO_RANKING`。
- 実キャプチャのOCRだけでは完全なBattle・相手の隠れた型・乱数・PPを復元できないため、Qtの実戦観測からのローカル診断は当面棄権する。これは意図的な安全境界であり、実戦で有用なローカル行動選択が完成したという主張ではない。

### ④ 1回の6行取得による18対面の条件付きPREP

- `conditional_preview_pipeline.py`：選出3＋初手を凍結LIVEマトリクス6行から算出し、その同じ6行でPREPを開始。 `prep18_audit.stage_prep18(..., cached_matrix_rows_six=...)` は凍結源と1行ずつ比較し改竄を拒否。
- `conditional_prep_builder.py`：初手6＋控え12＝18対面について、登録済みの技だけを引用し、速度・先制/技固定・メガ・処理役と受けの区別・暫定の温存/切返し/締め役を圧縮。未知のKO/相手最大打点/安全後投げを捏造しない。出力は `DRAFT` であり、`COMPLETE` とは区別する。
- Qt `MatchReplayDialog._capture_shadow_prep_draft()` も選出表示直後に明示opt-inのみ実施。すでにフリーズされたmatrix_indexを直接参照し、追加GPT通信・6行再取得・LIVE提示変更なし。相手6匹未確定時は省略する。

### ⑤ 実Qt→同一revisionのLOCAL_SHADOW並走

- `live_shadow_dispatch.py`：実際のGPT要求キューを使い、match/request/epoch/revision/4ファイルSHA/残り締切を固定。`UNKNOWN` の画面・未確認合法手・未観測PPを勝手に認証しない。
- `ui_shadow_dispatcher.py`、`parallel_shadow_session.py`：UIに初期OFFの試験チェックを追加。opt-in時だけ私的SQLite v2フォルダを作成。local futureをGPTと独立起動し、**同じGPT要求と同じrevision・応答画面**に結びついた結果だけを非同期保存。取消・新しいrevision・別画面・期限後の古い助言・`TEST_ONLY`/BLOCKEDはペア認証しない。
- SQLiteはSHA・固定理由コード・時間等のみ。実GPTの原文、画面画像、OCR・認証情報・未認証技名は保存しない。GPTの本番提案・操作・表示を変更しない。完全Battleがない局面はlocal `NO_RANKING`、GPT側は `GPT_UNVERIFIED` として記録。

### ⑥ ソフトウェア品質・Golden／遅延監査

- `scripts/report_phase2_shadow_software.py`：対戦**終了後のみ**使用。SQLite v2と任意のGUIイベント書き出しを、match/request/epoch/revisionで照合。失効・棄権・入力期限・P50/P95/P99・Golden8の未検証件数を別々に集計。
- `shadow_quality_gate.py` の既存Golden8・棄権率の境界を保持。全件棄権・synthetic-only/Qt単調時刻だけで実戦有効性を合格にしない。出力は常に `phase2_accepted=false`、本番LOCAL推奨は無効。

## テストと試合後CLI

- Linux Phase 1隔離テスト：Qtを要求しないsource/モデル/同一revision/CLI契約を検証。
- 固定Showdown実エンジン：実Dexの6+24候補と静的24技を照合する別workflow。
- Windows必須検証：`test_issue_141_stage1_qt_shadow_wiring.py` を含むGUIイベント接続、SQLiteと既存GPT非干渉、最新HEAD Windowsアプリのビルド/スモーク。Linux隔離ランナーにはQtの `libEGL` が無いため、Qt GUIテストはWindowsに割り当てる。

試合外でのみ実行:

```bash
python scripts/report_phase2_static_move_compat.py --masters-dir masters
python scripts/report_phase2_shadow_software.py --shadow-sqlite path/to/phase2-shadow/comparison-v2.sqlite --gui-export path/to/gui-timing-export.json
```

## PR #204 レビュー P2 対応（実ゲーム証拠ではない）

- **P2-1 GUI表示監査の誤昇格**：`AI_RESULT_DISPLAYED` は受信イベントであり、表示の証拠にしない。`scripts/report_phase2_shadow_software.py` は `audit_gui_timing_export` の厳密な要求順序・キャンセル判定を利用し、同一 `match_id/request_id/turn_epoch/state_revision/decision_window_id` の `ACTION_GUI_RENDERED` まで存在し、未表示turnが0のときに限り `SOFTWARE_QT_PAIR_COMPLETE_NOT_GAME_CERTIFIED` とする。描画欠落・別request・旧turn/revision・キャンセルを負例に追加。
- **P2-2 Shadow失効とSQLite書込の競合**：同一pool上のwrite taskと取消タスクを直列化し、トランザクション内のrevocation fenceを追加。UIのOFF/revision更新はSQLite完了を待たず、短いメモリlockだけで失効を宣言する。書込途中の取消が最終チェック後に割り込んでも、非同期の完全一致キー削除が直後に走り、失効行を監査成功として残さない。UI側はディスク完了待ちをしないため、`cleanup_future` を完了した後でのみオフライン監査の最終状態を確認する。正常に完了済みの過去行は後日のOFFで削除しない。
- **新規 P2-3 SQLite削除失敗のfail-closed**（再レビューで発見）：`sqlite3.OperationalError` を含むSQLite例外を確実に捕捉し、専用worker上で限定再試行。消去できなければ `cleanup_future=REVOKE_STORAGE_FAILED` とする。失効した記録が生SQLiteに残るケースも想定し、`_finish` が保存完了をメモリロック下で確定した**後だけ**、別トランザクションの `phase2_shadow_finalized_v1` に完全なレコードハッシュに紐づく受理証拠を永続化。保存途中の失効や削除失敗があっても受理証拠のない行をオフラインレポートは採用しない。未確定行数を `sqlite_unfinalized_or_revoked_count` として出力し、CLIは終了コード1を返す。一般的な `read_shadow_comparisons()` は生データ閲覧、`finalized_only=True` はソフトウェア確定記録のみを返す。UIはSQLiteを同期待機しない。
- 実SQLiteロックを保持した状態でOFF→2回の削除失敗→ロック解放後も生行が残る負例、COMMIT直前の失効負例、および正常な確定記録・後日のOFFで過去行を保持する正例を回帰テストに追加。削除失敗した生行は監査証拠に昇格しない（削除の永久成功を保証するものではない）。
- 固定Showdownとの静的比較では24枠中23枠一致、ミミッキュ「かげうち」のPP不一致が観測されている（2026-11-03 PR #204レビュー）。`PINNED_STATIC_FIELDS_DIFFER` は**対応範囲の不一致**であり、元データを推測で上書きしない。CI成功はエンジンとの完全互換性を意味しない。

## **合格と未達を混同しない**

- 本PRの追加コードは「未確認の状態では棄権する条件付き実装」であり、実ゲームで1ターンを正しく計算した、すべての18対面を戦術的に評価した、LOCALの適用率が正であるという証拠ではない。
- 別Issue #203には実Android→Windowsキャプチャ・動画/独立採点・ゲーム固有の技/特性/メガ互換性・実表示期限・不適用/誤判断率の受け入れを記録。対応局面が安全に確定するまでLOCAL_SHADOWは診断専用。
- PR #204をレビューする際の判定対象は **コードの安全性、最新HEADのLinux/固定Showdown/Windows CI、真の同一request/revision、既存GPTへの非干渉**。#141全体の完了・#142本番切替には実ゲーム受け入れが別途必須。
