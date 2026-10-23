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
- 開発PRは**Draft**で作成し、実装・テスト・修正・ドキュメントの**全コミットを確定後**に **Ready for review** に変更します。Ready PRへの新しいコミットでも `synchronize` でWindows CIを再実行し、古い実行はキャンセルします。DraftのPRではWindowsビルドを実施しません。masterへの単なるpushでは配布CIを起動しません。
- CI失敗時はPRをDraftに戻して修正し、全コミット後にReadyで再検証します。**旧SHAのCI再試行を新SHAの検証として扱わないこと。** CIはPRのHEAD SHAを明示的にcheckoutし、成果物公開前に最新PR HEADが変わっていないか再確認します。配布ZIPには `BUILD_REVISION.txt` を同梱します。
- 開発エージェントの必須手順は [AI開発・CI検証方針](docs/AI_DEVELOPMENT_AND_CI_POLICY.md) と [AGENTS.md](AGENTS.md) を参照してください。最終報告では **PR最新HEAD SHA・CI対象SHA・CI結果** の一致を示します。
- GitHubのRequired status checks（失敗PRのマージ禁止）はリポジトリの保護ルールで別途有効化する必要があります。保護ルール未設定のときは、GitHub画面のマージボタンをCIが自動的に無効化するものではありません。

## 自動キャプチャによるフル対戦AI試験

設定でWindowsゲームウィンドウまたはADB端末、ゲーム画面のROI、ChatGPTアカウントとモデル、次戦用の自軍構築を登録します。**「設定 → ChatGPT」タブ内で直接**ログイン・モデル選択・AIの思考の深さ・画像1枚の認識実測・選出画面の単体テストができます。「ChatGPTでログイン・認識実測を開く」の別ウィンドウは不要です。
**対戦支援はメイン画面へ統合されています。** 別ウィンドウでのフル対戦テストは開きません。メイン画面の「対戦支援」タブ内で **「対戦開始（4ファイル事前読込）」 → 「ゲーム画面を自動キャプチャしてAI支援開始」** の順に操作します。設定済みモデル、思考強度、次戦用構築、キャプチャ先が対戦開始時に反映されます。設定不足時は開始を止めます。
設定 → 画面キャプチャの「ChatGPTタブで画像認識を実測」からも同じタブに移動できます。ChatGPTの通信・画像送信は明示操作のみ（保存済みログイン時のモデル一覧更新を除く）。設定を閉じる操作は、進行中のChatGPT診断ジョブが終了するまで保護されます。
最初の同意後、変更した画面だけを読み取り、保存ファイルを作らずChatGPTへ送信します。ゲームへのボタン入力は行わず、ユーザーが直接操作します。
選出画面の提案後は実際の3匹と初手をGUIで確認・確定してください。結果画面の勝敗を確認すると自動送信が停止します。「自動送信を停止」からいつでも停止できます。

試験用構築の6匹が同梱LIVEマトリクスの自軍6匹と違う場合、推奨は `TEST_ONLY` と表示し戦術的な有効性を主張しません。実戦用のマトリクス・ゲートを他構築へ流用しません。

## 単一メイン対戦支援UI（2026-10-23）

- 通常のメイン画面は唯一のライブセッションです。「対戦支援」タブに従来の選出確定・メニュー登録・手動再送・PREP・停止・JSONエクスポート機能を直接埋め込んでいます。別ウィンドウへの遷移はありません。
- 大きな推奨表示、フェーズ、キャプチャ中/再試行待ち、4ファイルロード、PREP、選出、モデル、現在の観測値は**同じ画面認識セッション**から更新されます。未検証の行動は `TEST_ONLY` と明示します。別の旧 `DesktopController` FSM では試合を開始せず、そこからライブ表示を上書きしません。
- 同一試合IDに対し、読み込んだ4ファイルとSHA-256をSQLiteへ固定保存し、画像本体を含まないライブの状態/イベント/提案を `NOTE` イベントに順次記録します。「試合履歴」タブはこのSQLite記録を読みます。選出・行動提案そのものは状態/表示用で、推薦しただけでゲーム内操作が行われたと推定しません。
- キャプチャ設定を対戦中に変更した場合は監視を停止します。画面キャプチャの対象を暗黙に変更しないため、対戦をリセットし、次戦の4ファイルをロードしてから再開してください。アプリ終了時も自動監視を止め、進行中AIワーカーの破棄を防ぎます。
- 初回の「画面選出」「技選択」「交代」の見た目の登録は現在の版でも必要です。認識較正の永続化、構築別LIVEマトリクス生成、40秒PREPの自動起動、実機の対戦通し検証は別の改善課題です。今回のWindows CIは**オフラインUI/状態/ログの検証**で、対戦中のレイテンシーや勝率の保証ではありません。

## スマホミラーリングを使用する実機キャプチャ確認（2026-10-23）

- Windowsネイティブ撮影は BlueStacks 限定ではありません。ユーザーが明示的に選んだスマホミラーリングアプリのウィンドウを読み取ります。スマホに直接ゲーム操作を送る機能はありません。
- メイン画面の「対戦支援」タブは**同じキャプチャ画像のライブプレビュー**を表示します。監視を開始し、最初の撮影が成功した後に画像と最終撮影時刻・送信ROIサイズを表示し、その後は描画のみ最大2回/秒更新します。プレビューのための別キャプチャ、AI送信、画像ファイル保存はありません。設定ROIより外側はプレビューに出しません。
- `CAPTURE_PAUSED` のときは直近に取得できた画像を残し、**撮影失敗理由と最終成功時刻**を表示します。監視停止後はプレビュー画像を消去し、現在画像と誤解させません。
- 設定での1枚プレビュー成功は、ライブ中にウィンドウID（HWND/PID/タイトル）が維持されることを保証しません。`WINDOW_IDENTITY_UNAVAILABLE` の場合は**対戦支援画面の「撮影対象ウィンドウを再選択（ミラーリング対応）」**を押して、列挙された現在のウィンドウから実際のミラーリング対象を明示選択します。指定したそのウィンドウを**対戦支援と同じROI・撮影経路**で検証できた場合だけ設定とセッションの撮影対象を更新します。成功後も自動監視は開始せず、ユーザーが明示的に再開します。
- 再接続により画像変化検出と選出/技/交代メニューの登録テンプレートが初期化されます。必要な画面登録を再実行してください。4ファイルの事前読込結果や、同一試合の判明情報は破棄しません。ゲーム内容・画像はローカルのプレビューにのみ渡し、ユーザーが別途AI送信を許可するまでは送信しません。
- 不明な他ウィンドウへの自動乗り換えを防ぐため、タイトルが同じだけでは再接続しません。実ミラーリングが再起動・画面再生成される場合は明示的な再選択が必要です。ライブの継続性は実機で追加検証してください。
