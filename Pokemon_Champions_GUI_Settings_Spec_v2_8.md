# Pokémon Champions Assistant — GUI設定・キャプチャ対象・ログ保存・自軍構築管理仕様 v2.8

作成: 2026-10-22 / 既存Phase 0–6R-2実装への追加要件。Codex実装時はローカルリポジトリの現行コードと `AGENTS.md` / 正式API契約 v2.3 を優先し、この文書と矛盾する実装済み名称は対応付けて記録する。

## 1. 目的と非目的

WindowsネイティブのPySide6 GUIに「設定」画面を追加し、Windows/BlueStacksキャプチャ対象の指定、対戦ログおよび画像保存先の選択、自軍6匹の構築登録・検証・切替を画面操作で完結させる。

- WSL/Dockerは開発と自動テストに限定。配布実行には不要。
- Python/OpenCV/OCRは観測・計算・保持、OpenAI APIだけが戦術を判断。設定GUIやPythonは独自の選出・立ち回りを決めない。
- ゲームへのキー入力/クリック注入は実装しない。キャプチャは読み取り専用。
- 設定保存・画面プレビューによってOpenAI APIを呼び出さない。
- 旧チームファイルやLIVE判断ゲートは上書きしない。

## 2. 画面構成

既存 `desktop.py` のメインダッシュボードから「設定」を開く。タブを `画面キャプチャ`、`保存先`、`自軍構築`、`診断` に分け、下部に `保存` / `キャンセル` / `動作確認` / `既定値に戻す`。既存の試合画面・ログ画面・API停止操作は維持する。

### A. 画面キャプチャ

- キャプチャ方式: `Windowsネイティブ（推奨）`、`ADB（代替）`、`画像/録画リプレイ（検証）`。
- Windows方式: `ウィンドウを検索` → Win32の可視トップレベルウィンドウ一覧を表示（プロセス名、タイトル、PID、HWND、クライアント領域、最小化の有無）。`BlueStacks` を候補として上位表示するが、任意のウィンドウを選択可。タイトルだけではなく HWND/PID/プロセス情報で検証。古いHWNDを永久保存して使い回さない。
- `プレビュー開始/停止` : 低負荷・読み取り専用で縮小フレームを表示、実取得解像度、Win32 DPI、キャプチャバックエンド、キャプチャ時間、FPS、最終成功時刻を表示。プレビューと本番観測ワーカーの同時起動競合を防ぐ。
- `ゲーム領域を指定`: プレビュー上でドラッグ矩形を指定し、元キャプチャ画像のピクセル座標と、対象ゲーム領域内の正規化座標 [0..1] を保存。アスペクト・DPI・サイズ変更時は再変換/再較正を要求。選択範囲外・空領域は拒否。
- `テスト撮影`: フレーム1枚だけを設定した画像保存先へ保存（明示操作時のみ）。ゲーム画面かどうかは手動確認可能にし、未確認の画像をOCR較正済みと扱わない。
- 検出対象なし、最小化、タイトル変更、ウィンドウ再生成、黒画面の場合、黙ってデスクトップ全体を撮らない。安全に停止し警告。
- ADBはユーザーが実行ファイル/serialを明示的に指定。`get-state` / `exec-out screencap -p` の読み取り専用のみ。入力操作は禁止。
- 実績値（運用者報告）: BlueStacks 5.22.285.1013 / Android 13 beta / 1920x1080 / Android 240dpi、Windows DPI 192（200%）、キャプチャ実寸 2254x1298、Win32 PrintWindow初回47ms。ROIはこの数値で固定しない。

### B. 保存先

