# Pokémon Champions リアルタイムAI対戦支援システム
## P0実装契約・未確定項目の仕様化 v2.1

- 作成日: 2026-10-21
- 親文書: `Pokemon_Champions_Integrated_Architecture_v2_0.md`
- 目的: P0の7項目をプログラム実装可能な契約へ落とす。実機で未確認のUI・Champions固有効果は「検証待ち」とし、仕様を創作しない。
- 戦術上の唯一正本: `Pokemon_Champions_MC_live_decision_gate.txt`
- 現行自軍構築: `Pokemon_Champions_MC_team_2026-10-03.csv`
- この文書が定めるのはオーケストレーション、状態更新、認識根拠、入力・API同期の契約。戦術判断の代替・上書きではない。

## 0. 用語と不変条件

**識別子**

| キー | 意味 | 発番単位 |
|---|---|---|
| `match_id` | 試合ごとの不変ID | マッチング開始・新試合識別時 |
| `turn_id` | 実ゲームターンの識別子。未確定なら番号を捏造しない | ターン境界が確定した時 |
| `resolution_id` | 1つのターン内の行動解決エピソード | コマンド確定→結果観測 |
| `activation_event_id` | 一回の技・特性・道具発動単位 | 発動観測時 |
| `effect_instance_id` | 発動の個別効果、対象、効果位置で一意化 | 効果候補生成時 |
| `decision_window_id` | プレイヤーの1回の独立した選択機会 | 新しい選択権が実際に生じた時 |
| `observation_revision` | OCR・検出証拠の更新履歴 | 観測追加時 |
| `tactical_state_version` | AI判断を変え得る情報の改訂番号 | 戦術影響のある状態変化時 |
| `allowed_action_digest` | 現在選択可能な技・交代先・固定・フォームの指紋 | 合法候補が変わる時 |

**常時守る不変条件**
1. ポケモン選択UIは1種類 `PARTY_MENU` とし、交代理由は保持済みの履歴で識別する。
2. ターン終了は「行動が終わった」「ポケモン一覧が開いた」「選択画面が閉じた」と同義でない。
3. OpenAIの判断がない行動をPythonで戦術的に生成しない。有効な既存OpenAI判断の再利用は許可する。
4. API応答は `(match_id, decision_window_id, tactical_state_version, allowed_action_digest)` が現在値と一致した場合のみ表示候補になる。
5. 戦闘状態更新は、発生源の既知効果を予測し、実際の成立を観測してから**1回だけ**反映する。
6. 観測事実・規則に基づく条件付き候補・不明を分離。未確認のChampions仕様を他作品から補わない。
7. 選出/必須PREP/対戦中の制限時間・参照上限・勝ち筋評価は既存LIVE判断ゲートが優先する。

## P0-1 ターン終了判定 / 持続効果カウンター

### 状態
`TURN_OPEN` → `RESOLUTION_ACTIVE` → `TURN_END_CANDIDATE` → `TURN_END_CONFIRMED` → `NEXT_TURN_OPEN`。
途中の死に出し・交代技・情報画面・UI再表示は同じ`turn_id`内の下位イベント。

### 判定証拠の優先順位
- **強い証拠 A**: ゲーム内で明示的に確認できたターン番号の進行、または実機録画でターン終了を一意に特定できる確定イベント。実際に取得可能かはP1検証対象。
- **組合せ証拠 B**: このターンのコマンド実行が確定済み＋行動解決/ターン終了処理が観測済み＋**新しい**通常コマンドの選択権を確認。単なる情報画面からの復帰は該当しない。
- **弱い証拠 C**: アニメーション停止、技表示終了、ポケモン一覧表示、一定時間経過。これらだけではターン終了にしない。

### 決定規則
1. `TURN_END_CANDIDATE` はカウンターを減らさない。
2. A、または矛盾のないBが揃ったら `TURN_END_CONFIRMED(match_id,turn_id)` を一意に発行。
3. 同じ`(match_id,turn_id)`の`TURN_END_CONFIRMED`は何回受信しても1回だけ適用する。
4. 天候・フィールド・壁の候補残数は確定終了時だけ進める。**ターン途中で発生した効果も、そのターンの終了処理で1回分を消費**する（既存v2.0の定義）。
5. 一部ターンを見落として境界が曖昧な場合は残り候補を広げる、または`unknown`へ降格し、画面詳細・消滅表示等で再同期する。勝手に1ターン分を進めない。
6. 同ターン内の交代技・強制交代・死に出しでターンを進めない。ゲームが別の入力窓を開いても別判定。
7. 効果の上書き・解除・終了が確認された場合、推定残ターンより実際の観測を優先する。

