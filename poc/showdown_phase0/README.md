# Issue #139 / Phase 0 — Pokémon Showdown Champions 技術PoC

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
