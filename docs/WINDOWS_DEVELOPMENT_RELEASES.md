# Windows DEVELOPMENT配布: GitHub Actions + GitHub Releases

2026-10-23以降の現行方針。Actions Artifactsの保存容量不足を避けるため、ZIPは**Actions Artifactsには一切アップロードせず、GitHub Releasesの非正式プレリリースとして保存**する。GitHubの公式ドキュメントでは、Releaseアセットは1ファイル2 GiB未満、1リリース1000件以下、リリース全体のサイズと転送量に上限なし。従来のActions Artifactsと異なる保存機構。GitHub FreeのActions実行分数制限は引き続き適用される。

## 役割を分ける

- **Ready PR**：`.github/workflows/windows-validation-artifact.yml` がWindows上で全オフラインテスト・2種のPyInstaller Windows onedir EXE・両GUI起動スモーク・最新PR HEAD照合を実行。**Artifactのアップロード・Release公開はしない。** `contents: read` のみ。最新HEADに対するworkflow全体と必須job `desktop-windows-validation` が `completed/success` 以外なら**マージ禁止**。
- **master push**：`.github/workflows/windows-development-release.yml` が**masterの正確なSHA**をcheckoutしてテスト・Windows両ビルド・両スモークをもう一度検証。2種類のZIPをそれぞれ圧縮し、EXEと`BUILD_REVISION.txt`の存在、同梱SHA、ZIPサイズを確認。**ZIPが両方揃った場合だけ**Development専用のDraft Releaseを公開前検証し、GitHub Releasesプレリリースに公開する。途中失敗ならworkflow Failureとして報告し、失敗したDraftは未公開のまま残す。
- **手動master再発行**：GitHub Actionsの`Windows DEVELOPMENT GitHub Release`でRun workflowをmasterに対して実行。タグ名は同一コミットなら同じで、公開済みZIPが両方揃っていれば再利用・検証する。
- **古いRelease整理**：`.github/workflows/windows-development-release-cleanup.yml` が毎日実行（UTC 03:17 = JST 12:17）。`scripts/prune_development_releases.py`により、**14日以上**経過かつ**最新版3件以外**の特定DEVELOPMENTプレリリースだけ、Releaseと**対応する生成タグ**を削除。ジョブは完全失敗時に削除対象を勝手に広げない。通常リリース・別形式タグ・Draft・immutable releaseは削除しない。GitHubのRelease immutability有効時、削除できないものはスキップまたはAPI側で保護される。

リリース作成と定期削除は同一のconcurrency groupを使い、同時実行を避ける。リリース作成workflowだけが`contents: write`、PR検証は読み取りのみ。非公開リポジトリを公開設定へ変更しない。

## 取得するもの

[GitHub Releases](https://github.com/hatsu07/pokemon_champions_assistant/releases) のプレリリースから、それぞれ次のZIPをダウンロード：

- `PokemonChampionsAssistant-DEVELOPMENT-windows-x64.zip`
- `PokemonChampionsScreenDataCollector-DEVELOPMENT-windows-x64.zip`

タグ名は`development-windows-<40桁コミットSHA>`、リリース名は`Windows DEVELOPMENT <SHA先頭12桁>`。いずれも**DEVELOPMENT_ONLY**。正式MSI、実対戦検証済み、学習済みONNXモデル搭載を意味しない。ZIP内にはEXE、`_internal`、`BUILD_REVISION.txt`があり、二重ZIPにはしない。実画像・認証情報・個人ログを含めない。

## WSLのghで整理する場合

```bash
# 既定は一覧表示のみ、GitHubに変更を加えない
python3 scripts/prune_development_releases.py

# 保存期限14日を過ぎた開発プレリリースだけ削除（新しい3件は残す）
python3 scripts/prune_development_releases.py --apply
```

手動 `workflow_dispatch` のクリーンアップも既定はプレビューだけ。GitHub Actionsの手動Run workflow入力で`apply=true`を明示した時のみ実削除。定期スケジュールでは`--apply`を実行する。不要なstable Release、他種のプレリリースを削除してはいけない。

`scripts/prune_actions_artifacts.sh`は**以前のActions Artifactsを掃除するためのレガシー用ツール**として残す。今後のWindows DEVELOPMENT ZIPはActions Artifactsを使用しないため、通常の自動実行・定期cron登録は不要。Actions Artifact storageの削除/容量再計算が遅れても、Releases配布には直接依存しない。

## 検証と停止条件

- PRマージ判断：最新PR HEAD SHA・workflow_run.head_sha・実際checkout SHA・CI全体と必須job`completed/success`一致を確認。古い成功でマージしない。PRにRelease成果物がないことを失敗扱いしない。
- masterにマージ後：**別workflow**のWindows本体ビルド、両スモーク、両ZIPの実際のReleaseへのアップロード成功とReleaseタグSHAを照合。masterリリース失敗は配布失敗であり、元PRの成功を公開成功と混同しない。
- Windows CIのoffline smokeは実機のBlueStacks/ミラーリング画面、AI送信、モデル精度、ランクマでの勝率の証明ではない。
- GitHub CLIの `gh release create <tag> <ZIP1> <ZIP2> --prerelease` は内部で一時Draftを作り、両ZIPをアップロードしてから公開する。**空のDraftを先に作成し、タグでそのDraftを照会しようとしない**（未公開のDraftは一時的な `untagged-...` URLとなり、tag APIからは取得できない）。途中失敗したDraftは自動削除対象外であり、同一SHAを再実行しても競合する場合がある。復旧時はその特定の失敗DraftのID/作成SHA/assetを検証したうえで管理者が個別に後始末し、masterのビルドワークフローを再実行する。普通のタグ・正式リリースを勝手に削除しない。
- GitHub Actions実行時間・GitHub Releases権限やActionsワークフローが無効化される場合は、保存先の変更だけでは解決しない。その場合は現実のCI結果とエラーを報告する。

公式資料：[GitHub Releases保存制限](https://docs.github.com/en/repositories/releasing-projects-on-github/about-releases) / [gh release create](https://cli.github.com/manual/gh_release_create) / [gh release delete](https://cli.github.com/manual/gh_release_delete)。
