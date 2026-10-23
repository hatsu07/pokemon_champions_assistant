# CI成功の手動マージゲート・新チャット引継ぎ（2026-10-23）

開発開始時に AGENTS.md、docs/AI_DEVELOPMENT_AND_CI_POLICY.md、本書を必ず読み、最新のGitHub PR・HEAD SHA・CIを直接再確認する。過去のチャットの報告では代用しない。

## マージ事故と現在の方針

PR #30をCI全体Failure（GitHub Actions Artifacts quota超過）の状態でmasterにマージしたのは誤り。コードテストの成功とCI全体Successは違う。
ユーザーは2026-10-23、GitHub Proへのアップグレードを行わず、private repoのブランチ保護を不要と明示した。
master.protected=falseは既知の制約。GitHubのMergeボタンは自動的に無効にならない。エージェントが厳格なCI確認を行う。ユーザーの指示なしにリポジトリを公開しない。

## Required PR CIと手動Artifacts配布

Ready PRのWindows CIは、オフラインテスト、両Windows PyInstallerビルド、両GUI起動スモーク、最新PR HEADの検証、**両DEVELOPMENT ZIPのArtifacts自動アップロード**をすべて実施する。必須job名は desktop-windows-validation。
ユーザーはWSLにGitHub CLIを導入済み。古いWindows ZIP Artifactsは `scripts/prune_actions_artifacts.sh` をWSLで定期的に実行し整理する。最新1件/名称を残し、初回はドライラン、削除には`--apply`を必須とする。ArtifactsのGitHub保存期間は2日。詳細は `docs/WSL_GH_ARTIFACT_CLEANUP.md`。
アップロード容量超過はCI全体のFailureとして扱う。CI全体Successと両アップロード手順のSuccessを確認できるまで絶対にマージしない。
BlueStacksの実機確認・画面分類精度・正式MSIは別工程であり、WindowsオフラインCIでは証明できない。

## 手動・AIエージェントによる例外なしマージ判定

1. 開発は作業ブランチ + Draft PRで行い、変更をすべてコミットし最新HEADを確定したあとReadyへ切り替える。
2. PR HEAD、CI workflow_run.head_sha、実際にcheckout/buildしたSHAが完全一致することを確認。古い成功は使わない。
3. **PR最新HEADに対する最新Windows workflow run全体** とjob desktop-windows-validation がともに status=completed、conclusion=success を確認し、さらに **2件のUpload artifact手順がSuccess** であることを確認する。全必須ジョブがSuccess以外ならマージしない。
4. Failure、Skipped、Pending、Cancelled、Timeout、Unknown、部分成功ではマージしない。Actions Artifactアップロードが原因でCI全体がFailureでもマージ不可。
5. PRがReady、master宛て、コンフリクトなし、未解決の重要なレビュー指摘なしであることを確認。必要な統合・修正を行ったら再度最新HEADのCIを通す。
6. 以上を満たした場合のみマージし、後からPR.merged=trueとmasterの更新を実際に検証する。ユーザーは有料ブランチ保護を不要と指定済み。

## 読み取り専用の事前検査

scripts/verify_merge_ready.py は最新PR HEADに対するWindows workflow全体と必須ジョブのSuccessをGitHubから照合する。保護設定APIは使わず、プラン制限があってもCI検査を実施可能。
GH_TOKENを安全な環境変数として用意し、Docker devコンテナで次を実行：

    docker compose run --rm -e GH_TOKEN dev python scripts/verify_merge_ready.py --pr <PR番号>
    docker compose run --rm dev python -m unittest tests.test_verify_merge_ready -v

これはGitHubのMergeボタンを物理的に止める仕組みではない。取得失敗・未完了・古いSHAはBLOCKEDとなり、マージしない。検証がPASSでも直前にPRとCIを再確認する。

## PR #29 と PR #31 の引継ぎ

- PR #29 https://github.com/hatsu07/pokemon_champions_assistant/pull/29 ：複数グループ承認のUI拡張。PR #30との重複によるコンフリクトがあり、グループラベル仮訂正など固有機能を統合してから最新HEADのCIを再実行する。
- PR #31 https://github.com/hatsu07/pokemon_champions_assistant/pull/31 ：CIと配布の分離、最新SHA監査、新チャット引継ぎ文書。
両PRの最新の完了状態はGitHubで毎回確認すること。本書の履歴が古くなっている可能性を考慮する。

## 新しいチャットへの依頼例

「hatsu07/pokemon_champions_assistant の開発を引き継いでください。AGENTS.md、docs/AI_DEVELOPMENT_AND_CI_POLICY.md、docs/CI_MERGE_GUARD_AND_NEXT_CHAT.mdと最新PRをGitHubで確認。GitHub Pro/branch protectionは不要。最新HEADに対するWindows CI全体と必須ジョブのSuccessを確認できない限り絶対にマージしない。」
