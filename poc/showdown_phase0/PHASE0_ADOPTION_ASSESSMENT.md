# Phase 0 採否評価 — Issue #139 / 親 Issue #138

- **評価日:** 2026-11-02（JST）、現行 M-C
- **対象:** 統合ブランチ `integration/issue-138-showdown-local-engine` 上に蓄積されたPhase 0診断。固定Showdown SHA `ccfaa20f32af363569cd21a9d501b2bc1ded26b1`、format `gen9championsbssregmc`
- **評価の目的:** ShowdownをChampionsランクマ用のローカル計算・意思決定エンジンとして採用可能かどうか。Pokémon Championsで確認されていない過去作の仕様を根拠にしない
- **現在の判定:** **Gate A = UNKNOWN / Gate B = UNSUPPORTED / Phase 1採用 = NO_GO**。Phase 0の正式な判定は「**現時点では採用条件未達**」。調査・検証を終了したという意味ではなく、Issue #139は開いたまま。LIVEと本番判断を切り替えない

## 証拠階層を分離する

| 階層 | 正確な意味 | ここからは認定できないこと |
| --- | --- | --- |
| 固定Championsマスター・公式仕様 | 使用可能種族/技/特性/持ち物、固定版との静的差分 | 技の実行時効果やゲーム中の計算結果を全面保証 |
| Champions専用二次資料・独立式 | 記載された計算順/端数を参照する局所計算 | 他作品との共通仕様、実ゲームでの一致 |
| 固定Showdown合成Battle | 特定SHA・合法登録6→選出3の特定局面の状態遷移・HP差・順序 | 現行Championsの全局面・RNG分布・LIVE確定KO |
| 実ゲーム局所Golden（Issue #139に記録） | ユーザーがローカルで照合したマリルリの能力値/PPと単発はたきおとす観測 | 独立した6試合の再現、全特性・道具・天候・メガ・局面復元の互換性 |
| 製品化・配布・性能 | Windows同梱・IPC・E2E・ライセンス監査を**別途**実行して初めて評価可能 | Node単体3プロセス成功で製品性能達成を主張 |

レポート生成器 `phase0_final_adoption_report.cjs` は、Linux CIで同じコミットから生成した **Gate A / Gate B / 部分観測 / 127代表合成状態 / 3プロセス検証** の5件のJSONを入力する。実ゲーム観測の生データ、OCR画像、ローカルGoldenファイルを読み込まない。Issue #139掲載の**6ケース・11チェック**は「ローカルでユーザー照合済みの履歴要約」欄へ分け、**今回のCIで11件を再実行したという記載は禁止**。

## 5つの受け入れ軸と採否

| 必須軸 | 実際の証拠 | 未達・制限 | 判定 |
| --- | --- | --- | --- |
| **A. Champions計算互換性** | `gate_a_evidence_inventory.cjs` の10機能グループ。AP、技の優先度、まひ、天候、技効果、やけど＋壁を独立参照・合成実行で監査 | 10グループのChampions実機広範互換は未確認。やけど＋壁は二次資料の1/4一括とShowdownの1/2×1/2で既知1点差が4乱数。実機の正解は不明。端数・メガ・高速エース等の確定判定不可 | **UNKNOWN** |
| **B. 中途局面復元** | `gate_b_probe.cjs`：完全合成状態12件 `PASS_SYNTHETIC`、部分観測2件 `INCONCLUSIVE`、独立入力11状態。`partial_observation_probe.cjs`：26の部分観測・仮説機構を評価 | 可視部分だけから相手の技/PP/持ち物/特性/AP、メガ前履歴、RNGや未解決キューの唯一の実状態を再構成できない。合成の復元成功を実ゲーム復元成功へ数えない | **UNSUPPORTED** |
| **P. 代表局面と性能** | `representative_states_probe.cjs`：127合成状態、8機構群、Warm Node段階別 P50/P95/P99。`cross_process_synthetic_probe.cjs`：同一OSで127ケース×3別Nodeプロセスの照合と壁時計計測 | Windows製品E2E P95、Android→OCR→IPC→PySide6表示、常駐安定性、ピークRSS、長期リークは未計測。3プロセスは3独立CI runでも3独立ゲーム試合でもない。127入力の多様性は粗い対面投影を含むため実戦網羅性は限定的 | **合成PoCの限定達成／製品性能未確認** |
| **D. Windows配布・IPC** | ネイティブWindows Nodeで固定Showdownの選出/復元/プロセス診断を実行する別CIは存在。LinuxのDockerでは固定SHAからビルド可能 | 本番PyInstaller onedir同梱・Node worker IPC・タイムアウト/クラッシュ回復・オフライン実行を含む製品テストは未実施。PR最新HEADのWindows CI完了はレビュー時に**別途**確かめる | **未検証** |
| **L. ライセンス・再配布** | Showdown upstream MITの報告、Docker buildで上流LICENSEファイル存在を確認 | npm依存ライセンスの一覧、ソース改変の権利表示、SBOM、Windows同梱時のNOTICEと配布ファイルの照合は未確認 | **未検証** |

