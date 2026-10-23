# Codexへの最初の依頼（WSL2 + Docker開発／Windows MSI配布 v2.7）

このリポジトリの `docs/V2_7_SOURCE_COMPLETENESS_AND_AUTHORITY.md`、`docs/Pokemon_Champions_Project_User_Guidance_Reconstructed_v2_7.md`、`docs/Pokemon_Champions_CODEX_OX_Postmatch_Method_v2_7.md`、`knowledge/README.md`、`AGENTS.md`、`docs/AI_DEVELOPMENT_AND_CI_POLICY.md`、`docs/CI_MERGE_GUARD_AND_NEXT_CHAT.md`、`BUILD_PLAN.md`、`WSL_DOCKER_VSCODE_CODEX_SETUP.md`、`docs/Pokemon_Champions_Contract_And_State_Reconciliation_v2_3.md`、`docs/Pokemon_Champions_Integrated_Architecture_v2_0.md`、`docs/Pokemon_Champions_WSL_Docker_Windows_MSI_Design_v2_6.md` を読んでください。

目的：Windows上のBlueStacks 5で動作するPokémon Championsシングルランクマッチを、**画面監視だけでリアルタイム支援するアプリ**を開発してください。戦術判断は必ずOpenAI APIが担当し、Pythonは画面認識、状態計算、効果エンジン、イベント記録、API入力・回答の検証、表示、SQLite保存のみを担当します。ゲームの操作自動化は実装しません。

**開発環境を汚さないことが最優先です。** 開発コードはWSL2 Linuxファイルシステムにあり、依存ライブラリは `.devcontainer/Dockerfile` と `compose.yaml` で隔離します。**Windows/WSLホスト上で `pip install`・Python venv作成・アプリ用 `apt install` を行ってはいけません。** テスト・CLI・パッケージの実行は必ず `docker compose run --rm dev ...` または Dev Container のターミナルで行ってください。既存の環境が壊れるようなコマンド、Docker全体の prune、ホストディレクトリを広範に削除する操作はしないでください。

**製品実行はWindowsネイティブアプリ、最終配布はper-user MSI（PyInstaller onedir + WiX、Windows runnerで生成）とします。** 製品にWSL/Docker/Pythonの直接インストールを要求しないでください。リリース時までにPhase 7のWindows実機テストが必要です。

まずPhase 0 と Phase 1 を実装してください。プランだけで終わらせず、実際にコードを書いてテストしてください。

- `docker compose run --rm dev python scripts/verify_package.py` を実行する。
- `docker compose run --rm dev python -m unittest discover -s baseline_v2_3 -p 'test_*.py' -v` で既存16件の成功を確認する。
- Pythonモジュール `src/champions_assist`、オフラインCLI、設定、`tests/` を追加する。
- SQLiteの追記型イベント・ターン終端の重複防止・効果の二重適用防止・訂正イベントの追記・当時のAPI入力の不変保持・再起動後のリプレイを実装する。
- 未確認のChampions固有仕様を他作品から流用せず、未整備データは未確定として扱う。
- UI・OCR・BlueStacks連携は実機データが無い限りテストスタブとし、実際に動くと主張しない。
- 変更ファイル、Docker内のテスト結果、残課題、次の実行コマンドを `IMPLEMENTATION_STATUS.md` に記録する。

正本のAPI wireは `2.3.0`、効果レコードは `0.2.0`。4ファイルの実読込を試合開始時に行い、対戦中の再読込はしない。18対面PREPを時間内に評価する設計を維持。APIは戦術判断だけ、ローカルは合法手判定だけ。**ソースを直して終了せず、必ずDocker上で全テストを実行してください。**

## v2.7 追加タスク（Phase 0/1に含める）
- 初回に `python scripts/verify_v27_assets.py` をDocker内で実行し、旧構築、LIVE役割定義、環境方針、プライベートワークブックが存在し、原本SHA256と一致することを確認。
- ログExcelは**元の値を変えず**、読み取り専用の列・シート調査までに留める。自動で「戦績」や「相手構築」と推定せず、マッピング提案をレポートする。Git・CI・OpenAI APIに生ログを送信しない。
- O/Xはレビュー機能の仕様として扱い、LIVEの戦術判断や時間制限に追加しない。
- アーカイブ環境スナップショットは最新情報として使わず、オフライン分析で取得日とレギュレーションを確認する。
- このパッケージの運用ルールをLIVE判断ゲートにコピーして二重管理しない。


## 2026-10-23 CI・マージ事故再発防止
- 必須Windows PR CIは最新PR HEADで全テスト・両Windows GUIビルド・起動スモークの成功を検証。Artifact配布は明示的な手動実行と分離（Actions容量不足のため）。
- CI全体がcompleted/success以外なら絶対にマージしない。GitHubのmaster保護・Required status check・管理者例外禁止が有効であることを確認できない場合も、マージは停止する（2026-10-23時点でmasterは未保護）。
- 新チャットでは `docs/CI_MERGE_GUARD_AND_NEXT_CHAT.md` とGitHubの現状を必ず再読込する。必要なら `scripts/verify_merge_ready.py` による読み取り専用ゲート検証を行う。
