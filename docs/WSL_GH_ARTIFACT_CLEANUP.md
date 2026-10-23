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

保存容量を緊急に解放したい場合でも、最新版1件の保護は維持する。`--min-age-days 0`を明示すれば、残す最新版を除く古い成果物を作成当日でも対象にできる（必ず先にドライランを実行）。削除は復元できないため、特に必要な古いビルドは**事前に`gh run download`などでローカル退避**しておく。

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
