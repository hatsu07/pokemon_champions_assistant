# Pokémon Champions 対戦支援システム — 開発・Windows実行・MSI配布設計 v2.6

作成日：2026-10-21 / ステータス：**実装方針確定、実機・Windowsビルド未検証**

## 0. 適用範囲と変更履歴

本書は既存の戦術・状態管理・API仕様を**変更せず**、開発・Windows実行・MSI配布の方式を追加で確定する設計書。

- 正式API wire契約：`docs/Pokemon_Champions_Contract_And_State_Reconciliation_v2_3.md`／`contracts/`（`2.3.0`）。
- 効果マスター：`0.2.0`。未構造化項目は推測で補わない。
- 状態遷移：`docs/Pokemon_Champions_Integrated_Architecture_v2_0.md`／`S05_Battle_FSM_Design_v1_2.md`。
- 戦術判断の唯一の正本：`masters/Pokemon_Champions_MC_live_decision_gate.txt` と、関連するChampions固定マスター。
- 本書が優先する範囲：開発環境、Windowsアプリのモジュール境界、パッケージング、MSI、更新・アンインストール、Windowsテスト、セキュリティ、リリースゲート。
- 未実装の機能を本書によって「実装済み」と扱わない。過去の設計書にあるリスクと実機検証待ちは存続する。

## 1. 決定事項

**開発はWindows上のWSL2 + Docker Desktop + VS Code + Codex、実行はWindowsネイティブアプリ、配布は`.msi`**。

```text
Windows 11/10 x64（実機テスト対象）
├─ VS Code / WSL2 Ubuntu / Docker Desktop
│  ├─ Git repository: ~/projects/pokemon-champions-assistant（WSLファイルシステム）
│  ├─ Codex拡張: VS Code Remote-WSL側（推奨）
│  └─ Linux Docker開発コンテナ
│     ├─ Python, OpenCV, OCR, 契約検証, SQLite, OpenAI SDK
│     ├─ 状態管理・効果計算・APIクライアント（OS非依存）
│     └─ unittest/pytest・合成トレース/録画再生
├─ BlueStacks 5 / Pokémon Champions（実戦、Windows上で実行）
└─ PokemonChampionsAssistant.exe（Windows実行）
   ├─ Win32キャプチャアダプタ（第一候補、実機試験で決定）
   ├─ OCR、状態投影、Champions効果、SQLite
   ├─ OpenAI APIへの戦術判断要求
   └─ PySide6表示。ゲーム操作はプレイヤーが手動で実施

Windows GitHub Actions runner（CI/CD）
  -> Windows環境で依存関係を収集・テスト
  -> PyInstaller --onedir でWindows用実行物生成
  -> WiX Toolsetでユーザー単位MSI生成
  -> 検証・署名（設定時）・MSI成果物の保管
```

**本番実行にWSL2、Docker、Python、Node.jsは不要**。ただしBlueStacksとインターネット、利用可能なOpenAI API認証情報は別途必要。開発用ホストにはWSL2、Docker Desktop、VS Code等が必要であり「PCへの変更ゼロ」を意味しない。

## 2. 論理コンポーネントとOS境界

| モジュール | 内容 | Linux Docker単体テスト | Windows実機検証 |
|---|---|---|---|
| `core.events`, `core.reducer` | 証拠追記・効果反映・訂正・ターン追跡 | 必須 | 統合テスト |
| `core.effects` | 技／特性／持ち物効果の構造化計算。未整備はunknown | 必須 | 録画リプレイ |
| `core.decisions` | 入力機会FSM、古い回答の失効、合法手照合 | 必須 | 画面と統合 |
| `ai.openai_adapter` | SELECT/PREP/BATTLE/REVIEW用レスポンス契約 | モック必須 | 実通信は明示許可時のみ |
| `observer.interfaces` | `FrameSource`, `OCRReader`, `UIClassifier`等の抽象 | フェイク実装 | 実アダプタに差替 |
| `observer.win_capture` | Windows Graphics Captureなどによるウィンドウ取得 | スタブ | **必須** |
| `observer.ocr` | 領域OCR、日本語メッセージ・状態アイコン照合 | テスト画像 | 実画面で調整 |
| `ui.desktop` | PySide6で推奨、残り時間、現在状態、手動訂正 | 一部のロジック | **必須** |
| `storage.sqlite` | ローカルDB、再生、イベントとAPI入力の監査 | 必須 | インストール・更新試験 |
| `security.credentials` | ユーザーが入力するAPIキーの安全な保管 | モック | Windows資格情報ストア等 |