### 実機検証待ち
「ターン番号の明示UI」「行動解決完了の確定的な演出」「残りターン表示の可用性」は実際のBlueStacks録画で確認。保証できない場合、Bの証拠と不確実性管理で動かす。

## P0-2 効果マスターの範囲とデータ契約

### 正本と派生物
- 技正本 `Pokemon_Champions_MC_move_master.csv` は510行。名前・威力・分類・優先度等はあるが、**全技の効果量を網羅する列はない**。
- 特性正本 `Pokemon_Champions_MC_ability_master.csv` は216行。効果日本語要約あり。構造化補助 `Pokemon_Champions_MC_ability_ai_master.txt` を使用。
- 道具正本 `Pokemon_Champions_MC_usable_held_items.csv` は166行。効果日本語要約あり。構造化補助 `Pokemon_Champions_MC_held_item_ai_master.txt` を使用。
- 新たな `Pokemon_Champions_effect_rules.jsonl` は**派生効果マスター**。元データの改変・未確定情報の確定化は禁止。

### レコードの必須概念
`rule_id`, `source_type(move/ability/item)`, `source_name`, `trigger`, `conditions`, `effects`, `duration`, `observation_required`, `verification_status`, `source_file`, `source_excerpt`, `notes`。

`verification_status` の運用:
- `VERIFIED`: Champions元データに効果が明記され、構造化内容が一致。**発動の実戦観測の要否は別**。
- `PARTIAL`: 効果の一部は抽出できるが、条件・例外・数値が欠ける。
- `UNMAPPED`: 対応する技/特性/道具は存在するが効果を安全に構造化できない。
- `CONFLICT`: 出典とゲーム内観測または複数の根拠が矛盾。手動レビュー対象。

**運用上重要**: `VERIFIED` な効果レコードでも「その試合で発動した」という意味ではない。`activation`と`resolution`は別管理。

### 継続時間
`duration` は `{unit:"turn",base_candidates:[5], extended_candidates:[8], extension_source:"item_name_or_unknown", activation_turn_known:true/false}` などの**候補集合**を許容。相手の延長道具が未判明なら両候補を持つ。道具マスターが「延長」としか記載しない場合は、**具体的な8という値の根拠を別途記録**し、元データにない数値を元データ出典として扱わない。

### 構築方針
全件を`VERIFIED/PARTIAL/UNMAPPED`で**網羅登録**し、効果の機械可読整備は自軍技・環境上位・交代/行動保証/天候/壁/ステロを優先。元データにないことを推測補完しない。

## P0-3 イベント成立・失敗と二重適用防止

### イベント状態
`OBSERVED_ACTIVATION` → `PENDING_EFFECT` → `CONFIRMED | FAILED | NEGATED | CONFLICT`。

- `OBSERVED_ACTIVATION`: 「技名が表示された」「特性名が出た」「道具使用を確認した」という**発動源の観測**。
- `PENDING_EFFECT`: 効果マスターから候補を生成した段階。現在の確定状態は**書き換えない**。
- `CONFIRMED`: 必要な条件/効果成立を観測した段階。アトミックに1回だけリデューサで適用。
- `FAILED/NEGATED`: 技不発、無効、条件未成立等が確認された。該当効果は適用しない。
- `CONFLICT`: OCR/状態表示/技効果が矛盾。誤った確定値を採用しない。再照合対象にする。

### 判定方法の具体例
- `りゅうのまい`: 技名確認→マスターから能力変化候補→実際の技成立・能力変化表示を照合→適用（技マスターに変化量が未構造化なら、その規則は別途検証待ち）。
- `いかく`: 登場/特性発動→特性マスターから相手攻撃−1の候補→無効化等の未確認例外を確認し、成立分だけ適用。
- 追加効果: 命中しただけで抽選結果を確定しない。追加効果の実際の発生を観測する。
- 能力変化メッセージ: 技の効果と**同じ`effect_instance_id`に証拠追加**。二重加算しない。
- 複数段階/複数対象: `(activation_id,rule_id,target_id,effect_index)`で各効果を区別する。

