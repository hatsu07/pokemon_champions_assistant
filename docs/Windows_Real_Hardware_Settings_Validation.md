# Windows 実機: GUI設定・読取プレビュー検証

これは既存の **Pokémon Champions Assistant** の設定画面を、Windows開発用Pythonを入れずに実機で確認するための手順です。Phase 7のMSIや配布版ではありません。実行できても、BlueStacksの連続観測、OCR、対戦状態同期、実API、ゲーム規約適合性は検証済みになりません。

この成果物には個人の構築CSV、SQLite、ログ、キャプチャ、対戦履歴を含めません。構築検証用の固定カタログだけを同梱します。ゲーム操作（クリック、キー入力、ADB input）は実装していません。ポータブル設定検証モードでは「次戦を開始」とAPI送信も無効です。

## 1. Windows成果物を作る

GitHub Actionsで **Windows validation harness artifact** を対象コミットから `Run workflow` します。完了後、成果物 **`PokemonChampionsSettingsValidation-windows-x64`** をダウンロードします。これは既存のPhase 6 CLI成果物とは別です。

`PokemonChampionsSettingsValidation-windows-x64.zip` は外側のActionsダウンロードです。中にある同名のポータブルZIPをさらに展開してください。PowerShellでは次のとおりです（開発用Pythonは不要です）。

```powershell
$Root = Join-Path $env:LOCALAPPDATA "PokemonChampionsAssistant\phase8-real-hardware"
New-Item -ItemType Directory -Force $Root | Out-Null

$ArtifactDownload = Join-Path $env:USERPROFILE "Downloads\PokemonChampionsSettingsValidation-windows-x64.zip"
$ArtifactExtract = Join-Path $Root "artifact-download"
Expand-Archive -LiteralPath $ArtifactDownload -DestinationPath $ArtifactExtract -Force
$PayloadZip = Get-ChildItem -LiteralPath $ArtifactExtract -Recurse -Filter "PokemonChampionsSettingsValidation-windows-x64.zip" |
  Select-Object -First 1 -ExpandProperty FullName
if (-not $PayloadZip) { throw "Portable settings validation ZIP was not found" }
Expand-Archive -LiteralPath $PayloadZip -DestinationPath $Root -Force

$SettingsGui = Join-Path $Root "PokemonChampionsSettingsValidation\PokemonChampionsSettingsValidation.exe"
if (-not (Test-Path -LiteralPath $SettingsGui)) { throw "Settings validation executable was not extracted: $SettingsGui" }
& $SettingsGui --version
if ($LASTEXITCODE -ne 0) { throw "Settings validation version check failed" }
```

任意で、画面表示・キャプチャ・API・ゲーム操作を一切行わない凍結パッケージのローカルsmokeを実行できます。

```powershell
& $SettingsGui --smoke
if ($LASTEXITCODE -ne 0) { throw "Settings GUI smoke failed" }
```

`GUI_SETTINGS_VALIDATION_SMOKE_OK` はPySide6と同梱固定カタログを初期化できたことだけを示します。BlueStacks検出や画面取得の成功ではありません。

## 2. BlueStacksの1フレームを設定GUIから確認する

1. BlueStacksとPokémon Championsを手動で起動します。対象ウィンドウは表示中・非最小化にします。ほかのアプリが上に重なっていても構いません。
2. 次で、通常プロファイルと混ざらない実機検証専用設定を指定して起動します。

```powershell
$Config = Join-Path $Root "config\settings.json"
& $SettingsGui --config $Config
```

