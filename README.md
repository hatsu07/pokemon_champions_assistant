**画面分類の現行仕様：** 操作3画面（TEAM_SELECTION / TURN_ACTION / SWITCH_SELECTION）＋情報更新候補BATTLE_EVENT＋OTHERの5分類。旧7分類のPNG移行方法とモデル互換性は [5分類移行手順](docs/SCREEN_LABELS_FIVE_CLASS_MIGRATION.md)。元画像や過去モデルを自動で削除せず、旧ONNXは新モデルとして使わない。

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

## Windows CI・DEVELOPMENT ZIP配布（2026-10-23）

新チャットへ開発を引き継ぐ際の読み順と最新注意点は [開発引継ぎインデックス](docs/CURRENT_DEVELOPMENT_HANDOFF.md) に集約しています。

画面収集のグループ操作は、Ctrl/Shift複数選択→必要なら「選択グループのラベルを一括訂正」→「選択グループをPNG保存」1ボタン→ラベル・枚数の最終確認に統一しています。詳細は[画面データ収集手順](docs/screen_dataset_collection.md)。


**GitHub Actions ＋ GitHub Releases**。PRのWindows CIはすべてのオフラインテスト・Assistant/Collector両方のPyInstallerビルドとGUIスモークを実行するが、保存容量に制約のあるActions Artifactsへはアップロードしない。PRは最新SHAでworkflow全体と必須jobの`completed/success`を確認するまでマージ禁止（有料GitHub Proやブランチ保護は不要）。

