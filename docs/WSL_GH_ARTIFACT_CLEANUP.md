# WSL GitHub CLIによるWindows CI成果物の定期整理

2026-10-23の最新方針：**Windows EXEのビルド・起動スモーク・2種類のDEVELOPMENT ZIPの保存は、GitHub Actionsで行う。** WSL2/Linux DockerやWineでWindows EXEをビルドしない。Windows本体にも開発用Pythonを導入しない。

## 成果物の名称とスコープ

Ready PRの [Windows desktop development validation](../.github/workflows/windows-validation-artifact.yml) は次の2件をGitHub Actions Artifactsに保存する。

- `PokemonChampionsAssistant-DEVELOPMENT-windows-x64`
- `PokemonChampionsScreenDataCollector-DEVELOPMENT-windows-x64`

GitHubでダウンロードするとそれぞれZIPになる。展開直下にWindows EXEと依存ファイルがあり、二重ZIPはない。これらは**開発用**であり、正式配布版/実機BlueStacks確認済みを意味しない。

各アップロードの `retention-days: 2` は新規成果物の保持期間。既存のArtifactsには遡及適用されない。容量が尽きた場合、アップロード失敗により**CI全体もFailure**となり、最新HEADの成功を確認できるまでマージしない。

## 初回の整理（ユーザーのWSLで実行）

WSLのリポジトリ直下から、次の順に実行する。

```bash
gh auth status
bash scripts/prune_actions_artifacts.sh
bash scripts/prune_actions_artifacts.sh --apply
```

既定では**対象2名称に一致するArtifactsだけ**を対象とし、**各名称の最新1件を残し、作成から1日以上たった古いものだけ**を削除する。1行目のドライランは削除しない。2行目の`--apply`だけが実際に削除する。削除対象数・名前・ID・作成日・サイズを先に表示する。GitHub Actionsの**workflow run履歴は削除しない**。

## 2026-10-23の初回一括整理：旧3名称も対象

2026-10-23のユーザー提供CLI一覧では**計130件、約18.46 GiB**のArtifactsが存在。内訳：現行DEVELOPMENT 19件、旧 `PokemonChampionsAssistant-windows-x64` 41件、`PokemonChampionsSettingsValidation-windows-x64` 34件、`PokemonChampionsValidation-windows-x64` 36件。既定のスクリプトは現行2名称しか扱わず、`--min-age-days 1`のため当日分も除外していた。

最新版を残して初回の古い形式も整理する場合は、**PRブランチの最新版のスクリプト**を使い、`--include-legacy`を明示する。以下の実行は、旧3名称の全バージョンと、現行DEVELOPMENT 2名称の最新版1件ずつを除いた旧バージョンを対象にする。

```bash
git fetch origin fix/restore-ci-artifacts-gh-prune-20261023
git show origin/fix/restore-ci-artifacts-gh-prune-20261023:scripts/prune_actions_artifacts.sh > /tmp/pokemon-artifact-prune.sh

# 削除前の必須プレビュー：現行2件を保護し、旧バージョンは当日分も対象
bash /tmp/pokemon-artifact-prune.sh --include-legacy --min-age-days 0

# 一覧が意図した内容であることを人間が確認してからだけ実行
bash /tmp/pokemon-artifact-prune.sh --include-legacy --min-age-days 0 --apply
```

2026-10-23の一覧が変わっていなければ、約128件が候補。最新版である
`PokemonChampionsAssistant-DEVELOPMENT-windows-x64`（ID 12492401875）と
`PokemonChampionsScreenDataCollector-DEVELOPMENT-windows-x64`（ID 12491438458）は保護される。ただし削除対象数・IDは毎回GitHubの最新データを使用する。旧名称の成果物が必要なら**削除前にローカル退避**すること。削除は復元できない。

通常の定期運用では**`--include-legacy`を指定しない**。これにより現行2名称の古い版だけを整理し、その他の成果物を触らない。

保存容量を緊急に解放したい場合でも、最新版1件の保護は維持する。`--min-age-days 0`は当日生成分を対象にするが、最新版の保護は解除しない。削除は復元できないため、必要な古いビルドは**事前に`gh run download`などでローカル退避**しておく。

全アカウント共通のActionsストレージ上限には、このリポジトリ以外のArtifactsも影響し得る。本スクリプトは他のリポジトリや、異なる名称のArtifactsを一切削除しない。容量使用量の反映は即時とは限らない。最新版だけでも容量を使い切る場合は追加の整理方針が必要。

## 定期整理（WSLが起動している環境の場合）

まず手動実行とドライランで削除対象が意図どおりか確かめてから、WSL内の`crontab -e`に次のような行を追加する。**実際の絶対パス**に置き換えること。

```cron
# 毎日21:00 JST: 1日以上古いWindows DEVELOPMENT artifactsを整理
0 21 * * * /bin/bash /home/YOUR_USER/projects/pokemon_champions_assistant/scripts/prune_actions_artifacts.sh --apply >> /home/YOUR_USER/artifact-prune.log 2>&1
```

cronのWSLプロセスが稼働していないと実行されない。PCの電源OFF、WSL停止・スリープをまたぐ確実な定期実行が必要なら、**Windowsタスクスケジューラで`wsl.exe`から同スクリプトを起動する設定**を別途行う。GitHub認証がスケジューラの実行ユーザーから有効であることを確認。GitHubトークンをcron定義やログに直書きしない。

## CIで確認すべきこと

- 最新PR HEADとActions runの対象SHA、実際のcheckout SHAが一致。
- Windowsオフラインテスト、2 EXEのPyInstallerビルド、両GUI起動スモークが成功。
- **両方のUpload artifact手順がSuccess**（Skippedは不可）。
- Windows workflow run全体と必須ジョブ`desktop-windows-validation`が`completed/success`。

そのあと初めてマージ。ブランチ保護はユーザー判断により有料プランを使って有効化しない。GitHubの自動制限ではなくこの検証手順を開発エージェントが必ず守る。

CIが成功してもGitHubのArtifacts自体が消えている場合は、保存期間と削除履歴を確認する。実行履歴にZIPが存在したことと、現時点でダウンロードできることは別。

## 新チャットへの引継ぎ

最初に`AGENTS.md`、`docs/AI_DEVELOPMENT_AND_CI_POLICY.md`、`docs/CI_MERGE_GUARD_AND_NEXT_CHAT.md`、**本書**を確認し、最新のPR・CI設定・Artifacts状態をGitHubから再取得する。Windows ZIPの提供を目的とするPRでは**両方のアップロード成功**を省略してはならない。GitHub側の保存容量不足によるCI失敗をテスト成功としてマージしてはいけない。
