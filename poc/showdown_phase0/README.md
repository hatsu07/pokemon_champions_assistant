# Issue #139 / Phase 0 — Pokémon Showdown Champions 技術PoC

## PR #164（案）：天候・天候特性のSと行動順／5ターン・上書き監査

- Champions固定マスターで「ハリーセン／すいすい」「ドリュウズ／すなかき」「フシギバナ／ようりょくそ」「ツンベアー／ゆきかき」の4種族・特性・S種族値を固定照合。Champions専用二次資料[GameWith天気](https://gamewith.jp/pokemon-champions/561577)と[AP仕様](https://pkmn-cp-dataportal.com/battle/rule/stat-points/)に基づくShowdown非依存S計算器を追加。
- 13件の限定条件：対応天候と不一致天候、無天候、まひ・スカーフ・S+1・トリックルームの一部組合せ。**複合補正の整数端数が適用順序によって異なる**雨＋すいすい＋スカーフの奇数S条件は `SOURCE_UNKNOWN_MODIFIER_ORDER` とし、固定Showdownの実行SをChampions側期待値に昇格しない。
- 固定M-Cにおける合法な登録6→選出3を双方に作り、実際に使用された2つの「つるぎのまい」の技ID・優先度・順序を監査。場の天候は**合成ターン前提条件**としてセット。別にガブリアスの「あまごい」とメタグロスの「にほんばれ」を**実際の同ターンの技として実行**し、上書きと5ターン経過を固定Showdown合成局面で確認。これは実機証明ではない。
- **天候延長に資料間の相違**：[GameWith天気仕様](https://gamewith.jp/pokemon-champions/561577)は「延長できない」とし、[Championsデータポータル](https://pkmn-cp-dataportal.com/battle/field/weather-vs-terrain/)は延長道具で8ターンと記載。どちらも二次資料のため `SOURCE_CONFLICT` として保留し、道具付きの継続ターンは結論を出さない。
- ターン中の天候変更による同ターン内Sの再判定、登場時天候特性の発動順、能力抑制・終盤残余天候、天候ダメージの端数は範囲外。過去作品の同仕様推定は禁止。実ゲーム検証0件・Golden未読込・本番LIVE不変。**Gate A=UNKNOWN / Gate B=UNSUPPORTED / 採用NO_GO** を保持。


## PR #163 レビューP2対応：出典強度・まひ行動不能の実行証人

- 出典台帳のURLだけでなく、`sources[].basis`の説明全文と`not_verified`の6項目内容を独立定数と照合。出典を実機検証済みに偽装した変更や、未知範囲を削除した変更は fail-closed。Gate A の`unverified`と固定Showdownソース検査の`evidence`も実機根拠に昇格させない。
- `|cant|p2a: Metagross|par`を、行動直前から`par`が付いたメタグロスの実行失敗イベントとして、固定seed／側／ポケモン名／前状態付きで記録する。行動不能0件、動けた試行0件、別原因の失敗、別側の失敗、行動不能なのに同じ側の実行技ログがあるケースは失敗。固定48seedの成功と失敗を両方確認するが、**12.5%の実機確率分布を推定しない**。
- 記録するイベントは固定Showdownの合成ターンのみ。非公開Golden・実際のChampionsバトル・本番LIVEには変更なし。Gate A=`UNKNOWN`、Gate B=`UNSUPPORTED`、採用`NO_GO`。

## Phase 0：Championsまひ・S補正・行動順の独立監査（PR #163）

`champions_paralysis_order_ledger.json`は、Champions専用資料の[まひ](https://gamewith.jp/pokemon-champions/559883)・[状態異常](https://www.serebii.net/pokemonchampions/statusconditions.shtml)・[Sライン](https://pkmn-cp-dataportal.com/battle/priority/speed-stats/)・[トリックルーム](https://pkmn-cp-dataportal.com/battle/priority/trick-room/)を出典として固定する。まひによるS半減・端数切り捨て、行動不能確率 **1/8（12.5%）** は**資料仕様**として扱い、実機確認済みとはしない。固定ShowdownのChampions専用`conditions.ts`の抽選`randomChance(1,8)`と、継承元のS半減・切り捨て処理も独立して点検するが、これはソース実装の確認であって確率の実測証明ではない。

`champions_paralysis_reference.cjs`はShowdownの戦闘コードを呼ばず、固定マスター由来のガブリアス・メタグロス実数値と資料のS倍率から期待順を生成する。複合S補正は**演算順を入れ替えても結果が同じ場合**に限り速度値を検証し、奇数S93にまひ＋スカーフ＋S+1が重なり103/104に分かれる条件では `SOURCE_UNKNOWN_MODIFIER_ORDER` を保留する。保留条件でShowdownが出した一つの数値をChampionsの期待値と見なさない。

`champions_paralysis_probe.cjs`は両側のChampions合法登録6匹→選出3匹の固定Battleに、まひ・能力上昇・トリックルームを**合成ターン開始前状態として設定**して実際の1ターンの技ログを検査する。麻痺で技が不発になった試行は順序一致に含めず、別の固定seedで両者が技を使用した試行のみ比較する。別途固定seed試行で実装の行動不能イベントを観測するが、**その頻度から1/8の実機分布を推定・合格判定しない**。技名・ID・優先度・実測Sと元の固定入力の一致が必要。

既存 `gate_a_evidence_inventory.cjs` は、この診断を再検証して Gate A の「priority」と「status and boosts」に `paralysis_source_order_matches=9`、`paralysis_unknown_compound_rounding=1`、`paralysis_pinned_rate=1/8_SOURCE_ONLY_NOT_DEVICE` を公開する。資料と固定Showdown合成結果を示す別欄であり、既存の静的検査・実機検証数・全機能の `UNKNOWN` は変更しない。診断件数・実行技・S値・出典種別・まひ確率を改変した負例はGate A契約で拒否する。

今回の対象は通常フォーム2種の限定10条件のみ。同一ターン内の状態付与・能力変化の再順序付け、特性による補正、メガ後S、速度同速、実機での状態異常抽選、全レギュレーションは未検証。**Gate A=UNKNOWN／Gate B=UNSUPPORTED／採用NO_GO。LIVE・固定マスター不変、実ゲーム証拠0件**。


## Gate B：メガシンカ後の完全合成状態と部分観測の境界（PR #162案）

`post_mega_restoration_probe.cjs` は既存の Gate B ケース **B1-10** を再利用し、実際の固定Showdownの合成ターンでルカリオがメガシンカした**完全なエンジン由来スナップショット**を検査する。出典は固定Champions使用可能マスター（`メガルカリオ`）、固定M-C Showdown SHA、合成スナップショットであり、Champions実機を観測したものではない。

- 固定マスターのメガルカリオ（タイプかくとう／はがね、特性てきおうりょく、種族値）と、ピン留めShowdownの現行フォーム・タイプ・特性・種族値を照合。**メガ後の有効S、HP・最大HP、ランク、所持品・過去の所持品、技の残PP、選出3匹、メガ実行ログ、現在要求**を復元前後に比較する。数値の一致は固定マスター／固定Showdown間であり実機互換性の認定ではない。
- 独立して保持した完全合成状態SHA256と`restoreChecked`で元状態・復元状態を比較し、同じ技の再生と異なる合法行動の分岐、元状態の非破壊を検査。技PP・HP・特性・メガ履歴ログ・乱数・行動キューを変えた負例で改変スナップショットを拒否。**ダイジェストは合成元から独立保管した場合の入力整合性だけを示し、実ゲーム観測の真正性や未観測履歴を証明しない。**
- `partialStatus`は、部分観測にメガ済み・タイプなどが記録されていても、**未知のメガ履歴、PP、特性、内部RNG、行動キュー等を完全エンジン状態へ昇格させない**。`UNSUPPORTED`を保持し、実ゲーム部分観測からB1完全状態を復元したとは判定しない。

本PRの追加診断は**既存B1-10の詳細監査**であり、独立した新しい実ゲームケース・固有代表局面の件数へ重複加算しない。プライベートGoldenや画像はCIへ含めない。**Gate A=UNKNOWN、Gate B=UNSUPPORTED、採用NO_GO。LIVEコード・固定マスターの変更なし。**


## PR #161 再レビューP2対応：技同一性とGate A横断連携（2026-11-01）

- **P2-1（実行技・優先度）**：各合成ターンで選出後の`moveSlots`と入力する技スロットを突き合わせ、期待するChampions技ID/表示名・固定Showdown優先度・残PPを確認する。ターン実行後の`|move|`ログも技ID/名前と優先度で照合し、想定外の技が同じ側の先手として扱われる回帰を拒否する。ログ中の行動順で技が異なっても正解とすることはない。
- **P2-2（横断インベントリ）**：`gate_a_evidence_inventory.cjs`は同じ固定Showdownの10条件診断を実行して検証後、「priority」機能と総合の双方に `turn_order_source_matches=9`、`turn_order_inconclusive_ties=1`、`turn_order_scope=PINNED_SYNTHETIC_EXECUTED_TURNS_NOT_DEVICE` を出力する。既存の静的マスター一致・他のWiki事例・未検証の状態を上書きしない。
- 出典契約、同速の保留、実機検証件数0、結果件数、技名・優先度・観測順の変更を負例テストで拒否。**新しい2種類の観測はShowdownの合成実行であり、独立した実ゲーム証拠ではない。Gate A=UNKNOWN / B=UNSUPPORTED / NO_GOは維持**する。

## Gate A：素早さ・優先度・ターン行動順の独立監査（Phase 0限定）

`champions_turn_order_source_ledger.json` は2026-11-01確認のChampions専門二次資料（[素早さ実数値](https://pkmn-cp-dataportal.com/battle/priority/speed-stats/)・[こだわりスカーフ](https://pkmn-cp-dataportal.com/item/choice-items/)・[バレットパンチ](https://pkmn-cp-dataportal.com/waza/%E3%83%90%E3%83%AC%E3%83%83%E3%83%88%E3%83%91%E3%83%B3%E3%83%81/)・[技一覧](https://pkmn-cp-dataportal.com/move/)・[トリックルーム](https://pkmn-cp-dataportal.com/battle/priority/trick-room/)）とChampions固定技マスターの優先度を、ID・URL・条件ごとに固定。正しい実ゲーム仕様の認定ではなく、公式/ユーザーの最新ゲーム内確認が優先する。

`champions_turn_order_reference.cjs` は**Showdownに依存せず**既存のChampions AP/性格独立参照式から通常フォームのガブリアスとメタグロスのSを算出する。扱う速度補正は **無補正・スカーフ1.5倍・S+1段階1.5倍（いずれか1つ）** に限定し端数を切り捨てる。技の優先度を素早さより先に比較し、既知のトリックルーム場では同優先度内のS順を反転する。**同速は `UNRESOLVED_TIE` のまま**。S−1以下、S+2以上、スカーフと段階上昇の重複、まひ/天候/おいかぜ/特性補正、メガ後のS/特性、同速乱数・発動順は未検証として扱う。

`champions_turn_order_probe.cjs` は固定Showdown SHA、M-C、Champions使用可能種族/道具/習得技/優先度の固定マスター照合、`TeamValidator`、両側登録6→選出3を要求し、**10の合成条件で実際のPinned Showdown行動ログを比較**する。通常S、スカーフによる逆転、S+1による逆転、バレットパンチ優先、まもる優先、スカーフでも優先度を覆せないケース、同速、トリックルーム内逆転・優先度維持・スカーフ逆転を扱う。S+1とトリックルームは**実行した技による設置ではなく事前注入した合成状態**であり、発動手順・ターン数を検証したものではない。各ケースは独立した登録6→選出3の固定合成Battleで実行。任意の技・特性・構築に一般化しない。

一致は `SOURCE_SPEC_MATCH`、差異は `SOURCE_SPEC_MISMATCH` として保持し、不一致はCI失敗。同速1条件はShowdownでどちらが先に動いても `INCONCLUSIVE_SPEED_TIE` として残し、1回の抽選結果を正しい確率や確定順と誤認しない。期待契約・出典URL・優先度・実ゲーム観測件数の改変は負例でfail-closed。**実行はPinned Showdownの合成10ターンでありChampions実機の実行ターン0、非公開Golden未読込。Gate A=UNKNOWN / Gate B=UNSUPPORTED / adoption=NO_GO、LIVE経路は無変更。**


## Gate A：二次資料に基づくやけど・壁・急所のダメージ補正順と端数比較（Issue #139）

`champions_damage_modifier_source_ledger.json` はChampions専門の二次資料にある **じしん／ガブリアス→メタグロス** の数値と、急所・やけど・リフレクターの組み合わせを、2026-11-01確認の根拠URL付きで固定する。個々の効果を**独立に観測した実ゲーム証拠ではない**。公式やユーザー共有の新しい実ゲーム情報を上位に置く。

`champions_damage_modifier_probe.cjs` は既存の`wiki_damage_reference.cjs`（Showdownに依存しないBigInt算術：基礎→急所→乱数→一致→相性→最終補正の順・五捨五超入）を期待値に使い、M-Cの両側**固定登録6→選出3**をマスターとTeamValidatorで検証後、Showdown内部の`getDamage`で16乱数を8条件について比較する（**従来8条件＋追加の奇数丸め識別条件1件＝9合成条件×16乱数=144個別ダメージ比較**）。

条件は通常、急所、やけど、リフレクター、やけど＋壁、急所＋壁、急所＋やけど、急所＋壁＋やけど。急所は壁無視、やけどは残るという二次資料の記述を参照する。やけど＋壁の最終補正は`1/4`を一括で五捨五超入する。対象技の特性・他道具・天候・フィールド・ランク・技優先度などは検証対象外。

**今回新たに判明した資料差異：**追加カメックス条件の16乱数のうち4件（86%=22対21、90%=23対22、91%=23対22、98%=25対24）で、Champions二次資料の1/4一括丸めと固定Showdownの半減2回丸めが不一致。Showdown側は後者と一致した。**`WIKI_SPEC_MISMATCH`を維持**し、従来8条件は一致、追加1条件は不一致と機械可読で報告。これをChampionsゲーム内の不一致・仕様の正解判定に拡大しない。これ以外の新規差分や、既知4差分の消失・変化はfail-closedとし、明示的な再審査を要求する。CI全成功は「既知差分を正確に検出・記録する監査が成立した」意味であり、9件すべてが資料に一致した意味ではない。

追加の識別条件では、既存6匹登録チームのベンチにいる**カメックスを受け手として先発へ交換**し、相性等倍・防御実数値120の合法対面を作る。最終補正直前の整数が奇数かつ「1/4一括」と「1/2を2回丸め」の結果が異なる乱数を必ず要求し、Showdownの`getDamage`との相違は既知4乱数のみ監査上の確認済み差異として許容し、追加・消失・変化した差異ではfail-closedにする。元のメタグロス相性2倍条件は最終補正前が偶数となり**一括/分割丸めの検証不能**だったため、元の128数値一致と今回の識別テストを分けて扱う。カメックスの期待値は公開済み数値例ではなく、Champions二次資料の数式＋固定マスターの種族値から独立に導いたもので実機観測ではない。ケースIDとburn/Reflect/criticalの各フラグ・出典URLを別契約に固定し、片方のフラグだけ改変する負例でもfail-closedにする。

**`getDamage`を直接呼ぶ計算診断であり、8回の実行ターン検証でも端末での実ゲーム互換でもない。** 結果は`WIKI_SPEC_MATCH`/`WIKI_SPEC_MISMATCH`、非公開実ゲームGoldenは未使用、`executed_turns=0`、`game_verified_check_count=0`を維持。差異があれば`SOURCE`の差異として記録し、Showdownか二次資料のいずれかが正しいと即断しない。Gate A=UNKNOWN、Gate B=UNSUPPORTED、採用NO_GO。計算結果を本番の確定KO/安全受け出し保証に使用しない。


## Gate A：AP・性格の実数値をChampions資料から独立計算（Issue #139）

**PR #159 レビューP2反映（2026-11-01）**：APを実際に振り替える対象をHP/攻撃/防御/特攻/特防/素早さの6能力へ拡大。2種族×4性格×6対象×8AP値で**384合成局面・2,304能力値比較**（各対象能力は64ケース）。各ケースで6能力を照合するため、対象APを変化させた384例と総数2,304比較は別の指標とする。AP対象0の合法登録回避は対象外能力に2APを割り当て、特防自体が対象の場合は防御に割り当てる（48ケース）。これは固定Showdownの登録条件で、Champions実ゲームにおける全AP0の不適法性を示さない。

参照器は `{species,form,baseStats,ap,nature}` を必須入力とし、固定2種族の通常基本フォーム（`Garchomp/base`、`Metagross/base`）だけを許可。特殊HP・メガ・フォーム・未確認種族の入力は `UNSUPPORTED_CHAMPIONS_SPECIES_FORM_SCOPE` として計算せず拒否。提供された種族値の真偽やゲーム内互換性まで認証するものではない。新旧とも `SOURCE_SPEC_MATCH` はChampions二次資料の算術一致であり、`GAME_OBSERVED_MATCH`ではない。


`champions_ap_stat_reference.cjs` は **Showdownをimportしない**Champions専用の能力実数値参照式（HP=種族値+75+AP、他5値=(種族値+20+AP)×性格補正の切り捨て）。`champions_ap_stat_source_ledger.json` に2026-11-01時点で確認できたChampions専用の二次資料、ガブリアス素早さの公開表7行×2性格=14チェック、対象の性格4種（無補正／ようき／いじっぱり／ゆうかん）、例外と未確認条件を明記。**公式・ユーザーゲーム内の新しい実測を優先**し、他作品の式を無検証で補完しない。

`champions_ap_stat_probe.cjs` はガブリアス・メタグロスを固定M-Cマスターから取得し、登録6匹をChampionsマスターおよび固定ShowdownのTeamValidatorで合法性確認→3匹選出した**実際のShowdown BattleのbaseStoredStats**と独立計算を比較する。AP値0/1/7/8/18/28/31/32、対象HP/攻撃/防御/特攻/特防/素早さ、性格4種、2種族の **384合成局面・2304能力値比較**。各不一致は `SOURCE_SPEC_MISMATCH` として差分表示し、CLIは不一致時exit 1/不正入力時exit 2とする。 AP対象0の場合、固定ShowdownのM-C登録バリデータはAP合計0を登録エラーとするため、対象能力以外に2APだけ配分する（特防が対象のときは防御、それ以外は特防）。独立参照側とBattle側で**同一の配分**を使う。比較で使うマスターの種族値は実ゲーム観測でなく固定ソースの値である。

一致が出ても出典は **CHAMPIONS_COMMUNITY_SOURCE_ARITHMETIC_ONLY / SOURCE_SPEC_MATCH** であり、実ゲーム`GAME_OBSERVED_MATCH`ではない。選定以外の性格、他の種族・メガ、能力変化後/まひ/スカーフ/天候の速度、実行順・同速ランダム、実機での数値/端数を**検証しない**。Gate A=UNKNOWN、Gate B=UNSUPPORTED、採用NO_GO。本番LIVEのS確定・確定KO/安全受け保証に利用禁止。


## Gate A・Champions仕様別の根拠と未検証範囲（Issue #139）

`gate_a_evidence_inventory.cjs` は `golden_gate_requirements.csv` のGate A 10項目を正本として、固定ChampionsマスターとShowdownの静的差分、出典から**独立**に計算したWiki単発ダメージ例、Bulbapedia Champions記述由来の5契約・18イベントを機能別に照合する。ゲームの実測や未公開対戦ログをこのコマンドに読み込まない。

`gate_a_evidence_map.json` は静的監査の対応群・イベント契約・Wiki例の適用範囲・LIVEでの危険・次の証拠を明示する。 `move_type`・`move_category`は技の固定仕様として「move power and PP」に明示的に分類するが、動的な技効果やゲーム内ダメージの確認を意味しない。静的監査で実行する全13グループを少なくとも1機能に対応づけ、未マッピングの既存グループ・新規監査グループは `UNMAPPED_AUDIT_GROUP` として失敗させる。機能別の差異件数は同一グループが複数機能に使われるため単純合算できない。なお `audit.cjs` の `pokemon_link` グループは学習技リンクが欠落したときだけ出力されるため、グループ不在は0件として明示し、`move_pp`等の常時照合グループ不在とは区別する。

**PR #160の再レビューP2対応（2026-11-01）**：Gate A横断インベントリは `champions_damage_modifier_probe.cjs` の同一固定SHA・M-C条件別診断を再実行し、その機械可読結果を検証してから、`damage RNG and critical` 機能行とトップレベルの両方へ `modifier_source_status=REVIEWED_SECONDARY_SOURCE_DIVERGENCE`、`known_modifier_source_conflicts=1`、`known_modifier_roll_discrepancies=4` を表示する。従来の単発Wiki例だけに適用される `wiki_arithmetic_status=WIKI_SPEC_MATCH` は変更せず、併記して適用範囲を明示する。9条件のID・フラグ・出典、固定Showdown側での既知4差異の値・位置・件数を検査し、差異が消えた／増えた、資料競合を実機証拠へ昇格したなどの改変は fail-closed とする。比較対象はあくまで**Champions専門の二次資料と固定Showdown**であり、実機との一致・不一致ではない。本番LIVEへの採用可否、Gate A/BおよびゲームGolden読み込み0の境界は不変。なお総合レポートの件数は`champions_damage_modifier_probe.cjs`自体の直接診断に依存するため、独立した2回の実機観測としては数えない。

静的比較が一致しても `SOURCE_CHECKED_ONLY`、Wiki数式が一致しても `WIKI_SPEC_MATCH` に過ぎず、実ゲームに一致したことではない。差分は `SOURCE_CONFLICT` として残し、固定マスターとShowdownのどちらを正とするかは追加根拠なく判定しない。イベントで `INCONCLUSIVE` だった条件も解消扱いにしない。

非公開ローカルの実ゲームGolden 6ケース/11チェックは **当該runでは読まない**ため、`game_verified_cases_this_run=0` は過去の実ゲーム観測を否定する意味ではない。ユーザー本人による既存Goldenの独立照合・証拠紐付けは別途必要。

固定マスターCSVだけを変更するPRでもPhase 0のCIが自動起動するようワークフローのpathsに `masters/Pokemon_Champions_MC_*.csv` を追加した。監査差分注入テストは合成の負例であり実ゲームの不一致を示さない。

既存のテスト対象が減少した、検証ケースの状態が変わった、出典契約が未レビューで追加された場合はfail-closed。各機能の `feature_compatibility=UNKNOWN`、Gate A=UNKNOWN、B=UNSUPPORTED、総合NO_GOを維持する。**本番LIVEの確定KO・安全受け出し・行動順保証への転用は不可**。Phase 0のGate B・運用・採否レポートも別作業として残る。


## 固定ShowdownをLinux・Windows Nodeで3プロセス再実行（Issue #139）

PR #157レビューP2対応：プロセス間の署名照合に先立ち、固定4マスターのファイル名・SHA256形式、127ケースそれぞれの完全状態・粗視化状態・登録チーム・両分岐・再実行のSHA256の存在と64桁小文字16進形式を個別検証する。いずれか欠落・不正長なら、同じ壊れた値が3プロセスで一致しても失敗する。`.gitattributes`のみの変更もPhase 0 CI自動実行の対象とした。

固定Champions技・種族・習得技・道具CSVのGitチェックアウト時の改行変換を、ルート`.gitattributes`の`-text`で禁止。Windowsジョブはテスト前に4ファイルのSHA256を固定期待値と直接照合し、異なる場合は本体のバリデーション失敗と混同せず即停止する。これはマスター内容の変更でも期待値の修正でもない。


`cross_process_synthetic_probe.cjs`は登録6→選出3から作成した固定127合成状態を、新しいNode子プロセス3個でそれぞれ検証し、ケースごとの完全スナップショットSHA・粗視化状態SHA・両分岐と再実行SHA・固定マスターSHAを比較する。**127種類×3回=381合成実行**であり、381件の独立した対戦例ではない。

Windowsでは固定Showdown commit `ccfaa20f32af363569cd21a9d501b2bc1ded26b1` をチェックアウトして`npm ci && npm run build`したNode上で実行する（デスクトップGUIのCIとは別）。Linuxでは既存固定Dockerイメージ内で実行。プロセス単位の所要時間は子プロセス起動・moduleロード・127局面の検証を含むが、Docker/Windowsアプリの起動・UIやIPCは含まない。P50/P95/P99は3プロセスのみの探索的測定。3プロセスの同一結果は独立したCI run間の再現性、異OS間の同一ハッシュ、Champions実ゲーム計算仕様の一致を証明しない。

**Gate A=UNKNOWN、Gate B=UNSUPPORTED、総合NO_GO**を維持。実ゲームの非公開Golden/対戦ログ/画像をCIに含めず、LIVEには接続しない。


## 実ゲームGoldenと部分観測のオフライン証拠境界（Issue #139）

`game_evidence_bridge.cjs` は既存の `golden_compare.cjs`（Gate Aの能力値・PP・単発ダメージ照合）と
`partial_observation_probe.cjs::assertObservation`（Gate Bの34フィールド・情報源検査）を**読み取り専用**で使用する。
**新たな実機検証データは作らない**。既存の本人確認済みGoldenはユーザー端末にのみ残し、実ゲーム画像・JSON・参照ID・個別ポケモン名をGitHub/CIへ送らない。
ユーザーの `USER_VERIFIED` 宣言および根拠IDの形式・参照先対応を確認するが、CLIは元のスクリーンショットの真偽を独立に認証できない。

使用方法（ローカルDocker／秘密データを渡す場合はネットワークを遮断し、ファイルをread-onlyでマウント）：

```bash
docker build -f poc/showdown_phase0/Dockerfile -t champions-phase0 .
docker run --rm --network none --read-only --tmpfs /tmp \
  --mount type=bind,source=/ABSOLUTE/PRIVATE/CASES.json,target=/tmp/private-golden.json,readonly \
  --entrypoint node champions-phase0 game_evidence_bridge.cjs --golden /tmp/private-golden.json > /ABSOLUTE/PRIVATE/evidence-report.json
```

部分観測が別ファイルで存在し、本人が根拠を確認した場合に限り、
同じコマンドへ `--mount type=bind,source=/ABSOLUTE/PRIVATE/PARTIAL.json,target=/tmp/private-partial.json,readonly` と
`--partial /tmp/private-partial.json` を追加可能。ただし **Goldenと部分観測は自動的には同じ試合・局面へ結合されない**。
Gate AのGolden内の相手・技・HP・場をGate Bの相手選出・HP・履歴・キューへ自動転記しない。
正確な相手HPが画面にないならUNKNOWN。相手の能力配分・未公開技・道具、実際の内部乱数、残PP、メガ前履歴、壁の残ターン、未処理行動も情報がなければUNKNOWN。

結果JSONは*集計数・抽象的なフィールド名・入力SHA*のみを含む。個々の観測値・根拠ID・ファイルパス・対戦ログを出力しない。
ただし入力SHAも私人のファイルの識別子なので共有時には注意する。CIでは
`synthetic_golden_example.json` だけを扱い、実ゲームで一致したケース数を0として検証する。
エラー時もファイルパスや観測値を出さない。`MATCH`は1項目の局所一致で、
`MISMATCH`はレビューが必要だが、いずれも実ゲームの全ルール互換性ではない。

Golden v1の証拠メタデータについて、`UNKNOWN`は値・根拠ID・理由を持たせず、`SYNTHETIC`で根拠IDを明示する場合は同じcaseに宣言済みのIDだけを許容する（従来の根拠IDなしの合成fixtureはそのまま使用可能）。
`INFERRED`は観測済みIDと混同せず、任意の`reason`を正本スキーマに後方互換で追加した。旧Golden v1に理由のないINFERREDがある場合は**読み込みは継続**するが、`legacy_inferred_without_reason_count`と`LEGACY_V1_UNEXPLAINED_INFERENCES_NEED_LOCAL_REVIEW`を報告し、ゲーム観測への昇格や自動変換は行わない。
過去の非公開Golden 6ケース/11チェックはGitHubから取得・再実行しておらず、本人がローカルで新監査を動かすまで互換性の完全確認はできない。元の`golden_compare.cjs`とデータ本体は変更しない。

新規テストでは`STRUCTURAL_STUB_NOT_REAL_GAME`と明示した仮想`USER_VERIFIED`宣言・`GAME_FRAME`参照を含む部分観測34項目を一時ファイルで作る。これは**実機データではない**。CLIの`--partial`単独および`--golden --partial`同時入力、証拠ID欠落時のエラー秘匿、B復元不実施・自動結合なしを回帰検証する。実ゲーム一致数には加算しない。

**Gate A=UNKNOWN、Gate B=UNSUPPORTED、総合NO_GO。**
Bの完全スナップショット、実ゲーム内部乱数の復元、Phase 1/LIVE接続は行わない。
次は通常対戦で自然に得られる根拠付き局面を、ユーザー本人がローカルに記録した範囲で部分観測スキーマへ入力し、UNKNOWNを維持できるか個別に確認する。


## Phase 0：独立した合成代表局面127件（Issue #139）

`representative_states_cases.json` と `representative_states_probe.cjs` は、固定Showdown
`ccfaa20f32af363569cd21a9d501b2bc1ded26b1` の `gen9championsbssregmc`
だけで実行する **合成状態ベンチマーク**。既存の登録6匹検証と実Team Previewから選出3匹を経て、
双方の交代ターンを実行する。18種類の異なる対面に7条件（通常・双方HP低下・PP消費・まひ・能力変化・雨＋リフレクター・オボン消費）を組み合わせた126局面と、実際のメガシンカ行動を実行した1局面を独立定義する。

直列化前後の全状態（乱数・キュー・ログを含む）、同条件の行動再実行、
異なる合法行動への分岐と元状態不変性を検証する。見た目の局面と
実シリアライズ状態を**別々のSHA**で重複排除し、見た目の局面ハッシュは
乱数・ログ・登録選出順だけの差・正確なHPだけの違いでは増やさない。
各ケースの成功・失敗を機械可読JSONで記録し、失敗や重複で100件の
synthetic coverageを満たせない場合はNode/CIをfail closedにする。

```sh
docker build -f poc/showdown_phase0/Dockerfile -t champions-phase0 .
docker run --rm --network none --read-only --tmpfs /tmp --entrypoint node champions-phase0 --test test_representative_states_probe.cjs
docker run --rm --network none --read-only --tmpfs /tmp --entrypoint node champions-phase0 representative_states_probe.cjs > /tmp/phase0-representative-local.json
```

一部のHP/PP/状態異常/能力変化は**明示的な合成注入**。雨と壁は
Showdownの効果API、オボンはHPを合成設定してからエンジンの
道具消費処理、メガは実際の技行動で生成する。特性・個々の技効果・
状態異常の発動順や100件の**独立した実戦試合**を保証したものではない。
比較した127件は同じ登録6匹の組を元に一部持ち物だけ変えた合成条件で、
対象ポケモン・タイプ・効果相互作用の全面網羅ではない。
既存Gate B、部分観測、選出、イベントケースとは**重複排除した合算を行わず**、別集計する。

結果には直列化、復元、分岐、再現、チーム組成・局面準備の
Nodeステージ別P50/P95/P99と標本数、RSS/ヒープを記録。
`tactical_sha256` はアクティブな2匹の種族・現在タイプ・メガ状態・HP帯・PP消費・状態異常・能力ランク・持ち物・こだわり固定と、天候・リフレクターの有無のみを粗視化したハッシュ。
控え2匹のHP・PP、天候/壁の残ターン、未公開技・持ち物候補、未解決キュー、ステルスロック等の他設置物は**含まない**。
127件の粗視化ハッシュが異なることは、全戦術状態の網羅性、受け出しやターン判断の網羅性を意味しない。
メモリはNodeプロセスの実行前後のRSS/heap snapshotであり、**peak RSSと長期常駐リークは未測定**。
一部段階の例外は`failure_stage`を記録し、実際に状態比較で差分が取れた場合だけ`first_difference_field`を記録する。
同一Docker環境の単回実行値であり、Linux上でのNode/コンテナ起動時間、
Windows常駐・IPC・表示E2E、実機性能を証明しない。
100 **合成**局面を満たしても、実ゲームの部分観測から完全復元できる
こともChampionsの計算互換性も証明しない。
**Gate A=`UNKNOWN`、Gate B=`UNSUPPORTED`、採用=`NO_GO`を維持。**
次のゲートは実機で観測可能な状態情報とのオフライン照合、
ルール互換性、独立した複数回性能測定とWindows Node検証。


## 部分観測・UNKNOWN・複数仮説の独立診断

`partial_observation_schema.json` はPhase 0専用の部分観測モデル。34フィールドを `{source,value,evidence_ids,reason}` で保持し、`GAME_OBSERVED`／`SYNTHETIC`／`INFERRED`／`UNKNOWN` を分ける。UNKNOWNには値を持たせない。空文字の道具・状態異常、ランク0、最大PP、メガ未実行、効果なしはそれぞれ**確認された値**であり、UNKNOWNの代用ではない。合成fixtureの根拠IDを持つ値は常にSYNTHETICで、ゲーム観測へ昇格できない。GAME_OBSERVEDには別の実ゲーム根拠と本人確認が必要。このPoCに実ゲーム入力は含まない。

`partial_observation_probe.cjs` は固定Showdown M-Cで合法な登録6匹から実Team Previewを通して完全合成Battleを作る。`worker.cjs::observed` から種族・対面・天候等の**限定した合成盤面情報だけ**を投影し、同関数に含まれる相手HP整数は破棄する。相手選出全体、技・道具・特性、性格/AP、残PP、内部乱数、履歴、キュー等はUNKNOWNのまま。画面からその各値を読めるという主張ではない。既存B2の`GAP_FIELDS`を新モデルへ対応付け、不足情報を列挙する。

候補は真値Battleの隠し値から作らず、固定した4種類の合法なガブリアス型（技・道具・特性・性格/AP違い）から生成する。各登録6匹をChampions固定マスターとM-C TeamValidatorの両方で検査する。候補ごとのShowdown完全状態は**合成仮説**としてのみ直列化・復元・行動分岐できる。新しい技・道具・特性・メガ・消費・交代・能力変化・行動順の合成事実で候補を更新する。メガガブリアス（非Z）の現在特性は固定マスターの「すなのちから」と固定Showdownの`Sand Force`を照合する。メガ化時は通常形の現在特性をUNKNOWNに戻し、旧特性は別スコープに保持する。交代後は前のポケモンの道具・技等を新しい対面へ引き継がず、現在対面の候補数を未評価（`null`）、前の対面の候補数を`previous_active_hypothesis_count`で示す。行動順から能力配分を確定しない。候補の絞り込み後は旧Battleを現在状態として実行せず、`NOT_RUN_CURRENT_STATE`とする。更新後の候補SHAは`BASE_HYPOTHESIS_ONLY`、交代後は`BASE_PREVIOUS_ACTIVE_HYPOTHESIS_ONLY`であり、現在の完全状態のハッシュではない。候補0件は保留、入力事実の矛盾は別区分、探索上限超過は未評価候補数で示す。

```
docker build -f poc/showdown_phase0/Dockerfile -t champions-phase0 .
docker run --rm --network none --read-only --tmpfs /tmp --entrypoint node champions-phase0 --test test_partial_observation_probe.cjs
docker run --rm --network none --read-only --tmpfs /tmp --entrypoint node champions-phase0 partial_observation_probe.cjs > /tmp/partial-observation-local.json
```

`partial_observation_cases.json` の固定26機構は、`CONSISTENT_HYPOTHESES` 10、`SYNTHETIC_TEST_PASS` 7、`INCONCLUSIVE` 6、`UNSUPPORTED` 2、`CONTRADICTORY_OBSERVATION` 1。独立した完全合成真値9、根拠IDを除いた異なる部分観測12、異なる候補ID集合6、候補状態ハッシュ4、候補評価96、同一局面反復0。ケースIDや根拠IDだけを変えて局面数を増やさない。負例はUNKNOWNの値付け、推測の観測昇格、非合法な技・道具・特性/AP、単一候補への勝手な確定、候補0件、矛盾観測、候補間の干渉、部分状態の`restoreChecked`誤用を実コードで検出する。既存B1 12／B2 2と登録選出25ケースは独立集計であり、100代表局面は未達。合成候補が真値ハッシュと一致しても、観測だけから真の完全状態を一意に復元できたとは判定しない。

実ゲームの観測可能範囲、相手の正確なHP・残PP、未観測効果の残ターン、メガ前履歴、未解決選択/キュー、乱数、全合法型の網羅、複数runの再現性は未検証。次はユーザーが確認した実画面の各フィールドに根拠IDを付け、観測可能な値とUNKNOWNを分けてオフライン照合し、代表局面と効果機構を増やす。Gate A=`UNKNOWN`、Gate B=`UNSUPPORTED`、総合=`NO_GO`を維持し、LIVE判断へ接続しない。

## 登録6匹→選出3匹→途中局面復元（独立した合成診断）

`six_team_selection_probe.cjs` は固定Showdown `ccfaa20f32af363569cd21a9d501b2bc1ded26b1` の M-C (`gen9championsbssregmc`) を使う。両側の登録6匹を、Champions固定マスターに基づく登録検証とShowdown `TeamValidator` で**独立に**検証し、双方が合法の場合だけBattleへ渡す。固定マスター検証は全国No.（リージョン違いを含む）、種族・直接登録可能フォーム、技マスター・習得表・固定Dex、特性枠、持ち物とメガストーン所有者、重複道具、性格、AP、技スロットを検査する。日本語技名・英語名・MoveKeyは既存の正規化を使う。`level:50`はShowdownを動かす内部値で、ゲーム内レベルの観測ではない。

合法な双方6匹からBattleを生成し、Team Previewの登録数6、M-Cの選出数3、双方の選出要求を確認してから実際の `team 123`、`team 415`、`team 634` 等のコマンドを送る。指定スロット・3匹の重複・先発と順序は実行前にも検査する。固定Showdownは短い選出を自動補完するため、PoCの入力検査は厳格にする。Showdownでの選出結果、先発、控え、非選出3匹の不在、3対3を確認し、ターンと交代を進めた完全合成状態を直列化・復元する。元状態の独立SHA256、全状態比較、同一行動の再現、別合法行動への分岐と元状態の不変性を診断JSONに記録する。

```
docker build -f poc/showdown_phase0/Dockerfile -t champions-phase0 .
docker run --rm --network none --read-only --tmpfs /tmp --entrypoint node champions-phase0 --test test_six_team_selection_probe.cjs
docker run --rm --network none --read-only --tmpfs /tmp --entrypoint node champions-phase0 six_team_selection_probe.cjs > /tmp/six-team-local.json
```

`six_team_selection_cases.json` の固定25ケースは、合法登録・正確な選出・完全合成状態の復元分岐 **4/4 `PASS_SYNTHETIC`**、不正登録 **16/16拒否**、不正選出 **5/5拒否**。登録の合法性、選出、復元は別の診断欄を持ち、負例を合法成功数に加えない。対応しないメガストーンの負例ではChampions固定マスターが拒否し、固定Showdown TeamValidatorは受理するため、`agree:false` と両者の理由を記録する。Showdownの受理だけではChampionsの合法性を認定しない。

登録チーム構成は**両側6匹の組1種**、選出パターン3種、復元対象の異なる入力状態ハッシュ4種、復元成功4件、同一局面の反復0回。既存Gate Bの14局面・11ユニーク入力状態とは別集計で、重複排除した合算はしていない。100代表局面は未達。実ゲームからの未公開情報・乱数・履歴・部分観測状態の復元、Champions実機の登録・選出・ルール一致は未確認。Gate A=`UNKNOWN`、Gate B=`UNSUPPORTED`、総合=`NO_GO`を維持する。Linux CIはこのNodeテストと25件診断を実行し、WindowsデスクトップCIの成功をWindowsでのShowdown Node実行と同一視しない。

## Gate B 独立診断（途中局面復元）

`gate_b_probe.cjs` は固定Showdown `ccfaa20f32af363569cd21a9d501b2bc1ded26b1`、`gen9championsbssregmc` だけで動く。固定Championsマスターの種族・技習得・特性・持ち物を照合してから、`gate_b_cases.json` の14件を実行する。既存 `worker.cjs` の戦闘生成・直列化・復元と `state_compare.cjs` の全状態比較を再利用し、実ゲームの画像・Golden JSON・対戦ログは入力しない。

```
docker build -f poc/showdown_phase0/Dockerfile -t champions-phase0 .
docker run --rm --network none --read-only --tmpfs /tmp --entrypoint node champions-phase0 --test test_gate_b_probe.cjs
docker run --rm --network none --read-only --tmpfs /tmp --entrypoint node champions-phase0 gate_b_probe.cjs > /tmp/gate-b-local.json
```

`/tmp/gate-b-local.json` はローカルの合成診断で、GitHubへコミットしない。CIは同じ診断のケースID・機構・期待区分を固定して照合し、件数欠落や新たな差分で失敗する。各結果に入力SHA、元と復元後の全シリアライズ状態、乱数seed、最初の差分、行動ID、継続結果、分岐独立性、未対応フィールド、理由を記録する。比較はオブジェクトキー順のみ正規化し、リプレイ比較では完全一致の `|t:|数字` 行だけ時刻を正規化する。ログだけの差分も成功にしない。

- **B1／完全合成状態:** 12件 `PASS_SYNTHETIC`、状態・キュー差分0、ログのみ差分0、未対応0。通常・双方HP減少・PP消費・能力ランク・状態異常・オボン消費・こだわり固定と交代解除・天候と壁残ターン・メガ後・強制交代要求・片側選択済みを個別に確認。同一条件での再実行と異なる合法行動の独立結果を検査する。B1-04/05 は能力ランク・状態異常を合成注入、B1-06 はHPを合成設定後にShowdownの道具消費処理を実行、B1-09/11 はShowdownの効果・要求APIで合成設定した。これらの状態遷移自体を実ゲームや実ターンで証明したものではない。
- **B2／部分観測:** 2件 `INCONCLUSIVE`、成功0。合成した公開盤面投影が同一でも相手の技ID `earthquake` と `yawn`、持ち物が異なる2仮説を独立に進め、結果と元状態の不干渉を確認した。投影内の相手HP整数も実画面から読めるとは保証しない。相手未公開技とPP・特性・持ち物・能力配分・内部乱数・未観測効果履歴・未解決行動キューは `UNKNOWN` として保持し、実ゲーム局面の復元済み状態は作らない。2仮説でも全整合モデルを列挙した証明にはならない。
- **カバレッジ:** 14の局面定義、11のユニーク入力状態、14機構、反復0。100代表局面は未達。既存120回ベンチマークは6シナリオの反復であり、この件数へ加算しない。
- **負例と復元入力の整合性:** 実ShowdownスナップショットでPP、こだわり対象、乱数、壁・天候期間、消費済み道具、メガ形・履歴、選択済み行動、キューを故意に変更し、比較器が検出することを確認。追加負例は破損・欠落・不正型のJSONを固定Showdownの `Battle.fromJSON` に実際に渡し、拒否または信頼済み元状態との差を確認する。B1の `restoreChecked` は**別途保持した完全合成スナップショットの信頼済みSHA256**と入力・復元結果を照合し、違いを拒否する。ハッシュを改変入力自身から作成した場合の正しさを保証しない。B2部分観測を完全状態に昇格させない。未知の相手道具を確定に変える変更も検出する。
- **登録合法性の境界:** 現行B1/B2の試験チームは**双方3匹をShowdownへ直接投入**している。上記の登録6匹と選出経路の診断は別ケースであり、B1/B2の実績には加算しない。
- **計測:** 同一seed・同一チームで10回ウォームアップ後、直列化、復元、複製、最初の行動入力、残る選択によるターン解決を分けて記録する。強制交代と片側選択済みは入力と解決が同一API呼び出しなので結合計測する。P50/P95/P99は各段階の合格ケースだけから算出し、標本数を併記する。12局面の単一実行値は恒常的な性能保証でもWindows UIのE2E値でもない。

Gate A=`UNKNOWN`、Gate B=`UNSUPPORTED`、総合=`NO_GO` を維持する。次は実ゲームで確認できる途中状態の証拠範囲を定め、未知値を含む再構築または対応不可判定を実観測と照合する。加えて独立した状態定義・ポケモン/技/効果を増やして100代表局面と複数runの再現性を検査する。本診断の成功をChampions互換性、確定KO、安全受け出し、Phase 1採用の根拠にしない。

## ゲートA：実ゲームの独立観測とオフライン照合

ダメージ比較に使える `defender_hp_before` は命中直前、`defender_hp_after` は**直撃ダメージがHPに反映された直後、きのみ消費・回復・ターン終了時の定数ダメージや回復より前**に読めた値だけ。画面や映像からこの境界を切り分けられない場合は `direct_hit_only` を真にせず `INCONCLUSIVE` とする。`direct_hit_only: true` は人の申告であり、CLIが映像の時系列を証明するものではない。さらに防御側に直撃後の消費・更新イベントを持つ道具（オボンのみ、オレンのみ等）がある場合は、真と申告されても `INCONCLUSIVE` にする。

ダメージ比較の状態異常は固定modのID（`brn`, `par`, `psn`, `tox`, `slp`, `frz`、状態異常なしは空文字）のみ。`burn` 等の別名は自動変換せず `INCONCLUSIVE` にする。天候は空文字、`raindance`, `sunnyday`, `sandstorm`, `snowscape` のみ。ゲーム画面の「ゆき」を人が `snowscape` と確認した場合にそのIDで記録し、`snow` は未対応にする。固定modで天候を適用した結果も確認する。持ち物はChampions使用可能持ち物マスターの収録IDに限る。

### ゲームに存在しないレベルとShowdown内部値の区別

- ゲーム内のスクリーンショットからレベルを読み取った事実として扱わない。新しい`checks.stats.pokemon`・`checks.damage.attacker/defender` に`level`を記録しない。レベルなしの能力値計算に必要なのは種族・フォーム・性格・AP・表示実数値。
- Showdownのバトル生成には内部の数値設定`SIMULATOR_LEVEL = 50`を引き続き渡す。**これはShowdownを動作させるための実装値であり、Championsにレベル50が存在するという根拠ではない**。固定modの通常のChampions能力値計算分岐はゲームレベルを使用しない。一方、ダメージ計算はShowdown内部でこの数値を参照するため、実ゲームとの一致は別途ゲートAで検証する。
- 旧ローカルJSONの`"level": {"source": "UNKNOWN"}`は互換性のため無視できる。新しいJSONからは省略する。`GAME_OBSERVED` / `INFERRED` / `SYNTHETIC`でレベルの数値を入れた旧ケースは、ゲーム由来の根拠として誤認させないよう`INCONCLUSIVE`とする。
- `simulator_context.internal_level = 50`, `simulator_context.game_observed_level = false`を出力する。ステータス`MATCH`は個々の観測値の比較結果であり、この内部数値をゲームで確認したことを意味しない。観測根拠をユーザーが照合していない実ゲームケースは`MATCH_UNVERIFIED`のまま。

`golden_schema.json` が記録形式、`golden_compare.cjs` が比較CLI。実画面の内容を人が確認し、1件ごとにケースID・UTC観測日時・M-Cレギュレーション・根拠ID/説明/任意のローカルパスとSHA256を記録する。各事実は `{ "source": "GAME_OBSERVED", "value": ..., "evidence_ids": ["frame-1"] }` とし、推測は `INFERRED`、未判明は `{ "source": "UNKNOWN" }` にする。空文字の持ち物/状態/天候は「ないと観測済み」、UNKNOWNは未確認を意味する。`evidence.verification` は画面を本人が照合した場合だけ `USER_VERIFIED` にする。参照IDの存在はCLIで検査するが、映像の真偽やユーザー確認そのものは自動認定できない。

`checks.stats` は正確なShowdown種族・フォームキー、性格、6能力分のAPとゲーム表示6能力値を要する。**Pokémon Championsにはゲーム内のレベルという観測項目がないため、レベル入力を要求しない。** APは各0–32・合計66以下に限定する。`checks.pp` は種族キー・技IDと画面の戦闘時最大PPが必須で、元PP（マスター）、Champions制限後Dex PP、実際の戦闘最大PPを別欄に出す。残PPは記録と範囲チェックのみで、使用履歴なしには初期値からの一致を主張しない。`checks.damage` は攻防双方の正確な種族/フォーム・性格・AP・特性・持ち物・状態・能力ランク、技、天候・フィールド・防御側壁、急所、HP前後、身代わり・こらえるの有無、単一直接命中/条件網羅の人手確認を要する。メガ状態はフォーム込み種族キーで表し、`mega_state`を併記したときは矛盾を検査する。種族・フォーム・技はChampionsマスターと固定modの両方、特性は固定modの該当種族枠と照合する。未確認の相手型・道具・場の状態を補完しない。

比較対象のダメージは、固定威力・単発・直接命中の技に限る。16個の乱数係数を固定Champions modの`getDamage`に通し、実HP減少が実現可能な値か調べる。`getDamage`後に発動する特性/道具（きあいのタスキ、がんじょう、ばけのかわ、アイスフェイス等）、身代わり・こらえる、複数回攻撃、可変威力、天候以外の地形、複合HP変化などは `INCONCLUSIVE`。急所・技の成立・実際の行動順やダメージ分布全体の妥当性は、この単発照合だけでは証明しない。1件のMATCHはゲートA完了を意味しない。

実対戦データは `poc/showdown_phase0/local_goldens/` に保存する。このディレクトリはGitとDocker build contextの両方から除外される。スクリーンショットや動画をCI・PR・APIへ送らない。ローカルで固定イメージをビルドし、読取専用マウントで比較する：

```bash
mkdir -p poc/showdown_phase0/local_goldens
docker build -f poc/showdown_phase0/Dockerfile -t champions-phase0 .
docker run --rm --network none --read-only -v "$PWD/poc/showdown_phase0/local_goldens:/goldens:ro" --entrypoint node champions-phase0 golden_compare.cjs /goldens/cases.json
docker run --rm --network none --entrypoint node champions-phase0 golden_compare.cjs synthetic_golden_example.json
```

出力スキーマ `champions-golden-comparison-v2` は固定Showdown SHA、入力JSONのSHA256、実際に読んだChampionsマスター4件のSHA256、ケース別の判定と集計を含む。`verified_game_case_count` は人手確認済みの独立ケース数、`verified_game_check_count` はそのうち判定可能な能力値・PP・ダメージのチェック数。1ケースの能力値6件を6ケースとして数えない。根拠ID文字列や申告されたSHA256が実ファイルと一致するかはCLIで確認できないため、元画面とハッシュの**人手確認が必要**。出力は `MATCH` / `MISMATCH` / `INCONCLUSIVE`、合成と未検証データにはそれぞれ `_SYNTHETIC` / `_UNVERIFIED` を付ける。`MISMATCH` は終了コード1、入力・固定SHA・実行エラーは2。標準出力JSONを保存する場合もローカルの非公開領域を使う。`synthetic_golden_example.json` は使い方の例に過ぎず、実ゲーム証拠ではない。実ゲームの検証済みケースは未収集で、ゲートAは引き続きUNKNOWN。

本番アプリ・GPT経路・ゲーム操作は変更しない。合成データを使う隔離実験としてのみ利用する。

## 対象と根拠
- upstream: https://github.com/smogon/pokemon-showdown
- 固定commit: ccfaa20f32af363569cd21a9d501b2bc1ded26b1 （2026-10-30参照）
- format: [Gen 9 Champions] BSS Reg M-C / mod champions
- Node >= 22.18.0 (upstream package.json)
- upstream: config/formats.ts, data/mods/champions/{scripts,moves,abilities,items,learnsets}.ts, sim/{index,battle,state,battle-stream}.ts
- LICENSE: upstream MIT。依存npmの配布条件、ソース変更通知、SBOMは別途確認し、MITのみで依存物を許諾済みとしない。

## 再現手順（WSL2 + Docker）
リポジトリルートで実行。WindowsホストへPython/Nodeを追加しない。

    docker build -f poc/showdown_phase0/Dockerfile -t champions-phase0 .
    docker run --rm champions-phase0
    docker run --rm --entrypoint node champions-phase0 --test test_decision.cjs test_state_compare.cjs

Dockerfileはupstream SHAが期待値と完全一致することを確認してから npm ci / build を行う。実行時は外部通信しない。動的計測JSONを保存する場合のみ、ユーザーが選んだローカル領域へ --out パスを指定する（機密画面や対戦履歴は使用しない）。

## 実験の内容
- 子Nodeプロセスを常駐させてローカルJSONL IPC。request ID、有限タイムアウト、古い/重複応答の破棄を実験する。
- Battle.toJSON()/Battle.fromJSON()による3対3の途中状態の複製と同一RNG状態での分岐再現を確認する。復元時にキー順を正規化した揮発性効果の**値**（技固定対象・壁/天候の残ターン等）、queue/選択状態を含むシリアライズ状態全体を照合し、同一候補で次ターンの継続分岐も比較する。片側だけ選択済みの未解決キューは独立probeで検証し、未保持が発覚した場合はUNSUPPORTEDとしてゲートBを不合格にする。
- HP、PP、状態異常、能力ランク、持ち物消費、こだわり固定、天候、壁、強制交代を合成条件で試す。任意のメガ後履歴の復元は明示的にUNSUPPORTED。
- 同じ観測可能局面から、**自軍は共通の技「move 1（じしん）」**、相手は仮説0で「move 1（じしん）」・仮説1で「move 1（あくび）」を選び、異なる合法行動を分岐評価する。**技スロットの番号が同じでも技IDは異なる**。結果の相違と観測元不変性を確認し、未知値を観測確定に変換しない。
- 20回ウォームアップ後、**同じチーム・同じ乱数seedの6種類の合成局面をそれぞれ20回ずつ反復（計測iteration 120回）**する。これは代表局面120件ではない。入力を正規化したSHA256による`unique_fixture_count`、機構別の`unique_state_contract_count`、反復`iterations`を区別して記録する。現行は**5ユニーク局面／manifestケース6件／120反復**（normalとitem_choiceは同じ入力状態）であり、#139の**100代表局面は未達**。seedだけを増やして代表数を満たした扱いにしない。
- 直列化・復元・合法選択・分岐解決を別カウンタに分ける。起動＋probe、コピー／復元／初回合法手／継続分岐、未知型仮説probeも別に計測し、全体のP50/P95/P99と混同しない。強制交代は交代先の実際の変化を検証する。
- `test_state_compare.cjs`は**合成シリアライズ形状**について負例を作り、技固定対象・壁/天候/疑似天候の残ターン・保留中の選択・queue・状態異常・道具・交代履歴を意図的に変更すると、正規化ハッシュが異なることを検査する。これは比較器の回帰テストであり、Showdownがすべての実ゲーム局面を復元できる証拠ではない。
- 完全な実機中途状態の復元ではないため、合成条件を全通過しても途中復元ゲートはPARTIAL止まり。片側の行動が保留中の局面も、オブジェクトキー順を正規化して比較すると、選択済み行動1件と内部状態・続行結果が一致したためPASS_SYNTHETIC。これは**Showdown合成局面に限る**証拠で、実ゲームからの途中復元やメガ履歴は未合格。

## レビュー指摘への対応（PR #143）
- P0：PR head.shaを明示してcheckoutし、ビルド前に実checkout SHAと一致検証。Draft中のWindows CI skippedはSuccessではない。
- P1：Docker内に正本の固定CSV4件を読取り専用コピー。audit.cjsはCHAMPIONS_MASTERS_DIR入力とSHA256・比較件数を出力し、欠損時NOT_RUNで失敗する。静的一致は実機証明ではない。
- P1：serialized_ok / reconstructed_ok / choice_legal / branch_resolved / unsupportedをカテゴリ別に記録。合法な行動と分岐解決を確認しない状態は成功件数へ加えない。
- P1：同じ観測盤面で自軍は同一行動、相手は仮説別の技IDを評価し、結果の違いと元状態の独立性を確認する。
- P1：全120件のステージと時間、およびマスターSHA・監査差分はGitHub Actionsログへ機械可読形式で書き出す。Step Summaryには集計を記録。Actions Artifacts/Releaseには出力しない。ログ保持期限に従うため長期保存が必要ならローカルの--outで保存する。ゲーム実画像・対戦履歴・秘密情報は扱わない。
- P1/P2（再レビュー）：シリアライズ値全体・持続ターン・固定技・未解決の選択状態・続くターンの分岐を合成試験し、59差分に原因**仮**分類を付ける。CIサマリーは実checkout HEAD SHAを表示し、PR状態はDraftへ修正。
- P1（再々レビュー）：120回を6シナリオ名の反復として明記し、ハッシュ別のユニーク局面数、未カバー機構、段階別の計測を追加。100代表局面は未達として独立判定に反映。normalとitem_choiceの入力が重複する点も実SHAで可視化。負例5テストでシリアライズ比較の感度を確認。
- NO_GOは現時点の安全側の評価で、将来の最終採否判定器ではない。必須ゴールデン項目・未検証範囲はgolden_gate_requirements.csvに示す。
## 2026-10-31 再レビューへの対応：再現性と代表局面
- **再現性は独立判定**：CIの`completed/success`は診断処理・静的監査・失敗の取り扱いが正しいことを示すだけ。合成ケースのすべてが一致したか（`synthetic_replay_all_consistent`）、重要probeと固定seedの回帰試験32回が一致したか（`current_run_replay_consistency_pass`）、Phase 0で継続再現性を認定できるか（`phase0_replay_acceptance_met=false`）を別々に記録する。過去の不一致を消さず、単発緑を恒常的な再現性の根拠にしない。
- **失敗時の証跡**：全失敗にケースID、初期seed、入力ハッシュ、再現用の合成スナップショット、最初の差分パス、前後ログ末尾を紐付け、CIログに機械可読出力する。ログのみの不一致は`LOG_ONLY`、PRNG・行動キュー・効果/能力値の状態差は`STATE_OR_QUEUE`。**どちらも合格へ昇格しない**。既知の発生カテゴリ（こだわり、状態異常・能力変化・PP、消費道具、強制交代）を固定seedで反復検証する。
- **独立ケースmanifest**：`fixture_manifest.json`へ6件の固有ケースID・事前条件・機構タグ・期待結果・未対応、加えて未測定ケースを記録。実行時にケースごとの成功/失敗とタグ単位のカバレッジを付ける。入力状態ハッシュ数は5、manifestケース数は6、機構タグ数は別の数として集計。`unique_state_contract_count = unique_fixture_count`のような同一値の別名は廃止。**異なるseedだけを100個作っても100代表局面として承認しない**。将来の100代表局面には各manifestケースの検証成功・独立した状態入力・必要機構群の網羅を要求する。
- 実ゲーム内のゴールデンケース、部分観測復元、任意メガ履歴、Windows/Android E2Eは今回も未検証。ゲートA=UNKNOWN、ゲートB=UNSUPPORTED、採用=NO_GOを維持。

## ゲートと実戦投入の禁止
A = 計算互換性。compatibility.csvの個別項目をChampions固定マスター・公式情報・実機ゴールデンケース（観測日時、レギュレーション、期待値、実測値）で監査する。現時点はUNKNOWN。Showdownにモジュールがあることは合格条件ではない。

B = 途中局面復元。残HP/ひんし/能力変化/状態異常/残PP/消費済み持ち物/技固定/メガ履歴/天候/壁等を、不完全な実ゲーム観測から正しく再構成し、同一seedでの一致・分岐相互不干渉を実証する。今回の合成状態だけでは合格としない。未対応ケースを明示する。

双方が合格かつ必要な計算仕様の互換が確認されるまで、採用判定はNO_GO。確定KO、安全受け、行動順保証の根拠にしてはならない。実戦モードのfeature flag切替と探索は本Issueの対象外。

## 配布・E2E未解決
ビルドコンテナは製品ではない。Nodeランタイムとコンパイル済みShowdownのWindows onedir同梱、オフライン起動、npm依存物通知・SBOM、クラッシュ復旧、Windows実測は別ゲート。Node単体の性能と「Androidミラー→OCR→PySide6表示」のE2Eは別記録。P95 3秒は目標案であって測定済みの保証ではない。

代替案：Champions専用に検証された狭い範囲の計算器を作り、未知局面はUNKNOWNに倒す。別エンジン（poke-engine等）のChampions対応と状態復元・ライセンス・Windows同梱を比較し、検証できないものを代替採用しない。

## 検証状況
- 2026-10-30にLinux Dockerによる固定Showdownビルド・合成PoC・静的監査をGitHub Actionsで実行。修正後HEADでの結果はPR #143の最新CIを参照。以前のSHAの成功を代替にしない。
- 初回静的監査でPP 33件・種族値21件・タイプ4件・習得技1件（計59件）の差分を検出。時点やフォーム等の要因を含むため、ゲーム内での不一致が確定したものではない。全件に原因分類の仮ラベル・再検証条件を付け、CIログに完全出力する。
- 差分分類 A=フォーム/種族キー対応の**候補**、B=Dex静的値とバトル内ルール値の比較層差の**候補**、C=ソース版差、D=Champions仕様の要独立確認、E=独立再現による実装不整合候補。現段階ではA/B/Dへ**暫定分類**し、C/Eを原因確定した件数はゼロ。全件の`cause_verified=false`・`validation_status=UNKNOWN`を保持し、固定マスターをShowdownに合わせて変更しない。
- 未実施：独立した実ゲーム内ゴールデンケース、部分観測からの途中状態復元、任意のメガ履歴、Windows配布とNode IPC、Androidミラーから推奨表示までのE2E、依存物SBOM/通知。
- CIの診断成功は**合成probe全件の正常性・Champions互換性・100代表局面の達成を意味しない**。probeで一時的な分岐再現性の不一致が出た場合、`UNSUPPORTED`と原因をレポートに保持し、所定の最低診断数とfail-closed採否条件のみ確認する。実ゲーム根拠として使用しない。
- 反復分岐の完全一致は別の実測指標：一部のCI実行では合成120反復のうち1件でseeded branchの比較が不一致になった。これを全件成功に繰り上げず、`replay_consistency_full`・`synthetic_replay_failure_count`・個別失敗ログを記録する。100件以上の合法かつ再現した反復があっても全120反復が完全再現したとは表示しない。CIの診断成功は再現性ゲートの合格証明ではない。
- **Issue #139の代表100局面は未達**：実ゲーム由来の独立観測は0件、チーム構成1、開始seed1、固有の入力状態5・manifestケース6。メガ後履歴、より多様な種族・技・道具・特性、HP帯/積み/状態異常、部分観測、交代選択肢・未知型の多様性は追加ケースの対象。測定iteration数・ユニークなstate hash・代表機構の範囲を混同しない。
- 両ゲートは未合格。本番採用やマージはしない。

## 2026-10-31 リプレイ時刻メタデータの正規化（Phase 0限定）
- ユーザーの独立Docker実測：120反復×10 runのうち6反復で `LOG_ONLY` 不一致。固定seed回帰が成功しても、単発CI greenのみでは再現性を認定しない。
- 実際の失敗例 `SYN-001` の `continuation` で `$.log.62` が `|t:|1793405687` と `|t:|1793405688` に分かれた。固定Showdown upstreamの `sim/battle.ts` は開始時・ターン開始時に `Date.now()` 由来の `|t:|` 秒数をログへ追加する。
- `state_compare.cjs::normalizeReplayTimestamps` は **リプレイ同一性を計算する時だけ**、ログ行全体が `|t:|<数字>` と一致する場合に限り秒数をプレースホルダーへ置き換える。`worker.cjs::projection` のハッシュで使用し、`Battle.toJSON()` の元データ・本番証拠・ユーザー観測・診断用生ログは変更しない。
- 技、ダメージ、持続ターン、天候、行動キュー、乱数、ログ欠落・差し込み、`|t:|` 以外の文字列差異は引き続き不一致として検出する。対象外の `LOG_ONLY` 不一致を一律成功へ昇格させない。
- 回帰テストは `test_state_compare.cjs` に追加。Linux/WindowsのCI結果と、独立複数runでの再現性・重要ケース不一致0件を別々に確認する。**ゲートA=UNKNOWN、B=UNSUPPORTED、Phase 0=NO_GOのまま**。ゲーム実機との互換性を示す変更ではない。

## 2026-10-31 技・特性・持ち物の実ターン診断（#139）

`champions_event_probe.cjs`は固定Showdownの実ターンを進め、独立した`champions_rule_reference.cjs`の期待状態と比較する。出力は`champions-showdown-event-diagnostics-v1`のJSONで、5契約の実行前状態・操作・期待状態・実状態・フィールド差分・出典・未確認条件を含む。固定seed `[1,2,3,4]`では **14 WIKI_SPEC_MATCH / 0 WIKI_SPEC_MISMATCH / 4 INCONCLUSIVE**。内訳と判定境界は`CHAMPIONS_RULE_CONTRACTS.md`を参照。Linux Phase 0 CIは新規Node回帰と診断CLIを実行する。Windows Ready PR CIは既存のデスクトップテスト・ビルドを担当し、Showdownイベント検証の代替にはならない。**実ゲーム互換・Gate B途中局面復元・本番採用は未確認**。

## 2026-10-31 静的監査59件の再比較（#139：Source-only）

**今回変更したのは比較層と明確な通常形マスターデータの訂正だけ。Champions実ゲームとの計算互換性を認定しない。**
- 旧監査は習得技共通キー（LearnsetKey）で種族値・タイプも参照していた。これにより、パンプジンのサイズ3種類（9差分）、ポワルンの天候3形態（3差分）、ギルガルドのブレード（4差分）、イルカマンのマイティ（4差分）が通常形との比較になっていた。新版では該当行のフォーム名を固定Showdownの個別Dex種族キーへ明示対応付けして比較する。比較キーは `explicit_form_comparisons` に列挙する。これらの**実戦フォーム変化の動作検証は別**。
- 旧監査のPP33差分は固定マスターの元PP25–40と、Champions mod初期化時の `min(元PP,20)` 後のDex PPを直接比較したもの。新版は同一レイヤーへ写像して比較し、変換した33件を `source_pp_cap_cases` に残す。`calculatePP` は通常技に対して戦闘時最大PPを別計算し、例えば元PP5→戦闘最大PP8。**マスターの元PPを実戦PPの列として書き換えない。** 実戦でのPP使用・回復・技合法性の独立観測はゲートAで必要。
- 通常メガニウムの固定マスターはメガ形と同じ種族値・タイプ・特性の誤登録だったため、通常形のみくさ単／しんりょく・リーフガード／80-82-100-83-100-80（525）へ修正。メガ形はくさ・フェアリー／メガソーラー／80-92-115-143-115-80（625）のまま。Champions特化資料を複数突合した（後掲）。
- ハガネールの「あまごい」は固定習得マスターにあり、固定Showdownの該当習得データにはない。Champions攻略サイトのハガネール習得技一覧にも見当たらないが、**ゲーム内の全技確認がないため削除せず `UNKNOWN` の1差分に残す**。不一致を単純にShowdownのバグと断定しない。
- 調査時点：2026-10-31、レギュレーションM-C。ユーザー指定の https://wiki.pokemonwiki.com/wiki/Pok%C3%A9mon_Champions は閲覧先が403を返したため本文の断定根拠には使用していない。
- 根拠（通常メガニウムとメガメガニウム）：https://gamewith.jp/pokemon-champions/553243 、https://gamewith.jp/pokemon-champions/553244 、https://appmedia.jp/pokemonchampions/79876418 、https://appmedia.jp/pokemonchampions/79876421
- 参考（ハガネール習得一覧）：https://gamewith.jp/pokemon-champions/553318 および https://app.gamepedia.jp/pokemon-champions/pokemon/208
- 固定Showdownの検証対象：`smogon/pokemon-showdown@ccfaa20f32af363569cd21a9d501b2bc1ded26b1` の `data/mods/champions/scripts.ts`（PP制限と計算）、`data/pokedex.ts`（フォーム別種族キー）、`data/mods/champions/learnsets.ts`。
- 回帰テスト：`test_audit.cjs`（フォーム個別キー、PP比較層と実戦PPの区別、メガニウム通常/メガ別値、ハガネール未確定差分保持）。`audit.mismatch_count` が1まで減っても、**計算互換性A=UNKNOWN、途中復元B=UNSUPPORTED、採用=NO_GO**を維持する。

再現（Dockerイメージは現ブランチから再ビルド）：

```bash
docker build -f poc/showdown_phase0/Dockerfile -t champions-phase0 .
docker run --rm --entrypoint node champions-phase0 --test test_decision.cjs test_state_compare.cjs test_audit.cjs
docker run --rm --entrypoint node champions-phase0 audit.cjs > phase0-audit.json
```

### 2026-10-31 追補：ハガネールの「あまごい」習得リスト修正

- M-CのChampions専用習得技表の独立照合：Serebii [Rain Dance / Pokémon That Learn Rain Dance as standard in Pokémon Champions](https://www.serebii.net/attackdex-champions/raindance.shtml) にSteelixの記載なし。[PChamp DB ハガネール](https://app.gamepedia.jp/pokemon-champions/pokemon/208) と [AppMedia ハガネール](https://appmedia.jp/pokemonchampions/79876502) にも「あまごい」は掲載されていない（2026-10-31取得）。Showdown固定SHAの `data/mods/champions/learnsets.ts` にも `steelix.raindance` は存在しない。
- `masters/Pokemon_Champions_MC_pokemon_learnsets_linked.csv` の通常ハガネール／メガハガネールの共有 `steelix` リストから `raindance` と日本語「あまごい」をそれぞれ1件削除し、習得技数67→66に同期。過去の一般作品の習得技を流用せず、**Champions用の独立した公開資料と固定ソースの合致**を根拠とする。ゲーム内直接確認は未実施。
- `test_audit.cjs` に両行の技ID・日本語名・件数の一貫性テストを追加。現行の静的監査は差分 `59→0` を期待する。**静的データ比較の一致は、実ゲームでの習得可否の最終保証、ダメージ互換性、任意の途中局面復元の証拠ではない。**
- 独立ゲートは **A=UNKNOWN / B=UNSUPPORTED / Phase 0 adoption=NO_GO** のまま。100代表局面・実ゲームゴールデンケース・Windows E2Eも未達。