masterへマージすると別の[Windows DEVELOPMENT GitHub Release workflow](.github/workflows/windows-development-release.yml) が**masterのSHAを再ビルド・再スモーク**し、2つのZIPを[GitHub Releases](https://github.com/hatsu07/pokemon_champions_assistant/releases)の**DEVELOPMENT_ONLYプレリリース**として公開する。各ZIPの展開直下にEXEと`_internal`があり、二重ZIPはない。Release公開成功はmaster用workflowと2ZIPの実在で別途確認する。

- `PokemonChampionsAssistant-v0.1.0-dev.N-windows-x64.zip`
- `PokemonChampionsScreenDataCollector-v0.1.0-dev.N-windows-x64.zip`

リリース名は `Windows DEVELOPMENT v0.1.0-dev.N` です。**[GitHub Releases](https://github.com/hatsu07/pokemon_champions_assistant/releases) 最上部の開発版が最新**で、番号はWindows配布ワークフローの実行ごとに自動で増えます。SHAはZIP同梱の `BUILD_REVISION.txt` に記録。旧SHA形式のReleaseは既存のリンクを維持します。

[古いDEVELOPMENTリリースを整理するworkflow](.github/workflows/windows-development-release-cleanup.yml)が、作成後14日以上かつ新しい3件以外の**特定開発プレリリースのみ**毎日削除する。通常の正式リリース・他タグ・Draftは削除しない。[詳細運用・手順](docs/WINDOWS_DEVELOPMENT_RELEASES.md)。WSLの`gh`で古い**Actions Artifacts**を消す方式は履歴用途に変更した。

新方式はGitHub Releasesの保存機構を使い、Actions Artifactsストレージ上限に影響されない。ただしWindows Actions実行時間、GitHub認証・権限・GitHub側障害には別途依存する。正式MSI、学習済み画面モデル、実機でのランクマ認識精度は未検証。以前のPR #20などのActionsダウンロード案内は**過去の成果物向け**であり、最新版はReleasesから取得する。

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
- 通常運用の画面登録は不要とする実装を開発中です。ただし現段階の自動判定は暫定的な変化検出であり、専用学習済みモデルによる精度保証はありません。構築別LIVEマトリクス生成、40秒PREPの自動起動、実機の対戦通し検証は別の改善課題です。今回のWindows CIは**オフラインUI/状態/ログの検証**で、対戦中のレイテンシーや勝率の保証ではありません。

## スマホミラーリングを使用する実機キャプチャ確認（2026-10-23）

- Windowsネイティブ撮影は BlueStacks 限定ではありません。ユーザーが明示的に選んだスマホミラーリングアプリのウィンドウを読み取ります。スマホに直接ゲーム操作を送る機能はありません。
- メイン画面の「対戦支援」タブは**同じキャプチャ画像のライブプレビュー**を表示します。監視を開始し、最初の撮影が成功した後に画像と最終撮影時刻・送信ROIサイズを表示し、その後は描画のみ最大2回/秒更新します。プレビューのための別キャプチャ、AI送信、画像ファイル保存はありません。設定ROIより外側はプレビューに出しません。
- `CAPTURE_PAUSED` のときは直近に取得できた画像を残し、**撮影失敗理由と最終成功時刻**を表示します。監視停止後はプレビュー画像を消去し、現在画像と誤解させません。
- 設定での1枚プレビュー成功は、ライブ中にウィンドウID（HWND/PID/タイトル）が維持されることを保証しません。`WINDOW_IDENTITY_UNAVAILABLE` の場合は**対戦支援画面の「撮影対象ウィンドウを再選択（ミラーリング対応）」**を押して、列挙された現在のウィンドウから実際のミラーリング対象を明示選択します。指定したそのウィンドウを**対戦支援と同じROI・撮影経路**で検証できた場合だけ設定とセッションの撮影対象を更新します。成功後も自動監視は開始せず、ユーザーが明示的に再開します。
- 再接続により画像変化検出と補助的な選出/技/交代メニュー登録テンプレートが初期化されます。通常の自動認識に手動登録は不要です。4ファイルの事前読込結果や、同一試合の判明情報は破棄しません。ゲーム内容・画像はローカルのプレビューにのみ渡し、ユーザーが別途AI送信を許可するまでは送信しません。
- 不明な他ウィンドウへの自動乗り換えを防ぐため、タイトルが同じだけでは再接続しません。実ミラーリングが再起動・画面再生成される場合は明示的な再選択が必要です。ライブの継続性は実機で追加検証してください。

## キャプチャ更新中にAI支援が進まない場合（2026-10-23）

- **画面キャプチャのプレビューが動いていることは、AIへの画像送信を意味しません。** 通常は手動の選出/技/交代画面登録なしで、ローカル画面判定と画面変化検出がAIへの候補送信を起動します。訓練済みONNXモデルがない配布物では、軽量な画像変化の候補検出を使い、ChatGPTが画面の種類を確認します。
- メイン画面上部の **AI通信状態** と対戦支援タブ内の **メニュー登録** を確認してください。**AI処理を起動中**はアプリが要求処理を起動したこと、**ChatGPT認証確認中**は送信前、**AI画像送信処理中**は画像を用いた推論呼び出しを実行中、**AI応答受信**は結果が返ったことを表します。いずれも推奨の戦術的妥当性やHTTP通信成功を無条件に保証するものではありません。
- 選出画面が既に映っていても、**監視開始時の最初の画像から自動候補判定**します。必要時のみ「選出画面を登録」で手動補正できます。すでに同じ画像が送信済みなら重複しません。
- 送信を試行した場合、ログに `AI_REQUEST_QUEUED`（フレーム数・試行番号）が記録されます。この行は実際のサーバー受信証明ではなく、**AI実行タスクを開始した事実**です。完了時の `TEST_ONLY`、`BLOCKED`、`ERROR` 等で結果を確認します。
- テストで不明な場合は「現在までの重要画像をまとめてAIへ送信（手動保険）」から明示送信もできます。追加のキャプチャはその操作に限定されます。
- 画面再接続時はローカル候補判定と補助メニュー登録が初期化されます。手動登録せず自動認識を再開できます。

## 画面判別のCPUモデル・データ準備（PR #20）

**現在の配布版は、実画面から学習したONNX分類器をまだ同梱していません。** 従ってCPU分類器の精度は未実測で、アプリの「学習済みモデル未配置」は正しい診断です。毎回の手動登録を必要としないために、当面は `AutomaticVisualProbe` が画像の変化や初回フレームを検出し、ChatGPTで画面種別を確認します。画面認識が成功する保証はありません。

- 訓練済み・検証済み重みがある場合は `champions_assist/data/champions_screen_classifier.onnx` と `champions_assist/data/champions_screen_classifier.json` をアプリに同梱します。実行時は **ONNX Runtime CPUExecutionProvider** が既定です。初期段階でGPU用の別モデルを要求せず、CPU/GPUで同じONNXを使用する設計です。`auto` もCPUを優先します。GPUは今後の計測で必要と確認した場合のみ明示選択可能な拡張ポイントであり、現行の設定画面にGPU切替は追加していません。
- モデルは `MATCHMAKING`, `TEAM_SELECTION`, `MOVE_MENU`, `PARTY_MENU`, `BATTLE_EVENT`, `RESULT`, `UNKNOWN` の7画面種別を識別します。これは画面判別だけで、ポケモン/技/HPの読み取りや戦術判断は行いません。`UNKNOWN` や低信頼度の場合は無理にメニュー扱いしません。
- モデルを同梱する前に、スマホミラーリング環境の実画面（許可されたキャプチャ）から **対戦セッションごとに分離した** 学習/検証データを作ります。非公開の `private-training/screens/<LABEL>/<session_id>__<frame>.png` に整理します。生画像は通常のログ・GitHub・CIへアップロードせず、学習時のみローカルの明示操作で保存します。
- 学習は `./scripts/train_screen_classifier_docker.sh` で起動します。PyTorch/TorchVision/ONNX/ONNX Runtime は **学習専用の Docker `trainer` イメージ** にのみ入り、Windows/WSLホストや通常の `dev` イメージには導入されません。画像はワークスペース外の `%LOCALAPPDATA%` から読み取り専用でマウントし、trainer のリポジトリ本体も読み取り専用です。生成物は無視対象の `private-training/output` にのみ保存します。まず `--validate-only` で検査し、既定のCPU学習は重みをダウンロードしません。`--pretrained` はImageNet重みの取得を明示的に許可する場合だけ指定します。
- スクリプトは7ラベルごとの画像数・独立セッション数・画像読取可否を出力し、最低4対戦かつ各ラベル2対戦以上を要求します。同一試合の類似フレームが学習・評価の両方に入らないよう**セッション単位で分割**し、両側のラベル不足は不足ラベルと追加すべき対戦を表示して中断します。`_UNLABELLED` のJPEGは学習に含めません。
- 初期検証の合格ゲートは、独立検証5セッション以上、選出画面100フレーム以上・再現率99%以上・適合率99.5%以上、他の種別も30フレーム以上・再現率95%以上です。これは**開発ゲートの案**であり、本番の90秒制限を満たした証拠にはなりません。
- スクリプトの生成物は初期状態で `validated=false` です。基準達成時に限り `--approve-if-thresholds-pass` を指定できます。モデルのSHA-256や画面分類ラベルが一致しない場合、アプリはモデルを読み込まず、安全なフォールバックと警告を使います。検証したモデルだけをリリースの成果物に同梱してください。
- 実機で選出検出まで1秒以内、CPU推論P95 50ms以下、見逃し率/誤検出率、AIへの重複送信を記録し、**モデル追加後の別SHAでWindows CIとミラーリング実機検証**を必ず実施します。このPRでその目標を達成したとは主張しません。

### 開発用・正式リリース用の区別

このPRのビルドは **DEVELOPMENT_ONLY** と明示し、ユーザーへの正式配布とは区別します。モデル同梱なしで開発用アプリを起動できても、その状態を「画面判別モデル搭載版」として公開してはいけません。CI内の `scripts/check_screen_model_release.py` は開発用診断を表示し、正式リリースの前には **`python scripts/check_screen_model_release.py --release`** が成功することを必須条件とします。未配置・未検証・ハッシュ不一致・推論環境不備のいずれでも正式リリースチェックは失敗します。収集GUIは未ラベル画像のディスク保存と人手ラベル付けを提供しますが、実データでの学習精度・速度は未検証です。

## 画面認識モデル用学習データ収集アプリ（開発用・PR #21）

画面認識をユーザーの手動登録に依存しない正式版にするため、スマホミラーリング画面の**正解ラベル付き実画像**を収集する専用Windows GUIを追加しました。

GitHub Releasesの `PokemonChampionsScreenDataCollector-v0.1.0-dev.N-windows-x64.zip` を展開し、`PokemonChampionsScreenDataCollector.exe` を実行します。対戦支援アプリとは別のローカルアプリで、保存済みキャプチャ設定とROIを共有します。未ラベルJPEGのローカル自動保存には明示的な許可が必要です。標準5fps（2/5/10fps選択）で150枚に達しても自動収集を止めず、2/5/10GBの容量上限と最低空き容量を確認します。学習用の正解ラベルPNGに変換するのは人間が確定した画像のみです。ゲーム操作・AI/クラウド送信は行いません。モデル学習済みという意味ではありません。

**操作手順・ラベル定義・プライバシー・学習への引き渡し：** [docs/screen_dataset_collection.md](docs/screen_dataset_collection.md)

このツールの完成後に、複数試合のラベル済み実画面で軽量CPU分類モデルを学習し、独立した試合で精度と画面検出速度を検証します。モデル同梱済みの正式版とは区別します。

### 連続画面収集の改良（PR #22）

旧版の「1秒間隔・メモリ150枚で強制停止」を解消しました。**5fps標準（2/5/10fps選択）、未ラベル画像をローカルJPEGへ自動保存、対戦後に100枚ずつ確認して人手ラベル付け**する方式です。従来の学習済み正解ラベルPNGはそのまま保持します。容量上限・残容量不足時は理由を明示して停止し、自動で過去画像を削除しません。詳細は [docs/screen_dataset_collection.md](docs/screen_dataset_collection.md) を参照してください。

### 開発用：自動ラベル候補と一括確認（PR #25）

`PokemonChampionsScreenDataCollector` には、対戦終了後に未ラベルJPEGをグループ化し、実験用ONNXモデルで7画面ラベルの**候補だけ**を推定するオフラインGUIを追加しました。グループの先頭・中央・末尾画像を目視確認して一括承認・訂正した場合だけ、従来の人間確認済みPNGに変換します。 `validated=false` のモデル利用は開発用収集アプリのみで、通常の対戦支援側は未検証モデルを拒否したままです。

学習にはマージ済みのGPU学習スクリプトを引き続き利用します。承認済みデータの再学習はWindows収集アプリから明示的にWSL/Docker GPU学習を開始し、ログを確認できます（WSLのLinuxリポジトリパスを入力）。`--archive-output` でモデルを個別保存し、モデルは自動昇格しません。操作と実験モデルのWindowsへのコピー方法は [収集・ラベル付け手順](docs/screen_dataset_collection.md) を参照してください。

グループ内の誤ラベルを見つけるため、先頭・中央・末尾3枚だけでなく**全フレームのサムネイル一覧**を表示します。部分訂正・PNG保存対象からの除外が可能で、グループごとの全画像確認チェックがない場合は一括確定できません。詳細は上記の収集手順を参照してください。

自動ラベル付けのPNG一括保存・従来の手動PNG保存後は、保存済みフレームだけが現在のグループから除かれます。**未処理グループはAI再推論なしでそのまま続けてレビュー**でき、別試合の切替や明示的な再解析まで作業状態を保持します。

自動ラベルの修正操作では、**全画像サムネイルの直下**に専用の「グループ／選択画像の訂正先ラベル」欄があります。選択画像だけの訂正、グループ全体の訂正に共用します。ページ下部の手動保存用ラベル選択は別操作のため独立しています。

複数の自動ラベルグループを**まとめてPNG保存**できます。各グループの画像を確認・必要に応じて部分訂正し、確認済みチェックを付けたうえでCtrl/Shiftで複数グループを選び「選択した確認済みグループをまとめて承認・PNG保存」を押してください。AI予測ラベルの異なるグループを混ぜても各グループ固有のラベルを維持します。グループ全体のラベルは「グループラベルを訂正（保存は後で）」で仮訂正可能です。未確認・未確定ラベル混入時は一括保存を中断します。

グループが多数ある場合は**「確認済みグループをすべて選択」**で、確認チェックを付けたグループだけをまとめて選択できます。未確認グループは選択されず、勝手にラベル確定されません。
