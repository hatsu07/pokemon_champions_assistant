# v2.7 追加ソース・指示の収録台帳

## 追加内容と信頼度
| 分類 | ファイル | 収録方法 | 信頼性・扱い |
|---|---|---|---|
| 旧構築履歴 | `masters/Pokemon_Champions_MC_team_2026-09-29.csv` | アップロード実ファイル複製 | LIVEでは使用しない |
| LIVEソース定義 | `masters/PROJECT_INSTRUCTION_live_matrix_addition.txt` | アップロード実ファイル複製 | `live_decision_gate.txt`と競合時はゲート優先 |
| 対戦ログ | `private_history/Pokemon_Champions_battle_log.xlsx` | Excelバイナリを無改変コピー | Git/CI非公開、未解析 |
| 対戦データ | `private_history/Pokemon_Champions_battle_data.xlsx` | Excelバイナリを無改変コピー | Git/CI非公開、未解析 |
| 環境過去記録 | `knowledge/Pokemon_Champions_environment_snapshot_2026-10-21_archive.txt` | アップロード実ファイル複製 | **アーカイブ日=収録日**。最新環境とは限らない |
| 環境データ取得元 | `knowledge/Pokemon_Champions_environment_data_source_policy.md` | アップロード実ファイル複製 | Web参照は試合前・試合後のみ |
| 追加の過去設計 | `knowledge/Pokemon_Champions_Realtime_AI_System_Design_v1_0.md` | アップロード実ファイル複製 | 歴史資料。後続統合仕様が優先 |
| 効果旧スキーマ | `knowledge/Pokemon_Champions_effect_rule_schema_v0_1.json` | アップロード実ファイル複製 | 歴史資料。v0.2が正本 |
| プロジェクト運用方針 | `docs/Pokemon_Champions_Project_User_Guidance_Reconstructed_v2_7.md` | 提供されたユーザー方針から再構成 | **プロジェクト設定画面の逐語原本ではない** |
| O/X評価方法 | `docs/Pokemon_Champions_CODEX_OX_Postmatch_Method_v2_7.md` | 提供された運用方針から再構成 | **対戦後のみ** |
| ノウハウの所在 | `knowledge/README.md` | 整理版 | 既存マスターの置き換えではない |

元ファイルのSHA256は `assets/source_copies_v2_7.json` に記録。現在のChatGPTプロジェクトにのみ存在する指示・過去チャットを完全な形で出力したものではありません。ユーザーがプロジェクト設定の元テキストを手動で提供した場合は出典を付けて照合し、矛盾を記録して更新してください。

## Codexの参照順位（役割ごと）
1. 戦術：`masters/Pokemon_Champions_MC_live_decision_gate.txt` + 現行構築 + LIVE選出マトリクス。
2. API通信：`docs/Pokemon_Champions_Contract_And_State_Reconciliation_v2_3.md` + `contracts/`。
3. システム：`docs/Pokemon_Champions_Integrated_Architecture_v2_0.md`、`docs/Pokemon_Champions_P0_Implementation_Spec_v2_1.md`。
4. Windows/MSI：`docs/Pokemon_Champions_WSL_Docker_Windows_MSI_Design_v2_6.md`。
5. オフライン学習・改善：`knowledge/README.md`、O/Xガイド、必要時だけ `private_history/`。
6. 補足再構成：`docs/Pokemon_Champions_Project_User_Guidance_Reconstructed_v2_7.md`。競合時に独自のLIVE判断ゲートとして扱わない。
