# Issue #102 / B — 対戦後レビューの独立E2Eと本番可視化

このPRは [Issue #102](https://github.com/hatsu07/pokemon_champions_assistant/issues/102) の **Bのみ**。
勝敗自動判定（A）とターンOCR入力圧縮（C）は未実装として残す。実ゲームでの勝敗検出に合成試験は代用できない。

## GUI操作

- 「テスト全般」→「対戦後レビューE2Eテスト」は独立した6番目のタブ。
- 「オフライン模擬試験」：人工の `phase=ENDED,result=WIN` と `SYNTHETIC_POSTMATCH_TEST_NOT_GAME_EVIDENCE` を使い、毎回新しい一時SQLiteへ記録する。実ゲームキャプチャなし。外部モデル呼び出しなし。
- 「実APIレビュー試験（有料通信）」：画面上の料金・通信注意を確認して、利用者が明示的に押したときだけログイン済みモデルへ送信する。同じ一時SQLiteを使用。認証・通信失敗は FAIL。API結果は **未検証仮説**。
- 両モードとも、本番の `MatchupLearningStore.record_finished()` と `postmatch_learning.request_review()` を実行後、SQLiteの `match_history` / `postmatch_reviews` の保存内容・スキーマ・試合IDを読戻す。進捗は「記録保存→AIレビュー中→SQLite保存確認→完了/失敗」で表示。試験終了時に一時DBを削除。
- 既存「1対戦統合テスト（実ゲーム画面）」は独立したままで、スクリーンキャプチャ・状態・FSM・戦術ロジックを流用しない。

## 本番の終了後レビュー

`DesktopMainWindow._on_unified_state()` の `MATCH_END` が到着すると、実戦記録 `record_finished()` に成功した試合は追加AIレビューを起動する。モデル未設定、ワーカー使用中、重複結果、通信失敗は終了済みと偽表示せず、SQLiteに残した未処理状態と理由を画面に示す。

「対戦後レビュー」タブには対象試合ID・保存先・状態・保存済みレビュー本文（未検証ラベル付き）を表示する。「未処理レビューを再試行」は対戦外のみ、保留中の試合にだけ使用できる。戦闘中の行動AIを新しいレビュー処理で妨げない。レビューの提案をLIVEマスターへ自動昇格しない。

## 自動回帰

- `tests/test_postmatch_review_e2e.py`: 合成終了→SQLite保存→疑似AI→SQLite読戻し、API非実行、別DB不変、Qtワーカー解放、モデル未設定、**実際の `DesktopMainWindow._on_unified_state()` に `MATCH_END` を入力**した本番連携と重複挿入阻止、保存本文読戻し。
- `tests/test_diagnostics_tabs.py`: 6つのタブと「1対戦統合テスト」の従来テストを維持。
- Windows ready-PR CIとmaster開発ビルド検証に新テストを登録する。検証を通過した場合でも実API試験・実機結果検出・Windows GUI手動操作の成功を保証しない。

実行例（リポジトリ指定のDocker dev環境）:

```sh
docker compose run --rm dev python -m unittest tests.test_postmatch_review_e2e tests.test_diagnostics_tabs -v
```

CI成果報告は最新PR HEAD SHAのworkflow **全体** と Windows必須job が `completed/success` になってから行う。追加コミット・CI失敗時は再検証し、合格前のマージ禁止。