### 原子性・再生
`battle_events` は追記専用。`confirmed_effects` に一意キーを設定し、**イベント保存と状態更新を1トランザクション**で実施。OCR再処理時は同じ発動エピソードへ結合。再生時も結果が一致する決定的リデューサにする。

## P0-4 入力機会の開始・終了 / PARTY_MENU共有

### UI層（画像のみ）
`TEAM_SELECTION_UI`, `COMMAND_MENU`, `MOVE_MENU`, `PARTY_MENU`, `ACTION_RESOLUTION`, `RESULT_SCREEN`, `UNKNOWN`。
**`PARTY_MENU`は交代理由別に画像分類しない。**

### 判断窓の状態
`CLOSED` → `OPEN_CANDIDATE` → `OPEN_CONFIRMED` → `AI_REQUESTED` → `AI_RECOMMENDED` → `PLAYER_COMMIT_PENDING` → `RESOLVED`。
例外: `EXPIRED`, `CANCELLED`, `UNRESOLVED`。

### 新窓発行条件
実際にプレイヤーの**新しい独立選択権**が発生した証拠を確認したときのみ、新しい`decision_window_id`を発行。
- `COMMAND_MENU` → `MOVE_MENU` → `COMMAND_MENU`: 同じ通常判断窓。
- `COMMAND_MENU` → `PARTY_MENU` → `COMMAND_MENU`: 同じ通常判断窓。通常交代を開いて戻ってもAPI重複なし。
- `とんぼがえり`等の実行成立 → `PARTY_MENU`: **別の選択UI**だが、先のOpenAIが選んだ予定交代先を現在状態で使えるなら、**新APIなしで既存AI計画を実行**。別の`decision_window_id`を記録しても`parent_decision_id`で紐づける。
- 自軍ひんし → `PARTY_MENU`: 新しい判断窓。OpenAIに次の投入先と後ろに残す個体を判断させる。
- 交代投入後の**新しい** `COMMAND_MENU`: 直前の投入先判断とは別の窓。OpenAIに新たな技/交代判断を依頼。
- `ほえる`等の選択しないランダム強制交代: 入力窓なし。入れ替わった結果だけ記録。
- 道具/特性による交代: **実際に選択可能UIが存在した場合**だけ窓を開く。種類から操作可能と憶測しない。

### 通常交代とポケモン一覧の区別
`PARTY_MENU`があること自体はAPI呼出条件ではない。`window_context`として `NORMAL_COMMAND/PIVOT_TARGET/FAINT_REPLACEMENT/EFFECT_REPLACEMENT/UNKNOWN_CONTEXT` を保有し、UIから直接理由を推測しない。

### 同一窓の重複回避
一意キー `(match_id, decision_window_id, tactical_state_version, allowed_action_digest)` で送信済み判断を識別。UIを何度開き直しても同じキーへの重複問い合わせは行わない。**ただし重大な新事実で再判断が必要なら**同一窓内の新バージョンとして送信し、旧応答を失効。

## P0-5 プレイヤー実行行動の確定

推奨と実操作は別状態で追跡する。

| 段階 | 定義 | 典型的根拠 |
|---|---|---|
| `AI_RECOMMENDED` | OpenAIが行動を選んだ | API応答検証通過 |
| `PLAYER_COMMIT_PENDING` | UIが閉じた/選択操作が推測される | 暫定。**実行確定ではない** |
| `ACTUAL_ACTION_CONFIRMED` | 技使用・メガ/交代など実際の行動が始まった | 技使用メッセージ、交代成立映像等 |
| `EFFECT_RESOLVED` | 技成功/失敗、与ダメ、場の変化まで確定 | 結果・HP・メッセージ等 |
| `UNRESOLVED` | 観測欠損・通信断で確定できない | 未確定のまま保持 |

OpenAIの提案と違う技/交代を人間が行った場合、支援ミスと人間の入力差分を分離して記録。メガなどの行動宣言も、提案の表示だけでは成立扱いしない。技使用メッセージを見落としてもHP変化や場面から推測だけで技名を確定しない。

## P0-6 OpenAIへの現在状態同期と応答の検証

