# GitHub Actions の料金・CI実行節約方針（2026-10-26）

本方針は Pokémon Champions Assistant の継続開発に適用する。新しい ChatGPT / Codex チャットでも必ず参照する。
本ファイルは **Actions の課金設定を実際に変更した証拠ではない**。現在の利用量・予算・次回リセットは変わりうるため、最新情報を確認する。

## ユーザーの希望と優先順位
- GitHub Free・private repoを維持し、不要な有料プランへの移行とActions超過課金を避ける。
- **PR最新HEAD SHAに対するWindows CIワークフロー全体と必須ジョブの `completed/success` がマージ条件**。節約のためにこの品質ゲートを無視しない。
- GitHub Billing > Budgets and alerts で Actions 用の予算と超過時の停止設定をユーザーが確認し、必要な安全設定を行う。エージェントは設定済み・課金停止済みと推測しない。予算・請求関連のアカウント設定は利用者の明示指示なく変更しない。

## 2026-10-26 に共有された状況（履歴であり現在値ではない）
- Actions利用警告：当該サイクルの付属2,000分中1,800分（90%）を使用、残り200分。通知上のリセットは2026-11-01。
- Billing Overview：metered usage $11.18、included usage $11.18、差額 $0、Next payment due は「-」。**今後の追加料金が発生しない保証ではない**。
- 同日、PR更新で `windows-validation-artifact.yml` の複数回起動（キャンセル・失敗・成功）とmaster pushで `windows-development-release.yml` が実行されていた。実際の課金額を各runへ正確に配賦したわけではない。

## 守る運用
1. 作業は **Draft PR** を基本とし、Docker上のローカルテストを先に実行する。コード・テスト・ドキュメントの変更をまとめ、最後のHEAD SHAを固定した後にだけReadyへ変更する。
2. Ready中に細かいpushを繰り返さない。失敗した場合はDraftへ戻して修正をまとめ、最終SHAで新しい必須Windows CIを受け直す。Cancelled/Skipped/過去SHAのSuccessを合格扱いしない。
3. マージによるmaster pushでもWindowsで2アプリの再ビルド・開発用Releaseが起動するため、**必要な機能のまとまり単位でマージする**。配布回数を不用意に増やさない。
4. 予算が足りず最新HEADの必須CIを実行できないなら、**Ready・マージ・Releaseを延期する**。CIゲートの無断緩和、`[skip ci]` での検証回避、masterへの直接pushによる回避はしない。
5. CI節約の技術改善（軽量Linuxテスト/Windowsビルドの分離、変更パス条件、キャッシュ、重複実行抑制など）は別PRで検討し、検証の網羅性と最新SHAゲートを維持する。効果は実行時間・課金メトリクスで評価し、未実測の節約額を断定しない。
6. 作業完了報告は変更ファイル、最新SHA、CI実行状況、費用面の未確認事項を分けて述べる。料金画面や秘密情報の原本はリポジトリに追加しない。

## 新チャットの必須入口
`AGENTS.md` → `docs/CURRENT_DEVELOPMENT_HANDOFF.md` → 本ファイル → `docs/AI_DEVELOPMENT_AND_CI_POLICY.md` → `docs/CI_MERGE_GUARD_AND_NEXT_CHAT.md` を読み、GitHubの最新 master/PR/CI/Release 状況を確認する。
この方針がDraft PR上にしかない場合は未マージと明記し、最新masterの正式な変更と混同しない。
