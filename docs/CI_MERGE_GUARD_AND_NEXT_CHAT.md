# CI成功の手動マージゲート・新チャット引継ぎ（2026-10-23）

開発開始時に AGENTS.md、docs/AI_DEVELOPMENT_AND_CI_POLICY.md、本書を必ず読み、最新のGitHub PR・HEAD SHA・CIを直接再確認する。過去のチャットの報告では代用しない。

## マージ事故と現在の方針

PR #30をCI全体Failure（GitHub Actions Artifacts quota超過）の状態でmasterにマージしたのは誤り。コードテストの成功とCI全体Successは違う。
ユーザーは2026-10-23、GitHub Proへのアップグレードを行わず、private repoのブランチ保護を不要と明示した。
master.protected=falseは既知の制約。GitHubのMergeボタンは自動的に無効にならない。エージェントが厳格なCI確認を行う。ユーザーの指示なしにリポジトリを公開しない。

## Required PR CIとmaster限定GitHub Releases配布

Ready PRのWindows CIは、オフラインテスト、両Windows PyInstallerビルド、両GUI起動スモーク、最新PR HEADの検証をすべて実施する。必須job名は `desktop-windows-validation`。**PR CIでActions Artifactsをアップロードせず、Releaseを公開しない。** PR HEAD/CI HEAD/checkout一致と全体Successがマージ条件。
masterへのマージ後、別の`.github/workflows/windows-development-release.yml`が検証済みPRとmasterのファイル内容・Windows CI成功を照合後、単体/統合/GUIテストをスキップしてWindows再ビルド・最小スモーク・2ZIP生成・master SHA確認を行い、GitHub Releasesの**開発用プレリリース**へ公開する。masterのリリースworkflowで両ZIPの実在・同梱SHAと結果を別途確認。PR検証SuccessをRelease公開Successと言い換えない。
`.github/workflows/windows-development-release-cleanup.yml`が毎日、**14日以上かつ新しい3件以外**のコミットタグ付き開発プレリリースと対応タグのみ整理。stable Release・一般タグ・Draftを消さない。詳細は`docs/WINDOWS_DEVELOPMENT_RELEASES.md`。WSLの`scripts/prune_actions_artifacts.sh`は過去Actions Artifactsの履歴整理専用。
ユーザーが実際に利用するゲーム映像による検証・画面分類精度・正式MSIは別工程。BlueStacksは必須前提ではない。

## Phase 1 統合ブランチ専用の厳格ゲート（2026-11-02 / Issue #140）

2026-11-02のユーザー判断とマージ済みPR #174により、`integration/issue-138-showdown-local-engine` を**唯一の追加許可ベース**として扱う。これ以外の無許可ベースは`scripts/verify_merge_ready.py`で拒否する。**GitHub Releasesの自動配布は引き続きmaster専用**であり、統合ブランチ宛PRのマージがmaster配布を開始することはない。

- 統合ブランチ宛のReady PRは、最新`head.sha`に対し**Windows workflow全体＋`desktop-windows-validation`**、および**`.github/workflows/showdown-phase1-foundation.yml`全体＋`phase1-isolated-foundation`**の双方が`completed/success`の場合だけマージ候補にできる。どちらかが未実行・古い・失敗・Skip・Cancelなら禁止。
- PRのDraftは依然マージ不可。PR #144の開発が進んでも、明示的なレビューとCI結果を確認する前にマージしてはならない。
- 科学的なGate A=`UNKNOWN`／Gate B=`UNSUPPORTED` は**開発を止める条件ではない**（PR #174）。ただし計算互換性を実機確認済みと報告しない。
- `tests/test_verify_merge_ready.py`の統合ブランチ用正例・負例、および`tests/test_issue_140_foundation.py`の合法性/分岐応答/旧revision排除を厳格な最低条件として継続。

以降の「master宛て」という旧要件は、**masterへの通常PRとmasterからのRelease**について引き続き正しく、上記の明示的な例外と矛盾しないように解釈する。

## 手動・AIエージェントによる例外なしマージ判定

1. 開発は作業ブランチ + Draft PRで行い、変更をすべてコミットし最新HEADを確定したあとReadyへ切り替える。
2. PR HEAD、CI workflow_run.head_sha、実際にcheckout/buildしたSHAが完全一致することを確認。古い成功は使わない。
3. **PR最新HEADに対する最新Windows workflow run全体** とjob desktop-windows-validation がともに status=completed、conclusion=success を確認する。PRではZIP公開は行わない。全必須ジョブがSuccess以外ならマージしない。
4. Failure、Skipped、Pending、Cancelled、Timeout、Unknown、部分成功ではマージしない。Actions Artifacts上限に左右されるPR配布ステップは現行CIから廃止済み。
5. PRがReady、master宛て（Phase 1用の明示例外：`integration/issue-138-showdown-local-engine`宛て）、コンフリクトなし、未解決の重要なレビュー指摘なしであることを確認。統合宛てでは上記のPhase 1 Linux CIも必須。必要な統合・修正を行ったら再度最新HEADのCIを通す。
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