LinuxのWin32キャプチャ実行・Windows資格情報ストア利用・MSIビルドをDocker単体で完結する前提にしない。Windowsネイティブキャプチャが性能または互換性で不十分ならADBスクリーン取得との比較で決定。ADBポートを安易にLAN公開しない。

## 3. 戦術仕様・リアルタイム制約（変更不可）

- 選出／18対面PREP／各技・交代先／対戦後分析など、**戦術判断はOpenAI API**。ローカル処理は監視、合法手検証、確定した効果計算、ログ、UIのみ。
- 試合開始・次戦ごとにLIVE判断ゲート／選出マトリクス／60秒ルックアップ／現行自軍構築の**4件を実読込してハッシュ固定**。全件成功まで事前ロード完了と表示しない。対戦中に再読込・Web検索しない。
- 相手6匹を受信したらマトリクス6行を一括取得、選出3匹＋初手を最優先、続く40秒PREPは初手6＋控え12＋勝ち筋3点が最低条件。90秒の選出、各45秒の行動入力制限と実ゲーム締切を尊重する。
- 技選択と交代先のUI型は別管理。**同一の共通ポケモン選択UI**は、直前イベントによって通常交代・とんぼがえり・死に出しを判断。ひんし後の交代先と次の技選択は別入力機会にもなる。
- こだわり固定、先制技、メガ後のタイプ／特性／素早さ、即処理、処理役と安全受け、残り3匹全体の勝ち筋を遵守。
- 意図した技・効果と実際の成立は分ける。イベントの重複適用を防止。継続ターンはゲームのターン終了確定時のみ1回減算。
- API要求は`match_id`,`decision_window_id`,`tactical_state_version`,`allowed_action_digest`を含む。送信後に盤面や候補が変われば再検証し、不一致なら古い回答を表示しない。戦術上のローカル代替択は禁止。
- API通信はOpenAIの正式契約に従う。接続障害・遅延では「判断不可」を表示し、ローカルが勝手に技を選ばない。
- ゲーム自動操作（自動クリック、キー送信、対戦行動の自動実行）は実装しない。ユーザーが画面の推奨を見て手動入力する。

## 4. 開発環境を汚さない方法

- WSL側の`~/projects/...`にリポジトリを置き、`/mnt/c`配下へ直接大量のPython環境を展開しない。
- Linux Python／日本語OCR／OpenCV／依存管理／テストはDocker内。Windows/WSLへアプリ開発用`pip install`、仮想環境、OCRパッケージをグローバル導入しない。
- CodexはWSLリモートのVS Code拡張から使うのを基本とする。Dev Container内でのCodexはオプション。認証情報、SSH鍵、Windowsホーム、Dockerソケットをコンテナに自動マウントしない。
- Docker Composeは開発用。**製品の実行時にDocker Composeを呼び出さない**。
- GitHub ActionsのWindows runnerを初期のWindowsビルド環境とする。外部CIへ渡してよいのはソース／検証用データのみ。**実対戦録画、APIキー、個人ログを自動アップロードしない**。

## 5. Windowsアプリのファイル・データ設計

```text
Per-user MSI install:
  %LOCALAPPDATA%\Programs\PokemonChampionsAssistant\
    PokemonChampionsAssistant.exe
    _internal\...            # PyInstaller onedirの同梱実行時ファイル
    assets\...               # 版管理済みマスター、OCR辞書など（読み取り専用扱い）

Runtime user data:
  %LOCALAPPDATA%\PokemonChampionsAssistant\
    config\                   # 設定ファイル（非機密のみ）
    database\battle.sqlite3   # 対戦・イベント履歴
    logs\                     # 個人情報を最小化したログ
    cache\                    # 一時キャプチャ等、期限で削除

Secret:
  Windows Credential Manager または DPAPI保護下のユーザー領域
  （OPENAI_API_KEYをMSI本体、config平文、SQLite、ログへ保存しない）
```