**この5軸は相互に代替しない。** A/Bの未達を127合成ケースで相殺しない。D/Lの未達をLinux CI greenやWindows native Node greenで相殺しない。「3秒P95」「選出10〜15秒」は改善目標であって製品E2Eの実測結果ではない。

## PR #160 / #165から継続する安全な解釈

固定Showdownでは、ガブリアスの「じしん」が、やけど・リフレクター条件でカメックスへ与えるHP減少は、乱数86/90/91/98%において **21/22/22/24**。Champions専門二次資料に記載された1/4補正一括計算では **22/23/23/25**。PR #165は固定Showdownの実行ターンとHPログで差異を再現したが、**Champions実機の正しい方式を立証したものではない**。二次資料間の式全体も一致していないため、合成一致を確定KOや受け出し安全の証拠に昇格しない。

## 利用方法と検証可能な出力

Linux CIは既存の診断を順に実行後、次の5ファイルを渡す（いずれも合成診断JSON、GitHubへ実ゲームのローカルデータを送らない）。

```bash
node poc/showdown_phase0/phase0_final_adoption_report.cjs \
  phase0-gate-a-evidence.json \
  phase0-gate-b.json \
  phase0-partial-observation.json \
  phase0-representative-states.json \
  phase0-cross-process.json > phase0-final-adoption-report.json
```

`phase0-final-adoption-report.json` は機械可読の**一回の合成CI集計**。Gate A/Bの形式、データ件数、固定SHA・M-C、端数差の存在、private Golden未読込、実機証拠0をfail-closedで検証する。入力を欠落させる、実機互換を`CONFIRMED`に偽装する、部分観測を唯一の復元済み状態へ変更する、E2E計測済みと偽る変更は拒否する。

性能のP50/P95/P99は**CI実行ごとに変化する観測値**なので、マークダウンへ固定数値を転載しない。CI診断JSONの`timings`・`samples`を基準とし、E2E目標の達成判定には使わない。GitHub ActionsのPR成功は実機・製品採用合格を意味しない。

## 方針比較

| 選択肢 | 現時点の判断 | 根拠と次の条件 |
| --- | --- | --- |
| Showdown全面統合・LIVE計算切替 | **採用しない（NO_GO）** | A/B不合格。Windows同梱・IPC/E2E/ライセンスも未確認 |
| ShowdownをPhase 0・オフライン限定で使い続ける | **研究継続可** | 最新固定マスター・資料差・実ゲーム照合を収集し、確定行動としてLIVEへ返さない |
| ゲーム確認済み局所計算器または代替エンジン | **比較・研究のみ** | 実機・Champions固有仕様、途中状態、Windows配布、安全なUNKNOWN処理を独立に検証。poke-engine等の互換性は現時点で保証しない |
| 現行のLIVEマトリクス＋GPT経路 | **維持する** | 実対戦の検証・記録・改善を継続。開発版Showdownの推定で既知ゲーム情報を上書きしない |

## 最小の残課題・次の判断ポイント

1. **実ゲームとの高情報量な照合**：新規個体を大量に準備せず、既存ログまたは自然な対戦で、攻防双方の条件と直前直後整数HPが確認できる例を少数追加。既存ローカルGoldenの対象・独立試合数も分けて記録する。
2. **部分観測からの安全な再構築**：実画面から読める確定値に証拠IDを付け、未確認の相手技/持ち物/PP/AP、メガ前履歴・天候残余・キュー/RNGを`UNKNOWN`とする。完全合成状態の復元を根拠に、非公開相手セットを確定しない。
3. **Windows配布/IPC/ライセンス**：Nodeを配布物に含める場合、オフラインで起動できること、worker異常終了・タイムアウト・古いstate_revision/応答破棄、ライセンス通知とSBOMをテストする。
4. **計算から表示までのE2E**：Androidミラー→認識→局面→IPC→表示の分布と、正常・タイムアウト・失敗ケースを別々に計測。P95目標を実測達成と混同しない。

Phase 0の未達が明確なため、**現段階の統合方針は「研究用コードだけ保持。本番接続なし」**。改善の実証と合否判定の再レビューまではIssue #139を完了/合格扱いせず、親Issue #138のPhase 1へ自動進行しない。
