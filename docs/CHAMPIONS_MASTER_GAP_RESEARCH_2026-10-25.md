# Pokémon Champions 不足固定マスター：Web調査・追加候補（2026-10-25）

**目的:** Issue #44の新規構築マトリクス・ダメージ検証に不足するゲーム固有仕様を収集する。Championsの公式・ゲーム内確認と、非公式専門解説・シミュレータ・他作品の仕様を区別し、未確定を既存LIVE戦術へ混入させない。**今回のファイルは証拠付き候補であり、Champions実機による独立確認済みの固定マスターではない。**

## 既存マスターの欠損監査（基準: master、2026-10-25）

| 対象 | 既存件数 | 不足・再照合が必要な理由 |
| --- | ---: | --- |
| 効果ルールseed `Pokemon_Champions_effect_rules_catalog_seed_v0_2.jsonl` | 892 | `UNMAPPED` **880**、`PARTIAL` **7**、`VERIFIED` **5**。ゲーム内再現を示す独立証拠はさらに必要。 |
| 特性マスター `Pokemon_Champions_MC_ability_master.csv` | 216 | `Champions固有出典URL`を持つのは**9/216**で207件は専用URL欄が空欄。現行Champions確認欄の「確認済み」はゲーム内発動の全件検証と同義でない。 |
| 技マスター `Pokemon_Champions_MC_move_master.csv` | 510 | 主要フラグ空欄244、RawFlags空欄4。型ごとの威力変化、守る、先制技相互作用などの構造化が必要。 |
| 使用可能ポケモン・習得技 | 各350 | 2026-10-25取得のChampionspedia（自称ゲームv1.2.0抽出）は**396バトルフォーム**。対象地域、メガを含む数え方、更新版が異なる可能性がある。**46体未実装という計算は不可**。 |
| 持ち物マスター | 166 | Championspediaの表示数も166で件数は一致。ただし発動条件・消費時機・場の相互作用の個別検証が必要。 |
| 18タイプ相性 | 既存の独立固定チャートなし | 基本18×18の型候補324セルを補助ファイル化。出典の公開JSONには**Stellarを含む19タイプ**があったため、Champions候補ではStellarを除外。18タイプチャート自体も、Championspediaが**派生データでゲーム直接抽出ではない**と明記。**未検証・LIVE自動導入不可**。 |
| ダメージ計算 | 独立した実機検証済み機構なし | 計算順、SP実数値式、最低ダメージ等の参考を収集。確1/乱1/安全着地を自動認定できる段階ではない。 |

**外部データ件数の不一致も未解決:** 2026-10-25のChampionspedia掲載は技512・特性215・持ち物166・フォーム396。別のChampions専用日本語サイトは技511・特性215・道具164と記載。**どちらかを正解として現行固定マスターに追加・削除しない**。現行レギュレーション・フォームの集計方法・掲載の更新時刻を同条件で照合する必要がある。

## 今回追加した候補マスター

1. `masters/Pokemon_Champions_mechanics_web_evidence_2026-10-25.jsonl`：**41件**。SP上限、Championsの実数値式、通常ダメージ式・補正順・乱数候補、まひ・ねむり・こおり・やけど・どく、ステルスロック/まきびし/どくびし/ねばねばネット/除去、いかく・ノーガード・はどうのぼうご・各種特性など。各行に個別のURL・確認日・候補値・条件・未検証フラグ。
2. `masters/Pokemon_Champions_type_chart_candidate_2026-10-25.json`：**18×18、324セル**。通常のタイプ相性表の**参考候補**。取得元 `otterlyclueless/pokemon-champions-data`にはChampionsにないStellar列と行を含む19×19があったため、明示的に除去。決して実機で完全確認できた表ではない。
3. `masters/Pokemon_Champions_damage_reference_cases_candidate_2026-10-25.json`：**4件**。専門サイトの計算例（ガブリアス→メタグロス、リザードン→ギャラドスの天候別）を再現性チェック用に保存。**公開計算機の例であり実際の試合で観測した証拠ではない**。

**厳格な非昇格ルール:** これらは元の6匹用LIVE判断ゲート、LIVEマトリクス、60秒ルックアップ、自軍構築の**4ファイル事前ロードには追加しない**。完全独立の調査・候補ファイルで、`live_autoload_allowed=false`。既存固定マスター、技/特性/持ち物/使用可能一覧、既存のゲーム内記録を変更しない。

