# Issue #44 – Champions対面根拠の生成とレビュー

この変更では、構築選択時に対面候補358×6の情報をChampions専用固定マスターから作成する。**新しい構築を戦術的に実戦検証済みと宣言する変更ではない。**

## 生成するデータ

自軍の6匹、相手マトリクスの358キーから、対面ごとに次の事項を別のJSONに保存する。

- Champions固定マスターの出典SHA256、取得日、レギュレーション、構築の型署名
- 自軍と相手のタイプ・種族値・基礎S、実際に登録した特性・持ち物・性格・配分、Championsにおける登録4技の威力/分類/優先度/習得確認
- 相手の**習得可能な**先制技候補、基礎威力が高い技候補（実際の相手の採用技とは断定しない）
- 不明な効果タイプ相性、実ダメージ、S順序、相手の型、安全な受け出し、処理役はUNKNOWNのままにする。

保存先：ローカルの data_root/live-team-assets/TEAM_ID/FINGERPRINT/matchup-evidence.json。証拠SHAとCONFIRMED/CONDITIONAL/UNKNOWNの件数はmanifest.jsonにも保存する。従来のマトリクス・ルックアップの358キーを維持する。

## 設定画面

対戦前に「設定 → 自軍構築」から「構築別358×6対面根拠JSONを確認」を押すと、セルの状態とファイルパスを表示する。「確認済み対面レビューJSONを取り込む」は実戦観測を人間が確認した場合に限り使用する。

入力JSONスキーマ名は champions-matchup-review-v1 。構築ID、regulation、player_reviewed=true、6個のmember_signatures、reviews配列が必要。各レビューには、opponent（マトリクスに存在する相手キー）、slot（0～5）、member_signature、status（CONDITIONAL/CONFIRMED）、reviewed_at（タイムゾーン付きISO日時）、reviewer、5文字以上のrationale、sources（実際に確認した根拠URLまたはファイルSHA256と日時）、conditionsを入れる。CONFIRMEDには独立の根拠2件以上とconditions.observed_opponent_movesが必須。

スキーマ、型署名、レギュレーション、承認フラグ、独立証拠件数を検査し、エラー時には前回の正しいレビューを保持する。構築・技・配分・レギュレーション変更時の古いレビューは無効にして保管し、新規構築へ転用しない。

## 状態の意味

- CONFIRMED：プレイヤー確認付きの根拠記録。機械が証拠内容の真偽を独立検証できたことを意味しない。
- CONDITIONAL：完全一致した既存型の評価再利用、または人手確認の条件付き実戦記録。
- UNKNOWN：新規型など未確認の対面。実戦LIVEマトリクスでは受け禁止・処理役未確定の状態のまま。
- マスターの基本技威力は**実ダメージではない**。技習得候補は**相手の確定技ではない**。元の対面評価と条件付きのプレイヤー観測は、未知の対面に無条件に移植しない。

## まだ必要なIssue #44の工程

1. Championsで効果・タイプ相性・変化条件が実証されたダメージ計算、先制技、メガ、S、持ち物・特性、弱点のマスター拡張。
2. 実戦の判明情報を人間が確認し、処理役と**安全着地**を別々に審査するシステムとレビュー記録の充実。
3. 90秒選出、40秒PREP、各ターン45秒を独立した複数の実対戦で検証し、応答時間・認識・判断精度を測定する工程。
4. これらを満たしたときだけPROVISIONALからLIVE_READYへ昇格できる、独立の合格ゲート。

そのため候補生成やレビュー登録だけではLIVE_READYにはならない。既存構築の正確な再利用であるREVIEWED_REUSEは現行運用を維持し、新構築のPROVISIONAL_UNVERIFIEDとは分ける。実対戦検証前にIssue #44を完全完了扱いにしない。

詳細の関係するコードは matchup_evidence.py、team_live_assets.py、settings_management.py、settings_dialog.py にある。