3. 初回は「初回設定」が自動で開きます。`画面キャプチャ`で`ウィンドウを検索`を押し、`[BlueStacks候補]`の表示から、タイトル・PID・HWNDが対象と分かる項目を選びます。これはウィンドウの前面化、移動、サイズ変更を行いません。
4. 画面下部の`動作確認`を押します。`CAPTURED_READ_ONLY_UNVERIFIED_CONTENT / WIN32_PRINTWINDOW_CLIENT / (幅, 高さ) px / DPI ... / ... ms` が表示された場合のみ、読取専用の1フレームが得られています。プレビュー内容を目視し、Pokémon Championsが写っていることを確認してください。別ウィンドウの重なりを取り込まないため、ネイティブ方式は対象クライアントの`PrintWindow`を使います。
5. 正しい画面を目視確認した後にだけ、プレビュー上をドラッグしてゲーム領域のROIを指定します。これは正規化ROIとして保存されるため、DPI・モニター・BlueStacksサイズを変えたら再較正してください。
6. `保存先`では既定の`$Root\data`と`$Root\captures`を使うか、書込み可能なローカルフォルダーを選択します。`テスト撮影を保存（明示操作のみ）`を押すと、上書きを拒否してキャプチャフォルダーへPNGを保存します。画像・設定・SQLiteはローカルにだけ残し、GitHub Actionsへアップロードしません。
7. `保存`で終了し、同じ`--config $Config`を指定して再度起動します。対象ウィンドウIDとROI、保存先が復元されることを確認します。ウィンドウが再生成・最小化・消失していた場合は警告になり、古いHWNDへの黙った再利用は行いません。

この段階で記録する値は、GUIに表示されるキャプチャ方式、画像実寸、Win32 DPI、取得時間、保存したPNGのローカルパス、ROIの有無です。`CAPTURED_READ_ONLY_UNVERIFIED_CONTENT`はコンテンツ認識・OCR・対戦画面判定の成功を意味しません。

## 3. ネイティブ取得に失敗した場合のADB代替

`WINDOW_IDENTITY_UNAVAILABLE`、`WINDOW_MINIMIZED`、`CAPTURE_FAILED`、または目視で誤ったプレビューの場合はROIを保存しません。Phase 6の[読取専用ADB手順](Phase_6_BlueStacks_Validation.md#read-only-capture-probes)に従い、ローカル/私有の`HD-Adb.exe`または`adb.exe`と確認済みserialを用意します。

設定画面で`ADB（代替）`を選び、ADB実行ファイルとserialを明示入力してから`動作確認`を押します。実装するADB操作は`get-state`と`exec-out screencap -p`だけです。`adb connect`、`forward`、`input`、`tap`、`keyevent`は使いません。ADB画像にはWin32ウィンドウ領域・DPIの測定値がありません。ネイティブ測定とは混ぜず、別の結果として記録してください。

## 4. 構築CSVと保存先の確認（任意）

`自軍構築`の`CSVを登録`から、利用者がローカルで保持する現在の`Pokemon_Champions_MC_team_2026-10-03.csv`を選択できます。このCSVは成果物に含めません。`TEAM` 1行と`MON` 6行が表示され、固定カタログと照合されます。`NEEDS_REVIEW`は性格/APなどに固定マスターの根拠がないことを示す場合があり、別作品の知識で補完しません。

構築を選択・保存して再起動後に復元されることは確認できますが、このポータブル検証モードでは試合を開始できません。したがって、4ファイルの事前ロードや対戦中の構築切替を実機合格とは扱いません。

## 受入境界と報告内容

次を利用者が報告するまで、GUI設定のWindows実機検証は未完了です。

- `--smoke`の終了コードと出力status
- 選択したBlueStacks候補の表示（タイトル/PID/HWNDは必要なら一部伏せてよい）
- ネイティブまたはADBのstatus、方式、画像実寸、DPI（ネイティブのみ）、取得時間
- 保存PNGを目視してPokémon Championsであることを確認したか
- ROI・保存先・再起動後の設定復元の成否
- 失敗時はエラー種別のみ（画像、対戦ログ、ADB serial、APIキーは共有しない）

これで受け入れられるのは「Windows設定GUIによる読み取り1フレームと設定保存」のみです。連続監視、UI/OCR精度、イベント/FSM、GUI反映遅延、対戦運用、MSI、実APIは依然として未検証です。