## Web情報源と裏付けの強さ

| 入手元 | URL | 取り扱い |
| --- | --- | --- |
| Bulbapedia: Champions能力ポイント | https://bulbapedia.bulbagarden.net/wiki/Stat_point | Champions固有のSP 66/32、HP=base+SP+75、非HP=floor((base+SP+20)×性格倍率)。コミュニティ資料。 |
| Pokémon Showdown Champions mod | https://github.com/smogon/pokemon-showdown/blob/master/data/mods/champions/scripts.ts | 実数値計算について独立の実装参照。**実機仕様と同値の保証ではない**。 |
| Champions専門ダメージ解説 | https://pkmn-cp-dataportal.com/battle/damage/damage-order/ | Lv50・基礎式、天候→急所→乱数→STAB→相性→残補正、16段階乱数、例示数値。非公式シミュレーション解説。 |
| 最低ダメージ | https://pkmn-cp-dataportal.com/battle/damage/minimum-damage/ | 命中技の最低1と無効時0の切り分け。 |
| 状態異常一覧 | https://pkmn-cp-dataportal.com/status/ | **まひ行動不能1/8**やねむり・こおり等のChampions専用報告。**SV流用禁止・実機再照合優先**。 |
| 設置技 | https://pkmn-cp-dataportal.com/battle/priority/hazards/ | ステロ相性倍率、設置層・接地・除去・発動順など。 |
| 特性：いかく | https://pkmn-cp-dataportal.com/battle/ability/intimidate/ | 交換・メガ時の発動、反応特性。 |
| 特性：ノーガード | https://pkmn-cp-dataportal.com/battle/ability/no-guard/ | 命中必中とタイプ/特性無効・まもるを切り分ける。 |
| 特性：はどうのぼうご | https://pkmn-cp-dataportal.com/ability/aura-guard/ | メガルカリオZの接触技被ダメ半減の候補。 |
| Championspedia出典説明 | https://thechampionspedia.com/about | 自称ゲームデータ直接抽出v1.2.0。ただしタイプ相性とダメージ計算定数は**派生値**と明記。 |
| 公開タイプチャート候補 | https://github.com/otterlyclueless/pokemon-champions-data/blob/main/type-chart/effectiveness.json | 公開データはShowdown由来を含み、**Stellarを含むためChampions用固定マスターへの直接取り込み不可**。 |
| 別専用DB件数比較 | https://pkmn-cp-dataportal.com/ | M-C一覧・件数比較の参考。採用率は相手の確定型ではない。 |

調査日：**2026-10-25**。日本時間。**レギュレーションはM-Cを想定**するが、各非公式個別記事はregulation/timeが完全一致すると保証できない。2026-10-25ゲーム内実測を確認しておらず、出典は記事と公開コードのみ。公開元が実データのアップデートを行った後には再照合が必要。

## Issue #44を完了するための推奨検証順

1. **実数値/SP**：ゲーム内でB=100、SP 0/1/32、性格上昇/下降の各表示を採取し、非HP式とHP式を複数種・メガ後について照合する。
2. **タイプ相性**：18×18候補は採用せず、少なくとも弱点/複合4倍/無効/特性無効の実機動画・ログと差分照合して`PROVEN`セルだけ使用。Stellarは絶対追加しない。
3. **ダメージ計算**：4例を**公開計算機の合致試験**として使い、別途ゲーム内画面で乱数・丸め・持ち物・メガ・特性補正を検証して初めて確1/乱1を認定する。
4. **実戦の重大分岐**：まひ1/8・S半減・ノーガード・いかく/まけんき・メガルカリオZは、個別の発動スクショ/動画/ターンログで条件付き検証。
5. **戦術ゲート**：検証済み処理役と安全な受け出し先を別々に認定。新構築をLIVE_READYへ上げるには、独立のBlueStacks実機で90秒選出・40秒PREP・45秒ターンの複数試合E2E合格が別途必要。

**本PRで行うのは研究結果のマスター候補化と監査のみ**。ダメージエンジンの起動やLIVE選出の変更は意図的に行わない。候補を勝手に`VERIFIED`へ格上げせず、原本の実機データで差分を検証してから固定マスターへ承認統合する。
