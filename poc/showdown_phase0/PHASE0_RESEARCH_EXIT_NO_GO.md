# Issue #139 — Phase 0研究PoCの終了判定（GO と NO_GO を別経路にする）

**2026-11-02 / M-C / Fixed Showdown `ccfaa20f32af363569cd21a9d501b2bc1ded26b1`**

## 何を終了できるか

Phase 0（技術PoC）には二つの異なる終了ルートがある。

1. **GO採用判定** — Championsゲーム実測に基づく必要計算機能の整合（Gate A）と、実観測からの途中局面復元・独立分岐の安全性（Gate B）、Windows製品実装/IPC/E2E/ライセンスの受け入れがすべて証拠付きで成立した場合のみ別途審査する。**現時点では成立しない**。これらを合成テストで代用しない。
2. **NO_GO研究終了** — 現時点の採用不可の根拠とリスクを明示し、従来LIVEを維持し、Phase 1へ進まないことを人間が承認する。**機能実装や実ゲーム全技の検証を無期限に続ける必要はない**。OpenであるPoCを研究上の結論として閉じることは、Showdownの本番導入を許可することではない。

PR #166の機械可読 `phase0-final-adoption-report.json` は `phase0_ready_to_close:false` を返す。これは **GO条件未達** を表す既存の安全な表記として保持する。PR #168の `phase0_no_go_research_exit.cjs` はこれを **決して`true`に書き換えず**、別の `research_disposition:` `NO_GO_EXIT_EVIDENCE_READY_FOR_MANUAL_REVIEW` を返す。

NO_GOでも**Issue #138の親IssueはOpen**、Phase 1/2/3のShowdown利用は止めたまま。Issue #139を閉じるかどうかは最新HEADの必須CI、レビュー、ユーザーの終了判断を受けて手動で決める。研究用統合ブランチは残し、LIVEマスターやGPT経路を置き換えない。

## チェックできた事実・未達項目

| 軸 | 完了した技術PoC | GOに必要だが未達の証拠 |
| --- | --- | --- |
| Gate A `UNKNOWN` | 固定マスター・二次資料・合成の10機能別監査、やけど＋壁16乱数中4件の1点差を再現 | 高リスクChampions仕様の実機確認と矛盾解消。確定KO/安全受け/行動順の保証には利用不可 |
| Gate B `UNSUPPORTED` | 12完全合成状態の復元、部分観測2件`INCONCLUSIVE`、26部分観測機構で`UNKNOWN`・候補分離・観測矛盾を検査 | 実ゲームの可視情報から、隠れた技・道具・PP・AP・メガ履歴・キュー/RNGを勝手に確定しない復元/分岐 |
| 性能 | Linux 127合成ケース＋同一OS上の3 Nodeプロセス、計測値・分布 | Windows製品・Android OCR→状態→worker→GUIのE2E分布、ピークRSS・長期安定 |
| 配布/IPC | 固定SHAのコンテナビルド、Windows native Nodeの単体CI | onedir内蔵、オフライン起動/異常終了/復旧、旧state返信の拒否 |
| ライセンス | upstream MITとLICENSE存在 | npm依存物SBOM、NOTICEとWindows配布物の対査 |
| 実機Golden | Issue #139のユーザー照合6ケース/11チェック（マリルリ値・PP、1回の78ダメージ） | 6独立対戦や広範なゲーム互換を意味しない。**本CIでGoldenや元画像を再読込しない** |

## 実行・自動検証

既存Linux Phase 0 CIが5診断JSONから生成したレポートを渡す。

```bash
node poc/showdown_phase0/phase0_no_go_research_exit.cjs \
  phase0-final-adoption-report.json > phase0-no-go-research-exit.json
```

このJSONで、固定SHA/M-C、Gate A/BとNO_GO、合成と実機の区分、配布/E2E/ライセンスの未検証、非公開Goldenの未使用、**本番導入禁止・自動クローズ禁止**を検証する。入力が欠落した場合や、根拠のないゲーム互換・E2E達成・採用`GO`への書換えは失敗する。元レポート自身の5診断JSONの完全性は **PR #166の契約** が検証し、終了判定はその後の工程で実行する。

## 人間による終了判断の手順

- [x] Phase 0の研究用コード・10機能別差分とGate B仮説検証を整理した
- [x] 5軸の採否レポート、代替方針、プライバシー/本番隔離を文書化した
- [ ] 最新PRのPhase 0 Linux・Windows native Node・Windows desktop全ての必須CIが同じHEADで成功
- [ ] PRのコードレビューを完了し、採否証拠・NO_GO判定の誤昇格がないことを確認
- [ ] ユーザーが**「Phase 0を採用不可（NO_GO）として終了し、#139を閉じる」**と判断
- [ ] #139に最終結論と未検証タスクの追跡方法を明記して、手動で`not_planned`（不採用研究終了）などの適切な理由でクローズする

**重要:** `not_planned`でクローズしてもPhase 0の「GO合格」ではない。再挑戦には新しい実機根拠、Gate A/Bの独立再評価、製品配布/E2E/ライセンス証拠に基づく新たな採用判断が必要。Pokémon Championsやレギュレーションの更新があれば固定SHA/マスターの差分を確認し、別途研究再開を判断する。
