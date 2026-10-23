# CI・master保護・新チャットでの開発引継ぎ（2026-10-23）

この文書と [../AGENTS.md](../AGENTS.md)、[AI_DEVELOPMENT_AND_CI_POLICY.md](AI_DEVELOPMENT_AND_CI_POLICY.md) を、新しいチャットやCodexで**作業開始前に毎回読む**。最新のPR・CI・保護状態は必ずGitHubから再取得し、この文書に書かれた過去の状態で代用しない。

## 何が起きたか（再発防止）

2026-10-23、PR #30をWindows CI全体が`failure`のままmasterへマージした（merge SHA `2b8232dbbbdfc6d20e49e770fe09eca1d41d2d97`）。単体/GUI/Windowsビルド成功に対し、`actions/upload-artifact@v4` は `Artifact storage quota has been hit` で失敗。**部分成功でもCI全体のfailureはfailure**。GitHub側の`mergeable: true`はCI成功を意味しない。これを例外扱いしてはならない。

## PRの必須CIと配布物を分離する

`.github/workflows/windows-validation-artifact.yml` の必須PR検証は、Ready PRの各最新SHAでWindowsオフラインテスト・2つのPyInstallerビルド・両GUI起動スモーク・PR HEAD一致を検証する。ジョブ名は **`desktop-windows-validation`**（リポジトリRequired status checkとして登録する文字列）。

PRのCIではGitHub Actions Artifactを**アップロードしない**。理由はArtifacts保存容量上限がコードの品質と独立していて、失敗するとPR CI全体を赤くするため。配布成果物が得られたと報告してはならない。

開発用成果物をダウンロードする必要がある場合は、別途GitHub Actions画面から **Windows desktop development validation** の `Run workflow` で `publish_artifacts=true` を明示して手動実行する。この実行では同じWindows検証とビルドに成功した後、2つの`DEVELOPMENT_ONLY` ZIPをアップロードする。容量上限でアップロードできなければ**手動配布の失敗**であり、成功として扱わない。利用可能になるまで既存の不要なArtifactsをGitHubのActions管理UIから明示確認のうえ整理し、GitHub側の容量集計を待つ。既存Artifactsの削除を自動実行しない。外部クラウドへ画像やデータを移動しない。

PRの成功と配布物の存在は別に報告する。実機BlueStacks/Championsでの認識精度・動作確認とも別。

## GitHub上でマージを物理的に止める設定（未設定なら**ブロック中**）

2026-10-23のAPI確認では `master.protected=false`。非公開リポジトリのRulesets APIは「Upgrade to GitHub Pro or make this repository public」(403)、branch protection読取も接続権限制限(403)だった。GitHub公式ドキュメントでは**private repoのbranch protection/RulesetsはGitHub Pro、Team、Enterprise等の対象プランが必要**。ユーザーのプランを推測しない。リポジトリを無断でpublic化しない。現GitHubコネクターにはルール書込ツールがなく、ブランチにファイルを追加するだけではサーバー側のマージ拒否を強制できない。

管理者が対応可能なプラン・権限を用意した後、GitHubで次を設定する（操作しただけで完了とはせず、API/UIで有効状態を再確認する）。

1. [Settings → Branches](https://github.com/hatsu07/pokemon_champions_assistant/settings/branches) で `master` 向けBranch protection ruleを作成（またはRulesetsで同等設定）。
2. **Require a pull request before merging** を有効化する。自身が唯一の開発者なら必須レビュー数の設定には注意（本人は自PRを承認できない）。
3. **Require status checks to pass before merging** を有効化し、Windows workflowのjob `desktop-windows-validation` を必須にする。チェック候補は該当CIを一度走らせた後に出る。**Require branches to be up to date before merging** も有効化。
4. **Do not allow bypassing the above settings**（管理者も例外なし）を有効化する。force push・branch削除の許可は無効のままにする。
5. 保存後、`master.protected=true` とRequired checkの有効状態を確認。可能なら意図的に失敗する別の安全なテストPRで、マージボタンが無効になることを確認する。**設定できなければ「GitHub強制ブロックは未達」と報告する。**

`skipped`チェックをGitHubが合格相当と扱う場合があるため、エージェント側では**明示的に`completed/success`**であることも別途必須条件とする。Draft PRはCIがスキップするが、Readyへ移すまでマージ対象にしない。

## すべてのチャット・エージェント共通の厳格マージゲート

- 新しい作業は新ブランチ + **Draft PR**。まずコード、テスト、ドキュメントを完成。Latest HEAD SHAを固定する。
- Readyへ変更してWindows CIを実行する。追加コミットが発生したら古い結果を破棄し、最新HEADで再実行。
- マージ前に `PR.head.sha` = `workflow_run.head_sha` = checkoutしたSHA、ジョブ`desktop-windows-validation` とworkflow全体の`completed/success`、baseが`master`、PRがReadyであることを**すべて**確認。
- さらに**`master`に必須status check + 管理者バイパス禁止のルールが実際に設定されている**ことも確認する。接続制限やプラン制限で確認できない場合はfail closedとして**マージしない**。ユーザーが単に「続けて」と言っても緩めない。
- Fail/Cancelled/Pending/Skipped/Timeout/Unknown、古いSHA、ジョブ部分成功、配布成果物がないのに配布成功という報告、GitHub UI上でmergeable=trueであることはマージ許可にならない。
- CI失敗は修正して新SHAで再実行する。回避のためのテスト削除、必要な品質チェックのスキップ、テスト失敗の許容化をしない。
- 新しいチャットでは引継ぎを推測せず、GitHubにある`AGENTS.md`と本書・最新PR HEAD・CIを再取得する。前チャットの成功宣言は根拠にならない。

## 新チャット開始用の依頼例

「`hatsu07/pokemon_champions_assistant` の開発を引き継ぐ。まず `AGENTS.md`、`docs/AI_DEVELOPMENT_AND_CI_POLICY.md`、`docs/CI_MERGE_GUARD_AND_NEXT_CHAT.md` と最新のPR/CI/ブランチ保護状態をGitHubから直接確認。Draft PRで実装し、必須Windows CIの最新HEADに対するcompleted/successとサーバー側master保護ルールの有効性を確認できない限り絶対にマージしない。実機未検証を実機成功と扱わない。」

## 確認リンク

- [リポジトリ](https://github.com/hatsu07/pokemon_champions_assistant)
- [Actions](https://github.com/hatsu07/pokemon_champions_assistant/actions)
- [PR一覧](https://github.com/hatsu07/pokemon_champions_assistant/pulls)
- [master保護設定](https://github.com/hatsu07/pokemon_champions_assistant/settings/branches)
- GitHub公式: https://docs.github.com/en/repositories/configuring-branches-and-merges-in-your-repository/managing-protected-branches/about-protected-branches
