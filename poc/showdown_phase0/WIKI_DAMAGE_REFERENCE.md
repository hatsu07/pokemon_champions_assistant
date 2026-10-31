# Wiki仕様による独立ダメージ検算（Phase 0 / Issue #139）

**実ゲーム互換性の認定ではありません。** 固定Champions Showdownが扱う通常ダメージの算術的な一部を、**Showdownへ依存しない**小さな参照計算器 `wiki_damage_reference.cjs` で検査します。ユーザーの実画面・ローカルGoldens・個別の対戦データはリポジトリに持ち込みません。

## コミュニティWikiの参照順位（2026-10-31改訂）

**BulbapediaのChampions固有記載をコミュニティ資料の第一候補にする。** 出典URL、確認日、Champions固有節の有無、未記載領域、使用レギュレーション、確度は `champions_wiki_sources.json` に明示する。全資料はゲーム公式・Pokémon HOME等公式・ユーザーの最新ゲーム内観測・Champions固定マスターより下位。別作品・一般世代の記事をChampionsルールとして推測補完しない。

主なBulbapedia参照先：
- [List of Pokémon in Pokémon Champions](https://bulbapedia.bulbagarden.net/wiki/List_of_Pok%C3%A9mon_in_Pok%C3%A9mon_Champions)：実装・フォームの候補確認（**本実装では固定使用可能マスターが第一ゲート**）。
- [List of moves by availability in Pokémon Champions](https://bulbapedia.bulbagarden.net/wiki/List_of_moves_by_availability_in_Pok%C3%A9mon_Champions)：Champions技の存在可否。
- [Stat](https://bulbapedia.bulbagarden.net/wiki/Statistic)：Champions固有の能力値計算式。HP=`Base+StatPoints+75`、その他=`floor((Base+StatPoints+20)*Alignment)`。
- [Knock Off](https://bulbapedia.bulbagarden.net/wiki/Knock_Off_(move))、[Protean](https://bulbapedia.bulbagarden.net/wiki/Protean_(Ability))：Champions固有の効果・更新点がある。
- [Damage](https://bulbapedia.bulbagarden.net/wiki/Damage)：**「Championsがこの式を使用するか未確定」「固有の補正が未反映」と記載された不完全資料**。Championsの完全なダメージ算式・補正順序の確定根拠には**使わない**。

### 既存の2次Wiki由来ゴールデンについて

このPRの `wiki_damage_reference.cjs` と `wiki_damage_vectors.json` が使用する数式・公開「ガブリアスのじしん→メタグロス」例は、**従来の2次Wikiに基づく暫定算術テスト**として保持する。Bulbapediaで独立に全式・補正順・端数が確認されたものへ**出典を付け替えたり、実機互換性確認と呼んだりしない**。出典不一致やChampions固有の未確定係数は`UNKNOWN`。Bulbapediaの個別効果の記載は今後の独立ルール・状態遷移テストの根拠候補とする。

## 出典（非公式／2026-10-31確認、レギュレーションM-C候補）

- [ダメージ計算の処理順](https://pkmn-cp-dataportal.com/battle/damage/damage-order/)：公開ページ更新日2026-10-30。基礎式、天候→急所→乱数→STAB→相性→その他、補正位置と五捨五超入、公開例のガブリアス「じしん」→メタグロス138〜164。
- 計算に使用する係数 `22` は資料掲載式の**数値計算係数**です。Championsの画面にゲーム内「レベル」が存在する、という観測を示すものではありません。Showdown内部の `level: 50` もシミュレータ実装パラメータです。

**未実装ポケモンは検証入力から排除します。** クレセリアを用いた他作品由来の数式例を一度混入させましたが、ユーザーのゲーム内確認により未実装と判明したため**入力JSON・比較コード・自動テストから削除**しました。今後は固定使用可能ポケモン・習得技マスターで実行前に照合します。

取得したWeb記事は非公式な**文献仕様の候補**です。公式・最新ユーザー確認・Champions固定マスターと矛盾したら未確定にし、SV/ZA等の未確認仕様で補完しません。Wikiや第三者計算機同士が同一コードに依存する可能性にも注意します。

## 対象・非対象

- **対応（算術）**：既に確定した**有効な攻撃実数値・防御実数値**、技威力、解決済みの倍率、単発直接ダメージの16乱数、基礎計算、天候・急所・STAB・相性・その他の所定位置での整数端数処理。倍率は明示的な整数比 `[分子,分母]` で入力し、未指定はエラー。Wiki例は物理技で検証。
- **非対応（イベント・型決定）**：攻防の実数値の生成、ポケモン/技合法性、タイプ相性倍率の自動推定、能力変化・やけど・壁の影響の自動算出、特性発動条件、へんげんじざいのタイプ変更、はたきおとすの持ち物除去、フォルム・メガ、技固定、天候やフィールドの発動・解除、複数回攻撃、追加効果、ダメージ後の回復・持ち物消費、乱数の生起確率、戦闘状態の復元。
- 呼び出し側に確定した倍率がない場合は**計算を実行しない**。値を空欄・null・他作品の一般仕様から補完してはいけません。返る `WIKI_ARITHMETIC_ONLY` は計算値であり、実戦の確定KOや安全受けを保証する判定ではありません。
- Wikiの端数順序に差異があれば参照側とShowdownの差として記録します。ソースの相違を理由に固定ChampionsマスターやLIVE判断ゲートを無断で書き換えません。

## ファイル

- `champions_wiki_sources.json` — Bulbapediaをコミュニティ資料の第一候補とする出典台帳。Championsのダメージ記事が未完成であることを明示。
- `wiki_damage_reference.cjs` — 独立した整数比/BigIntベースの算術。Showdown/Dexをimportしない。
- `wiki_damage_vectors.json` — **公開Wikiの例のみ**を入力・記事の期待範囲・URL・出典区分付きで記録。ユーザー個人の観測は入れない。
- `test_wiki_damage_reference.cjs` — 参照例、端数境界、合成の急所倍率、未知入力の拒否、無効相性。通常のNodeだけでテスト可。
- `wiki_damage_compare.cjs` — ローカル固定SHA Showdownで**Championsの使用可能マスターに存在するポケモンの公開Wiki例だけ**を16通り実行し、参照計算器の`rolls`と**値の配列全体を比較**。元ゲーム観測の一致とは別の`WIKI_SPEC_MATCH/MISMATCH`を返す。

## PR #149 レビューP1の修正：比較用セットの適格性

旧版では防御側メタグロスに未習得の `Tackle` が設定され、攻撃側の使用技以外を固定マスターと突き合わせていなかった。現行版はメタグロスを**`Bullet Punch`** に訂正し、登録チーム12匹のすべてについて**Battle生成前**に個体適格性を検査する。

- 登録可能なChampions通常個体・フォーム・種族値・タイプ：固定 `Pokemon_Champions_MC_usable_pokemon.csv` と固定Showdown種族キー双方で照合。未収録・未対応フォーム・マスター不一致は停止。 **複数のChampionsフォームが同一の固定Showdown種族キーに重複対応する場合（例：イキリンコの複数色）は、そのキー全体を保守的に除外して `UNKNOWN` 相当とし、今回使用しない種族の曖昧さで無関係なケースの検算を停止しない。**
- **全設定技**：固定技マスター・ポケモン別Champions習得技・固定Showdown習得データの3層に存在すること、技スロットが1〜4・重複なし、実際に比較する技が攻撃側の設定技に含まれることを確認。
- **特性**：固定使用可能ポケモン表の日本語特性名と固定Showdown種族の特性候補双方で照合。英日名の対応は今回の比較に必要な `Rough Skin→さめはだ`、`Clear Body→クリアボディ` のみ明示し、**未対応の訳・特性を自動で合法扱いしない**。
- **持ち物**：空欄（持たせない）またはChampions持ち物マスターに存在し、固定Showdownでも存在が確認できる道具だけを許可。メガストーンの対応種族が違う場合も停止。
- 性格・AP：固定Showdownで存在する性格と、各能力ポイント0〜32／合計66以下を確認する。レベル50はShowdown内部構築用の固定パラメータであり、ゲーム実測レベルではない。

出力が `WIKI_SPEC_MATCH` の場合も「この**合法性を検証した合成ケース**の数式が16乱数で一致した」という意味に限定。特性・道具の実際の発動タイミングやChampions実機互換性、任意の別フォーム等は**未検証**。

`test_wiki_damage_reference.cjs` は実CSV・固定Dexを読み込む統合系の正常・負例（未実装クレセリア、未習得Tackle、未設定攻撃技、控えの未習得技、異種特性、未知道具、不正フォーム、重複技、AP等）を追加した。自己申告モックで不正な技を合法とするテストは除去した。

## PR #149 再レビューP1：登録6匹・選出3匹・算術の分離

2026-10-31レビューで、旧フィクスチャの同種ガブリアス複数採用・両側の控え共用・3匹だけの登録が問題になった。今回の例は**攻撃側6匹・防御側6匹の独立したセット**へ変更し、各チームについて次の検査を行う。

1. 固定Champions使用可能ポケモン、技、習得技、特性、持ち物、タイプ、種族値、AP等、**全12個体セット**を確認。
2. 正本の**全国No.**を種族同一性の基準に用い、**各側の6匹内**で同名・リージョン違いを重複として拒否。相手側と味方側の間にこの制約をかけない。全国No.へ一意対応できないフォームは引き続き拒否。
3. 同じ**持ち物ID**のチーム内重複を拒否する。アイテム空欄は重複と数えない。双方の持ち物は独立して判定。
4. 固定Showdown SHAの `[Gen 9 Champions] BSS Reg M-C` に対して`TeamValidator.validateTeam`を**それぞれ6匹全体に実行**し、エラーがない場合だけ `Battle` に投入。
5. Team Preview後に両側の最初の3匹を `team 123` として選出して16乱数の計算結果を比較する。この選出は合成の固定入力であり、ゲームの実画面や人間の選出判断を示すものではない。

出力の`validated_registered_teams=2`、`validated_registered_team_members=12`、`selected_team_size=3`、`pinned_format_team_validator_passed=true`は上記の**固定ソースと合成チームでの検証**を意味する。Champions実ゲーム側のすべてのルールが検証済みという意味ではない。個別の数式比較はこれまでどおり、**公開二次Wiki1例の算術16値**でありGate A/Bは未合格。正常系・同種・通常＋リージョン・同一持ち物・空欄持ち物・異なる道具の負/正例は実マスターを読み込むテストで確認する。

## 再現

開発環境はWSL2内のリポジトリ＋Dockerに限定。WindowsホストにNodeをインストールしません。

```bash
docker build -f poc/showdown_phase0/Dockerfile -t champions-phase0 .
docker run --rm --network none --read-only --entrypoint node champions-phase0 --test test_wiki_damage_reference.cjs
docker run --rm --network none --read-only --entrypoint node champions-phase0 wiki_damage_compare.cjs
```

`wiki_damage_compare.cjs` の終了値：`0` は公開Wikiの選定例と固定Showdownの16値一致、`1` は少なくとも1例が相違、`2` は固定SHA・入力・依存などの理由で未実行。相違はコードかWikiかソース時点が原因の可能性があるため、**自動でゲーム真実・シミュレータ不具合と断定しない**。

ローカル既存の実ゲームGolden 11チェックの記録は従前どおり非公開のまま保持。ゲート判定は **A=UNKNOWN、B=UNSUPPORTED、総合NO_GO** のままで、途中局面復元はこの変更の対象外です。

## 次の拡張

高リスクかつ頻出の技/特性/道具から、**ルール発動条件と状態遷移をテスト可能な仕様台帳**に追加して、対象が増えても手動対戦が必須にならない状態を目指します。全ポケモン×全技を一度に独立実装することは受け入れ条件にしません。