### API入力: 静的部/試合部/窓部
1. **静的部**: 現行LIVE判断ゲートの必要規則とChampions固有禁止事項。キャッシュ効率のため安定した順序に置く。
2. **試合部**: 選出3匹の4技/性格/配分/道具/特性/フォーム、選出前に取得した相手6行、保存済み18対面PREP、主要勝ち筋。履歴全件を毎回送信しない。
3. **窓部**: 最新自軍/相手の確定・候補・不明状態、HP、技固定、能力、現在タイプ、メガ、天候/壁の残り候補、予測し得る相手の先制/積み等、合法候補、残り選出、直近確定イベント、入力期限。

**重要:** API側へ渡すことをPythonの事前ロード成功と混同しない。4ファイルの本文は今回試合開始時にローカル実読込し、モデルには判断に必要な情報を供給。APIリクエストごとに判断に必須の現在状態を明示し、過去の応答記憶のみには依存しない。

### 送信例（フィールド契約）
```json
{
  "protocol_version":"2.1",
  "match_id":"sample-match",
  "decision_window_id":"dw-012",
  "decision_kind":"FAINT_REPLACEMENT",
  "tactical_state_version":42,
  "allowed_action_digest":"sha256:...",
  "deadline_ms_remaining":14000,
  "confirmed_state":{"active_own":null,"active_opponent":"ギャラドス","board":{},"roster":{}},
  "uncertain_state":{"opponent_item":"unknown","remaining_turns_candidates":[2,5]},
  "allowed_actions":{"switch_targets":["マスカーニャ","ミミッキュ"]},
  "relevant_prep":{"preserve":"...","processor":"...","closer":"..."},
  "task":"投入先と温存対象を全体勝ち筋から判断"
}
```

### 応答検証
`match_id`, `decision_window_id`, `tactical_state_version`, `allowed_action_digest` が全て最新値と一致し、OpenAIの出力が該当窓の**合法候補**に含まれること。ローカル合法性検証は戦術的代案を生成しない。旧応答/不正/不明なら破棄または`NO_DECISION`。OpenAI Responses APIのStructured Outputs（`text.format` のJSON Schema）を利用する場合、出力スキーマ準拠と戦術的正しさは別に確認する。

### 版管理
- OCR再読み込みなど戦術に無関係な観測だけなら`observation_revision`を進め、戦術判断を不必要に取り消さない。
- HP、能力、タイプ、行動順、持ち物、技固定、控え、合法候補などの変化は`tactical_state_version`を進める。**重要度が低いかどうかをPythonが戦術判断しない**。分類基準は固定ゲートの既知依存項目を含む。
- 交代技の予定交代先が有効でも、現在盤面に重大差分があれば既存計画を再利用せず、新しいOpenAI判断にする。

## P0-7 期限・API障害・レース対策

### 期限
- 選出: LIVEゲートに従い10〜15秒を目標、20秒上限。
- 必須PREP: 選出返答直後から40秒。18対面+勝ち筋3点が不足なら未完成。相手初手が出れば即打切り。
- 戦闘判断: LIVEゲートに従い5秒目標、重大更新10秒目標、15秒上限。ゲーム内入力残り時間を超えない。
- システムの`deadline = min(ゲート上限, 実画面の入力期限 - 安全余白)`。**安全余白の秒数は実測で決める**。架空の固定時間を保証しない。

### 非同期I/O
`capture/OCR/state_reducer/overlay`とAPI呼出ワーカーは独立。API待ち中も観測を止めない。
`new_game_state`・`new_window`・`match_end`・`opponent_lead_visible` は未完の古い判断の**表示権限を即失効**。通信そのものが取消可能かと、旧結果を表示しないことは別の概念。

### 失敗時
- 同じ窓への無制限再試行は禁止。リトライは**期限内に間に合う根拠がある場合のみ**で、試行回数は初期1回以下を推奨（実測後に確定）。
- API失敗/期限超過なら`NO_DECISION`。Pythonは技を勝手に選ばない。
- 認識が曖昧でも入力期限が迫れば、**不確実性を明示した最新スナップショット**でOpenAI判断を求め得る。安全な合法候補を特定できなければ無理に実行推奨しない。
- 勝敗確定時は該当`match_id`の戦術ジョブを全失効。対戦レビューは別ジョブで処理。

## P0補足: 状態再同期・ログ完全性