- `対戦データ保存先`: SQLite、監査イベント、振り返り、ログ出力のホームフォルダー。初期値 `%LOCALAPPDATA%\\PokemonChampionsAssistant\\data`。
- `キャプチャ/較正画像保存先`: 初期値 `%LOCALAPPDATA%\\PokemonChampionsAssistant\\captures`。画像・動画保存は原則オフ/明示的許可のみ。容量上限/保持日数の設定を用意。
- `保存先を選択` はWindowsネイティブのフォルダー選択ダイアログを使用。存在確認、書き込み権限、ディスク空き容量、SQLite向けファイルシステム特性、パス規格化、パスの危険性（インストールフォルダー、他ユーザー領域など）を確認。
- **既存データの場所変更**: 試合中は反映しない。試合終了後にSQLiteを安全にバックアップ、必要な関連ファイルをコピー、ハッシュ/件数照合、成功後に設定を切替。失敗時は元データに戻す。切替だけしてデータを置き去りにしない。元データは確認前に削除しない。
- 過去ログ・データの外部共有、Git追跡、GitHub Actions成果物への混入は禁止。`private_history` は起動時には読まない。APIキーはログ対象から除外。
- ゲーム内ログ/アプリ診断ログ/スクリーンショットの保存ON/OFFとエクスポート範囲を分離する。

### C. 自軍構築

- 構築一覧: 登録名、6匹、作成/更新日、検証状態、選択中（次戦適用）、SHA-256。`登録（CSVインポート）`、`編集`、`複製`、`選択`、`エクスポート`、`削除`。
- 初期インポート対象: `masters/Pokemon_Champions_MC_team_2026-10-03.csv`。旧9/29構築は履歴扱い、黙って切り替えない。
- 実際のCSV形式はUTF-8 BOM対応。`TEAM` 1行＋`MON` 6行を扱い、列 `record,date,reg,name,item,ability,nature,ap_h_a_b_c_d_s,move1,move2,move3,move4,role,priority,setup,recovery,choice_lock,form_change,action_guarantee,runtime_note` を保持する。未知列やTEAM行のメモは破棄せず、入出力のround-tripが可能な実装にする。
- 編集対象: 名前/フォーム、持ち物、特性、性格、AP配分（原CSVの `ap_h_a_b_c_d_s` をそのまま表す）、4技、役割、補足。6匹表示。技や持ち物はChampions固定マスターの候補から選択できるUIと手入力の未確認状態を区別する。
- 特殊なCSV表示（例: `ルカリオ → メガルカリオZ` や特性の前後表記）を無理に単独種名へ変換しない。変換規則がなければ要確認として表示し、原文保持。
- 検証: CSVスキーマ・列欠落・重複・6匹/各4技・マスターに載るポケモン/技/持ち物/特性・Champions習得技の適合性（確証のある対象のみ）。AP値・性格・フォーム規則はChampions固定マスターまたはゲーム内確認に根拠がある範囲で判定し、未確認なら `要確認` とする。別作品の規則で補完しない。
- 検証状態: `VALIDATED`、`NEEDS_REVIEW`、`INVALID`、`SOURCE_MISSING`。理由と参照したマスターのSHAを表示。`INVALID` または必須項目不足なら本番選択不可。情報不足は確認済みと偽らない。
- インポートは元ファイルを変更せず、プライベートなユーザーデータ領域内 `teams/` にコピー。コピーにはSHA・原本パス/取込日時/出典を記録。任意のチームデータを自動でGitやパッケージへ同梱しない。
- **4ファイル事前ロードとの接続**: 試合開始を指示されたとき、LIVE判断ゲート・LIVE選出マトリクス・60秒ルックアップ・GUIで選択中の構築ファイルの4つを実際に読込し、その試合のスナップショットとSHAを固定。4件読込成功時だけ `事前ロード完了`。試合中の構築変更は予約し、試合後の次戦から適用。既存試合のown_team、API snapshot、PREPを変更しない。

### D. 診断

- Captured: yes/no, capture target, capture resolution, DPI, live preview status, ROI calibration state。
- Config saved/dirty、チーム検証状況、試合開始時4ファイルSHA、DBの保存先、ディスク空き、ログ権限、API status（キーを表示しない）。
- `問題診断レポートを保存` はパス・ユーザー名・トークン・画面内容をデフォルトで秘匿し、共有前レビューを要求。

## 3. 永続化と適用タイミング