既存ユーザーの設定・戦績DBはアプリ更新で消去しない。DBは版管理マイグレーション、バックアップ、不可逆変更前の確認、破損時の復旧導線を設ける。インストーラのcustom actionでDBを直接更新せず、アプリの管理下で行う。複数インスタンスによる二重起動とDB競合を防ぐ。

## 6. Windows実行ファイル・MSI設計

**技術選択：** PySide6 GUI、PyInstaller `--onedir`、WiX Toolset（初期候補v6、利用条件確認後ピン留め）。Windowsランナー上でビルドする。PyInstallerのLinuxからWindowsへの標準クロスビルドはしない。

MSI要件：

1. 製品名は`PokemonChampionsAssistant`。ターゲットは**Windows x64のユーザー単位インストール**。可能なら`Scope="perUser"`で管理者権限を要求しない構成を初期値とする（企業ポリシー等による差を検証）。
2. インストール対象は実行用ファイル、必要なPythonランタイム、PySide6/Qtプラグイン、OCRエンジン・学習データ、日本語リソース・Championsマスター・正規ライセンス表記を含め、Windowsに開発用PythonやTesseract導入を要求しない。
3. スタートメニューショートカットとアプリ一覧登録。自動起動・サービス化・OS全体へのPATH書換え・自動昇格は行わない。
4. WiXで安定した製品識別子を使い、**同一製品の新バージョンを上書きアップグレード**できるようにする。UpgradeCode/Id、コンポーネントGUID、バージョン方針を固定し、ダウングレードは明示的に制御。
5. アンインストールではアプリ本体を削除。ユーザーの対戦ログ・設定・鍵は**初期状態では残す**。個人データ消去は明示的な操作でのみ実行する（将来UI/専用削除機能）。
6. APIキーは各ユーザーがアプリ起動後に入力。MSI、Git履歴、CIのログ／成果物に埋め込まない。
7. 署名証明書を設定した場合は署名・タイムスタンプを付与。未署名配布ではSmartScreen/UACや信頼警告があり得ることを明示。コード署名と真正性検証は正式配布ゲート。
8. スキャンソフト誤検知、ランタイムDLL・VCランタイム依存、QtとOCRの配布ライセンスを実機とライセンスレビューで確認。商用・収益化の場合はWiX利用条件を別途確認。
9. MSIは**インストーラー**であり、ウィンドウを起動するだけで対戦支援が完成するものではない。アプリの実装・BlueStacks検証は別ゲート。

出力名：`PokemonChampionsAssistant-<app_version>-win-x64.msi`。

## 7. CI/CD パイプライン（採用設計）

```text
Pull request / source commit
 -> Linux Docker: jsonschema契約＋unittest＋再現テスト＋Lint
 -> Windows runner: Python/PySide6/OCR依存導入、Windows単体テスト
 -> Windows runner: PyInstaller onedir
 -> Windows runner: EXEを起動できることのsmoke、DLL/Qt/OCR同梱確認
 -> Windows runner: WiX MSI生成
 -> Windows runner: サイレントインストール → 起動 → アップグレード → アンインストール → ユーザーデータ保持検証
 -> 署名・ウイルススキャン（準備済みの場合）
 -> MSI、SHA-256、リリースノート、テストログをartifactとして公開
```

CIでBlueStacks実機の画面キャプチャやランクマ判定ができるとは仮定しない。これらは手元のWindows PCで別途認定する。デフォルトで実APIコールしない（CIはモック）。リリース／署名／公開は明示的な承認が必要。

## 8. MSIの受入条件

