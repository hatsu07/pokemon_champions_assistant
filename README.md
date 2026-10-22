# Pokémon Champions Codex開発スターター v2.7 — WSL2/Docker開発 → Windows MSI配布

**これはCodexへ実装を依頼するためのリポジトリです。完成アプリでもMSIでもありません。**

## 今回の設計更新

- **開発**：WSL2 Ubuntu + Docker Desktop + VS Code/Codex。Python／OCR／OpenCV等をホストへ導入しない。
- **製品**：WindowsネイティブのPySide6アプリ。戦術はOpenAI API、画面観測・確定状態・SQLite・表示はローカル。プレイヤーがゲームを手動操作。
- **配布**：WindowsランナーのPyInstaller onedir + WiX Toolset per-user MSI。アプリ更新・アンインストールでも設定／対戦ログを保護。
- **正本**：`docs/Pokemon_Champions_WSL_Docker_Windows_MSI_Design_v2_6.md`（配布）、`docs/Pokemon_Champions_Contract_And_State_Reconciliation_v2_3.md`（API）、`masters/Pokemon_Champions_MC_live_decision_gate.txt`（戦術）。

## 最初の手順

1. ZIPをWSL2の`~/projects/`へ展開。`WSL_DOCKER_VSCODE_CODEX_SETUP.md`参照。
2. `docker compose build`、`docker compose run --rm dev python scripts/verify_package.py`を実行。
3. `docker compose run --rm dev python -m unittest discover -s baseline_v2_3 -p 'test_*.py' -v`。
4. WSL RemoteのVS Code + Codexでフォルダーを開き、`CODEX_START_PROMPT.md`を貼り付ける。
5. Phase 0/1から実装。Windows実機テストが無い段階で完了宣言しない。

## 収録

- `AGENTS.md`, `BUILD_PLAN.md`, `CODEX_START_PROMPT.md`, `IMPLEMENTATION_STATUS.md`
- `.devcontainer/`, `compose.yaml`, `requirements-baseline.txt`（Linux開発コンテナ雛形）
- `docs/`：過去の統合設計・FSM・API契約 + **新v2.6 Windows MSI設計**
- `contracts/`、`baseline_v2_3/`：形式スキーマ、参照実装、16件の基準テスト
- `masters/`：Champions固定マスターと効果ルール初期台帳（大半の効果は未整備）

**重要**：このZIPの環境でWindows MSIの生成やBlueStacks認識が確認されたわけではありません。既存テスト通過はランクマ自動認識を保証しません。APIキーや実対戦画像はリポジトリ／CIへ含めないでください。

## v2.7 — プロジェクト情報の補完
- `docs/V2_7_SOURCE_COMPLETENESS_AND_AUTHORITY.md`: 元ソース／再構成文書の収録状況と参照優先順位。
- `docs/Pokemon_Champions_Project_User_Guidance_Reconstructed_v2_7.md`: **プロジェクト固有指示のCodex向け再構成版**。設定原文と逐語一致は未保証。
- `docs/Pokemon_Champions_CODEX_OX_Postmatch_Method_v2_7.md`: 対戦後O/Xの評価・改善候補の審査方法。
- `knowledge/`: 環境取得ルール、歴史的環境スナップショット、旧設計とノウハウ所在。
- `private_history/`: バイト列無改変の対戦ログ・対戦データ（.xlsxを付与）。Git/CI/Docker build contextから除外。ZIPごとの公開共有は禁止。
- `masters/Pokemon_Champions_MC_team_2026-09-29.csv` は履歴。**現在構築は2026-10-03版のまま**。

最初に `docs/V2_7_SOURCE_COMPLETENESS_AND_AUTHORITY.md` と `AGENTS.md` を読んでから `BUILD_PLAN.md` に従う。

## Windows配布とPR検証（2026-10-22）

- 配布対象は **PokemonChampionsAssistant-windows-x64** のみです。
- GitHub Actions の Artifact をダウンロードすると1個のZIPになります。そのZIPを展開した直下の `PokemonChampionsAssistant.exe` を起動します。内部ZIPは含みません。
- 開発PRは**Draft**で作成し、機能がまとまった最後に **Ready for review** に変更します。この時にWindows CIが1回起動します。単なるコミット追加・PR作成・masterへのpushでは配布CIを自動起動しません。
- CI失敗時はマージせず原因を修正。失敗した実行の再試行、または最終修正後にDraft→Readyで再検証します。**必ず最終コミットSHAの成功を確認すること。**
- GitHubのRequired status checks（失敗PRのマージ禁止）はリポジトリの保護ルールで別途有効化する必要があります。保護ルール未設定のときは、GitHub画面のマージボタンをCIが自動的に無効化するものではありません。

## 自動キャプチャによるフル対戦AI試験

設定でWindowsゲームウィンドウまたはADB端末、ゲーム画面のROI、ChatGPTアカウントとモデル、次戦用の自軍構築を登録します。
**ChatGPT → フル対戦テスト → 新しい対戦を開始（4ファイル事前ロード） → 「ゲーム画面を自動キャプチャしてAI支援開始」** の順に操作します。
最初の同意後、変更した画面だけを読み取り、保存ファイルを作らずChatGPTへ送信します。ゲームへのボタン入力は行わず、ユーザーが直接操作します。
選出画面の提案後は実際の3匹と初手をGUIで確認・確定してください。結果画面の勝敗を確認すると自動送信が停止します。「自動送信を停止」からいつでも停止できます。

試験用構築の6匹が同梱LIVEマトリクスの自軍6匹と違う場合、推奨は `TEST_ONLY` と表示し戦術的な有効性を主張しません。実戦用のマトリクス・ゲートを他構築へ流用しません。