- UIの詳細表示/HP/状態異常アイコン/能力表示を**確認済み根拠**として、イベントリデューサの保持結果と比較。
- 不一致は`RECONCILIATION_REQUIRED`とし、直近画面バッファ再解析、明示的ユーザー訂正、消滅メッセージなどで解決。勝手な補正で「確定」にしない。
- `raw_observations`, `battle_events`, `effect_candidates`, `confirmed_effects`, `state_snapshots`, `decision_windows`, `api_calls`, `ai_decisions`, `actual_actions`, `review_results` を別テーブル/ログで保持。
- イベントは時刻と連番で並べ、OCR読取順の入替がある場合は暫定的に保留・再整列。カウンターを二度動かさない。
- ログは端末内に保持。APIへは戦術に必要なフィールドだけ送る。ログには個人情報・APIキーを保存しない。

## 受入試験の必須セット（録画リプレイ＋単体テスト）

| ID | 事象 | 合格条件 |
|---|---|---|
| AT01 | `TURN_END_CONFIRMED`が二重に届く | 残りターン減算は1回 |
| AT02 | 同一ターン中のとんぼがえり→交代先UI | ターンカウンター減算0回、交代計画再利用条件を評価 |
| AT03 | ひんし後の死に出し→通常コマンド | 別の判断窓2つ。投入先も技もOpenAI判断 |
| AT04 | 通常コマンド→控え一覧→戻る | 同じ窓。重複API0回 |
| AT05 | ほえるでランダム交代 | 交代選択API0、次の通常入力で1回 |
| AT06 | 同じ能力変化メッセージが複数フレーム | 1効果1回だけ反映 |
| AT07 | 技失敗/能力変化無効 | `PENDING`を失敗にして変化0 |
| AT08 | 追加効果が表示されない | 成立確認がなければ確定しない |
| AT09 | 相手道具不明の光の壁 | 持続候補を維持し断定しない |
| AT10 | ターン境界をOCRが取りこぼし | 残ターン`unknown`/候補へ降格、捏造しない |
| AT11 | API待機中に相手メガ/現在タイプが変化 | 旧`state_version`応答を表示しない |
| AT12 | 遅れて旧`match_id`の応答が届く | 破棄して新試合に影響させない |
| AT13 | OpenAIがこだわり固定と矛盾する技を提案 | ローカル検証で拒否、独自技は生成しない |
| AT14 | 使用技がAI推奨と異なる | 実際に観測した技を記録し、差分を別ログ化 |
| AT15 | PREP中に相手初手が出現 | PREP終了、未完なら未完ログ、新たな行動窓優先 |
| AT16 | `PARTY_MENU`の直前理由が不明 | `UNKNOWN_CONTEXT`として保持、理由を作らない |
| AT17 | 成立した晴れが別天候で上書き | 古い天候インスタンス停止、現在効果のみ継続 |
| AT18 | UI詳細表示と推定壁残数が矛盾 | `RECONCILIATION_REQUIRED`、明示表示を用いて再同期 |
| AT19 | 道具/特性効果で選択式交代が実際に開く | 新判断が必要ならOpenAI、選択UIがなければAPI0 |
| AT20 | API障害中に技入力期限到来 | `NO_DECISION`、古い提案を現在の指示として表示しない |

## 依存関係と残件（未確定を隠さない）

**P0の仕様契約は本書で確定。実装・試験は未実施。**
- P1: BlueStacks実際のUI座標、ターン境界の強い証拠、技・特性/道具メッセージの実語句、強制交代・だっしゅつボタン等のUI挙動、HP精度、継続残数UIの表示仕様、通信断・降参など。
- P2: モデル選定、実際のAPI遅延p95、画面取得方式の速度、OCR閾値、秒単位安全余白、プロンプト短縮とキャッシュ率。
- 効果マスターの数値確定: 技マスターの不足分をChampions専用ソースと実画面証拠で追加。今回は初期候補ファイルを別添するが、全面網羅/検証済みと誤解しない。

## 参照
- `Pokemon_Champions_Integrated_Architecture_v2_0.md`
- `Pokemon_Champions_MC_live_decision_gate.txt`（LIVE唯一正本）
- `Pokemon_Champions_MC_move_master.csv`
- `Pokemon_Champions_MC_ability_master.csv` / `Pokemon_Champions_MC_ability_ai_master.txt`
- `Pokemon_Champions_MC_usable_held_items.csv` / `Pokemon_Champions_MC_held_item_ai_master.txt`
- `Pokemon_Champions_S05_Battle_FSM_Design_v1_2.md`
- `Pokemon_Champions_OpenAI_Prompt_Contract_v1_3.md`
