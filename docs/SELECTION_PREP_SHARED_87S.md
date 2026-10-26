# 選出→PREP共有87秒期限と高速化（2026-10-26）

## ユーザーの最新方針

90秒の選出画面の中で選出提案を表示し、同じ相手6匹・選出3匹でPREPを続ける。**PREPの独立40秒期限は廃止**。人が選出を入力する最後の3秒を確保し、**選出開始から87秒以内にPREP結果を画面表示**する。実対戦で相手の初手が現れたら全PREPを即停止し、対戦行動を優先する。目標は選出＋PREP 45秒以内（必須の合格閾値ではない）。ターン30秒の独立ベンチマークは維持。

## 実装の変更箇所

- `src/champions_assist/selection_test_dialog.py`: 87秒から選出経過時間を差し引き、認証・ワーカー起動・PREP結果のQt表示まで同じ期限で判定。PREP不合格は `prep_completed=False` としターン試験の前提にしない。
- `src/champions_assist/live_timing_test.py`: PREP呼び出しの上限を共有残余時間へ。検証可能な18対面（初手6＋控え12）と温存・切り返し・締め役3点をすべて要求。遅延結果は破棄する。
- `src/champions_assist/fsm.py`: 実セッション側も相手6匹を受けた時計から87秒後の締切に統一。別途ゲーム画面の残り時間が示される場合は3秒確保した短い方を用いる。 `S04_PREP_40S` は既存wireの互換性のため名前だけ残す。
- `src/champions_assist/real_battle_validation.py`: Windows実戦の記録も、選出画面表示開始からPREP終了まで共有87秒以内で判定。旧40秒方式の証拠と混同しないよう、実機証拠schemaをv2に更新。
- `src/champions_assist/match_replay.py`: AI出力を位置が確定した `lead_six[6]` と `bench_twelve[12]` のTAG=value文字列に圧縮。Pythonは既知の3匹×6匹への**結び付けと形式検証のみ**を担い、戦術選択を生成しない。6+12件を受け取れない場合は不合格。選出した自軍3匹だけとLIVEゲートの戦術安全関連節を入力し、不要なデータを圧縮する。モデル設定が `auto` の場合だけPREPは `low` を使う（明示選択した思考強度は尊重）。
- `masters/Pokemon_Champions_MC_live_decision_gate.txt`: タイミング部分をv6に更新。前のv5原本は `masters/Pokemon_Champions_MC_live_decision_gate_v5_archive_2026-10-26.txt` にアーカイブしSHAマニフェストを更新。選出と先制技・技固定・安全受けの判断条件は保持。

## 失敗・成功の解釈

`PREP_TIMING_PASS` は**構造とUI到達時間の合格**であり、Champions戦術判断の勝率や確定ダメージの検証ではない。`PREP_TIMING_FAIL` でも `source_status`、`source_reason`、`source_stage`、`source_model_elapsed_ms`、`source_prompt_chars` に元のPREPモデル応答を記録し、UI deadlineによる上書きと区別する。生の秘密情報・送信画像はログに含めない。

前回の14.24秒（選出）＋50.04秒（PREP表示）＝64.28秒という記録は、**時間だけなら87秒内**だった。ただし当時の40秒で破棄された出力の内部構造は記録されていないため、その記録から18対面の実際の完成可否は不明。今回の変更後に同じ画像で再計測すること。

## 受入テスト

```bash
docker compose run --rm dev python -m unittest tests.test_phase_2 tests.test_match_replay tests.test_live_timing_test tests.test_real_battle_validation -v
```

- PREPの完了が40秒超でも87秒共有期限内なら、18対面＋役割3点が正しければ合格。
- 共有期限・Qt画面反映期限超過、欠落対面、評価タグ不備、役割欠落は不合格。
- 最新HEADのWindows CI全体と必須ジョブ成功なしには**マージしない**。GitHub Actions無料枠を節約するため開発途中のPRはDraftのままとし、Windows CI実行・マージ・Releaseは別途必要時にまとめる。

## 限界と将来計測

現段階ではリポジトリ内のオフラインテストコードとUI検証契約を変更したのみ。Windows実機・API実測で45秒以内や完成率向上を確認したわけではない。修正後はモデル時間・出力完成率・UI到達時間・戦術上のエラーを同じ画像とモデルで比較し、必要に応じて入力圧縮の範囲や明示設定された思考強度を見直す。