- 1つの型付き設定モデル（例 `AppSettings`）を使用し、スキーマ版と移行処理を設ける。Windows実行時の既定保存先 `%LOCALAPPDATA%\\PokemonChampionsAssistant\\config\\settings.json`。原子的書き込み（tmp→fsync→replace）、破損時バックアップ復旧。
- Linux/Dockerのテストでは設定パスをDIで差替可能。Windowsパスをテスト環境で強制作成しない。
- APIキーやサービス認証情報は設定JSONに含めない。Windowsの資格情報保護またはWindows Credential Managerのインターフェースを使う。
- キャプチャターゲット/ROI変更: 非対戦時は即時適用、対戦中は観測停止→明示切替→再同期→必要なら古いAI判断を失効。無断で対象を切替しない。
- ログ保存先変更・自軍構築変更: **試合中は予約、次試合から適用**。既存のSQLite接続・4ファイルSHAスナップショットを破壊しない。
- GUIの設定表示、プレビュー、保存、サイズ変更はAPIの新しい判断機会を発行せず、tactical_state_versionも不必要に更新しない。

## 4. 参考の設定JSON（非秘密情報のみ、内部実装では要スキーマ検証）

```json
{
  "schema_version": "1.0",
  "capture": {
    "backend": "WINDOWS_NATIVE",
    "preferred_process": "HD-Player.exe",
    "preferred_title": "BlueStacks",
    "selected_window_identity": null,
    "game_viewport_normalized": null,
    "adb_path": null,
    "adb_serial": null
  },
  "storage": {
    "data_root": "%LOCALAPPDATA%\\PokemonChampionsAssistant\\data",
    "captures_root": "%LOCALAPPDATA%\\PokemonChampionsAssistant\\captures",
    "save_screenshots": false,
    "retention_days": 30
  },
  "teams": {
    "active_team_id": null,
    "pending_team_id": null
  }
}
```

環境変数参照やパスの解決はWindows実行時に行う。HWNDは変わり得るためキャプチャ開始時に再照合し、保存済みの候補を無条件に信用しない。

## 5. Codex実装タスク

1. 既存 `desktop.py`、`fsm.py`、`preload.py`、`store.py`、`windows_capture.py`、`capture_runtime.py`、`calibration.py` の公開APIを調査。重複実装しない。
2. 設定モデル、Windows/Linux差し替え可能な保存サービス、データ移行の実装とテスト。
3. PySide6設定ダイアログ実装。UIスレッドから重いキャプチャ/OCR/DB処理を実行しない。
4. Windowsウィンドウ列挙/選択/読み取り専用プレビュー/ROI設定を既存FrameSourceへ接続。
5. ログ・データ保存先変更を安全に移行し、手動保存・エクスポートを実装。
6. 現行CSV形式の構築インポート/編集/検証/登録を実装。既存マスターと照合し、未確認は未確認。
7. 選択中の構築を4ファイルPreloaderへ注入し、試合スナップショットを固定。試合中変更は次戦適用。
8. PySide6 offscreen/モックテスト、設定復旧/シリアライズ、window identity失効、無許可画像保存なし、DB移行失敗時ロールバック、チームCSV round-trip、4ファイルSHA、試合中更新隔離をテスト。
9. WindowsビルドにはQtプラグイン等を明示的に同梱。`WindowsValidation` EXEと正式GUIアプリを混同しない。MSI作成は既存Phase 7計画に従う。
10. `IMPLEMENTATION_STATUS.md` と `docs/implementation_decisions.md`、ユーザー設定手順を更新する。画面実機検証済/未検証を明記。

## 6. 完成条件

- Windows GUI上から対象ウィンドウを選択し、読み取り専用プレビューと単発保存ができる。
- 保存先変更後のログ読み書き・データ保持・再起動後の復元が検証できる。
- 現行Champions構築CSVを読み、6匹×4技と全項目を欠損なく表示/保存し、設定した構築を次試合の事前ロードへ渡せる。
- 試合途中の構築・保存先変更で現在試合の固定スナップショットが変化しない。
- API呼出増なし、ゲーム入力注入なし、秘密情報漏えいなし。
- Docker内全テストPASS。Windows実機GUI/BlueStacksの動作はWindowsで実行確認できた場合のみPASSと記録。
