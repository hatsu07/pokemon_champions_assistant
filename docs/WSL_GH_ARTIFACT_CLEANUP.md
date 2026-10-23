# 過去のActions Artifacts整理手順（履歴・非推奨）

**現在のDEVELOPMENT ZIPはActions ArtifactsではなくGitHub Releasesへ保存しています。**
現行の配布方針・削除の自動スケジュールは[GitHub Releases運用](WINDOWS_DEVELOPMENT_RELEASES.md)を参照してください。

この旧手順は、2026-10-23以前のActions Artifacts保存容量上限による配布失敗へ対処するための履歴です。既存の`scripts/prune_actions_artifacts.sh`は廃止していないものの、**新しいリリース用workflowでは使用しません**。以前作成された古いArtifactsを手動で整理する場合だけ、最初にドライランで対象を確認し、`--apply`を付けて削除できます。対象名の拡張は`--include-legacy`で明示します。

このリポジトリの旧ArtifactsはユーザーがWSLの`gh`で削除済みです。新たなGitHub Releases版へ切り替え後は旧クリーンアップをcronに登録しないでください。GitHubの正式リリースや関連タグを誤削除しないため、**GitHub Releasesには専用の`scripts/prune_development_releases.py`を使います。**