- [ ] Windows 10/11 x64のクリーンなVMで、Python・Docker・WSLなしでインストール・起動できる。
- [ ] PyInstaller onedir内でPySide6、OpenCV、OCR日本語、付属マスターが動作する。
- [ ] 新規MSI、同バージョン再インストール、バージョンアップ、ダウングレード制限、アンインストールで矛盾がない。
- [ ] 更新・アンインストール後にユーザーの`battle.sqlite3`、設定、キーの保存方針が守られる。
- [ ] APIキーと画面録画がMSI・Git・CI artifactに含まれない。
- [ ] `version --json`等でアプリ版、wire `2.3.0`、効果マスター `0.2.0`、固定マスターSHAを表示できる。
- [ ] 重大なOCR未認識・API遅延・古い状態の返答を適切に表示・拒否できる。
- [ ] BlueStacks 5の画面取込はWindows実機の録画／生画面で検証済みである（MSIビルド成功とは別）。
- [ ] 外部対戦支援の利用規約と利用するライブラリの再配布許諾を確認済み。

**重要：上記は合格条件であり、現時点ですべて未検証。**

## 9. Codexのタスク分割・完了報告

既存のPhase 0–6を順に実装する。さらに`Phase 7: Windows release/MSI`を追加する。

- Phase 0: 既存16件の契約テスト・依存固定・雛形。
- Phase 1: SQLite追記イベント・訂正・効果計算・再生。
- Phase 2: S00–S08/S05判断窓・4ファイルロード・40秒PREP。
- Phase 3: OpenAI契約／モック／期限／旧回答無効化。
- Phase 4: 実画面の観測アダプタ／OCR／サンプル動画によるキャリブレーション。
- Phase 5: PySide6 GUI／操作支援と手動訂正、ログ。
- Phase 6: Windows実機BlueStacks E2E検証と性能測定。
- **Phase 7: Windowsパッケージング／MSI／更新・アンインストール試験。** Phase 5のGUI等が存在しない時点では完成MSIを偽装しない。CIの配布ビルドはテスト結果に依存。

各フェーズでCodexはコード、実行したコマンド、テスト件数、成功/失敗、未検証事項、次の実装コマンドを`IMPLEMENTATION_STATUS.md`へ追記する。Windows用の試験をLinuxコンテナの合格で代用しない。

## 10. この時点で未確定の事項（実機・運用で確定）

1. Windows Graphics Capture / 他Win32方式 / ADBの実際の取込速度、画面認識適合性。
2. 日本語OCRエンジンの採用・トレーニングデータの再配布／認識精度。
3. Windows 10/11別のPySide6やライブラリ互換性、セットアップ時のインストール権限。
4. WiXの利用条件、開発・配布時点で採用する最終版、署名証明書。
5. BlueStacksとPokémon Championsの外部対戦支援に関する利用条件。
6. 実画面でのターン終了・状態異常・メガ演出の検知精度。
7. ネットワーク往復時間、APIコスト、モデル選定、実戦タイムリミットとの適合性。

## 11. 参考リンク・検証対象

- PyInstaller： https://pyinstaller.org/en/stable/usage.html
- WiX Toolset / MSI： https://docs.firegiant.com/wix/ ; https://docs.firegiant.com/wix/schema/wxs/packagescopetype/
- WiX Open Source Maintenance Fee： https://docs.firegiant.com/wix/osmf/
- GitHub Actions Windows runner： https://docs.github.com/en/actions/reference/runners/github-hosted-runners
- Docker Desktop + WSL： https://docs.docker.com/desktop/features/wsl/use-wsl/
- VS Code WSL： https://code.visualstudio.com/docs/remote/wsl

## 12. 変更管理

- 本書の`v2.6`は**開発・Windows MSI配布の設計バージョン**であり、API wire `2.3.0`や効果マスター`0.2.0`を`2.6.0`へ勝手に変更しない。
- 実装開始後の変更は設計記録（ADR）・互換性判定・回帰テストを伴う。
- 古い`WINDOWS_SETUP.md`中の「WSL/Docker実行を製品に必要とする」と読める表現は本書の**開発時のみ**という境界で読み替える。
