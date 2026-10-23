# 開発・CI・配布の現行引継ぎ（2026-10-23）

このファイルは**新しいChatGPT / Codexチャットや別エージェントが、過去のチャット履歴を持たずに作業するための索引**。新チャットでは本書だけで判断せず、以下の正本をGitHubの最新`master`と最新PRから実際に読み込む。

## 読む順番と正本

1. `AGENTS.md`：リポジトリ開発エージェントの必須ルール（Champions固有仕様とプライバシー）。
2. `docs/AI_DEVELOPMENT_AND_CI_POLICY.md`：Draft PR、CI検証、古いHEAD成功による誤マージ防止。
3. `docs/CI_MERGE_GUARD_AND_NEXT_CHAT.md`：マージ直前の厳格な最新SHA照合、引継ぎ初回コマンド。
4. `docs/WINDOWS_DEVELOPMENT_RELEASES.md`：**ActionsでWindowsビルド、ZIP配布はGitHub Releases**。開発用プレリリースの扱い。
5. `docs/screen_dataset_collection.md`：画面収集/グループ/訂正/PNG承認/学習用データの安全性。
6. `docs/Pokemon_Champions_WSL_Docker_Windows_MSI_Design_v2_6.md`：WSL+Dockerの開発とWindowsビルド・実行の分離。MSIは別作業。
7. `README.md`, `CODEX_START_PROMPT.md`：新規参加時の開発入口（古い日付の記述より正本とGitHub現況を優先）。

## 現行CI・配布・ストレージ

- リポジトリ：`hatsu07/pokemon_champions_assistant`（**privateのまま維持**）。
- `.github/workflows/windows-validation-artifact.yml`：Ready PR最新HEADのWindowsオフラインテスト、Assistant/Collector両Windows PyInstallerビルドと起動スモーク。**PRではActions Artifactsをアップロードしない**。workflow名とファイル名は歴史的名称のまま。
- `.github/workflows/windows-development-release.yml`：masterへのpush後、Windowsで再テスト・再ビルド・スモークし、`PokemonChampionsAssistant-DEVELOPMENT-windows-x64.zip`と`PokemonChampionsScreenDataCollector-DEVELOPMENT-windows-x64.zip`を**GitHub ReleasesのDEVELOPMENTプレリリース**として公開。初回のRelease公開処理を修正したのはPR #33。
- `.github/workflows/windows-development-release-cleanup.yml`：14日以上経過し**新しい3件から外れた**特定のDEVELOPMENTプレリリースと対応タグだけを毎日削除。通常Release、保護されたRelease、Draft、他のタグを削除しない。`scripts/prune_development_releases.py` はドライランがデフォルト。
- 旧`scripts/prune_actions_artifacts.sh`は過去のActions Artifactsだけの履歴整理用。新しいDEVELOPMENT ZIPには使用しない。

**2026-10-23時点の公開実測**：masterコミット`67715135e80190a7cea7aabbf2456c433b0a5b9a`に対するWindows DEVELOP Release workflow run `39968802394` は `completed/success`。プレリリース`development-windows-67715135e80190a7cea7aabbf2456c433b0a5b9a`へ2つのZIPを公開済み。以後は新チャットで必ず最新状態を再取得。

## 絶対に守るPRマージ条件

- Draftでコード・テスト・文書をまとめ、最後のコミットを確定してからReadyにする。
- Ready PR最新HEAD SHA、Actions workflow_run.head_sha、runnerがcheckoutしたSHAが同じこと。
- 最新SHAの**PR Windows workflow全体と必須job`desktop-windows-validation`がともに`completed/success`**。Failure、Skipped、Cancelled、部分成功、以前のSHA成功ではマージしない。
- `scripts/verify_merge_ready.py --pr <番号>`は追加ゲート。GitHubのMergeボタンが有効でも自動的にマージしてはいけない。
- ユーザーは**GitHub Proや有料branch protectionを希望していない**。保護は運用で徹底。問題が起きた場合はPRをDraftへ戻して修正し、最終SHAのCIを再実行する。
- masterへマージ後のGitHub Release公開結果は**PR CIの成功とは別に確認**する。リリース失敗を成功と報告しない。

## 収集アプリのUIと学習データの注意点

- Windowsの画面収集UIは`src/champions_assist/screen_dataset_collector.py`。テストは`tests/test_screen_dataset_collector.py`。全ファイル`screens/_UNLABELLED`は人間がラベルを付けて保存するまで学習データにしない。
- 画面幅変更でプレビューと操作ボタンをレスポンシブに配置し、横方向にはみ出さないようにする。
- グループ一覧のCtrl/Shift複数選択に対して、訂正ラベルを選び**「選択グループのラベルを一括訂正」**でまとめて仮訂正。**訂正時点ではPNGを保存しない**。画像単位の個別訂正と除外を優先して保持。グループのPNG保存は**「選択グループをPNG保存」1ボタンだけ**（旧AI候補承認・訂正して保存ボタンは廃止）。選択した1件以上を最後にグループ別ラベル・枚数で1回確認し、Noなら保存しない。
- AIの予測が不正解の場合があるので、**ラベル未確定画像、全画像除外、重複画像は保存前にブロック**。画像単位の訂正/除外を優先。成功したJPEGだけをPNGに変換し、未保存画像や他セッションの画像を保持。
- PNG、元の実画面、秘密情報、ゲーム内ログをGitHub・CI・Releaseに送らない。オフラインQt GUIテストと実機のBlueStacks上の挙動は区別する。

## 新チャットでの最初の依頼文

> `hatsu07/pokemon_champions_assistant` の開発を引き継いでください。まずリポジトリの `docs/CURRENT_DEVELOPMENT_HANDOFF.md` とそこにある全正本の開発・CIルールを読んで、masterと未マージPRの最新SHAとActions CI/Release状態を実際に確認してください。privateのまま、GitHub Pro不要。Ready PR最新HEADのWindows CI全体/必須jobがSuccessでない限り絶対にマージしない。Assistant/CollectorのDEVELOPMENT ZIPはGitHub Releasesで配布し、14日超の特定開発プレリリースのみ自動削除します。既存の学習データや実画像は公開しません。

