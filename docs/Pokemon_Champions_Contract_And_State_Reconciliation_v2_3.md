# Pokémon Champions リアルタイムAI対戦支援 — 契約統一・状態同期/訂正仕様 v2.3

- 設計日: 2026-10-21
- 上位文書: `Pokemon_Champions_Integrated_Architecture_v2_0.md`、`Pokemon_Champions_P0_Implementation_Spec_v2_1.md`
- 対象: 未確定台帳v2.2の **①契約一本化 (D04/D05)** と **②送信前確定/認識漏れ/状態訂正 (D01/D02/D03/D07)**
- 対象外: ゲーム画面の実座標・認識モデルの校正、未構造化のChampions技・特性・道具の詳細効果、API実通信のp95計測
- 戦術判断の唯一正本: `Pokemon_Champions_MC_live_decision_gate.txt`。この仕様およびPython実装は戦術判断を行わない。

## 1. 今回の結論・確定事項

| 課題 | 新しい確定仕様 | 状況 |
|---|---|---|
| D04 | APIのwire版を `protocol_version="2.3.0"` に統一。全要求・全回答に `request_id`、`match_id`、`decision_window_id`、`tactical_state_version`、`allowed_action_digest` を必須化 | JSON Schema作成＋単体テスト済み |
| D05 | 効果マスターの正式名称は **`confirmation_required`**。`observation_required` は旧設計文中のみの別名とし、wireへ出さない | v0.2.0カタログに移行・全892件検証済み |
| D01 | API送信前に観測処理のウォーターマークを確認。同期待ち、期限切迫時の不確実性付き送信、致命的不明時の停止を分離 | 判定ロジックの参照コード・テスト済み。実機待ち |
| D02 | OCR再解析・画面再照合・明示的手動訂正を **追記イベント** として保存。決定的な再生で現在状態だけ更新。過去送信内容は不変 | 参照リデューサ・テスト済み。SQLite本実装は別工程 |
| D03 | 全フェーズに `context` と `battle_state`、`snapshot_quality` を統一し、選出3匹の4技とPREPを必須送信対象に設定 | 入力Schema＋ローカル必須チェック済み |
| D07 | 選択肢の認識が不完全なら actionable なAI推奨として表示しない。候補を捏造しない | ハードゲート済み。OCR校正待ち |

重要な区別：**設計契約・参照コードが成立**したことと、**BlueStacks上で連続戦闘を正常追跡できたことは別**。まだ後者は未検証。

## 2. 正式なファイルと版の切り分け

### 2.1 API wire 契約は `2.3.0`

- `Pokemon_Champions_API_request_schema_v2_3.json` — ローカルで生成するAPI入力の検証用（JSON Schema Draft 2020-12）
- `Pokemon_Champions_API_response_select_schema_v2_3.json` — 選出3匹＋初手
- `Pokemon_Champions_API_response_prep_schema_v2_3.json` — 18対面＋温存/停止役/締め
- `Pokemon_Champions_API_response_battle_schema_v2_3.json` — 通常コマンド/交代技交代先/死に出し/効果による選択交代
- `Pokemon_Champions_API_response_review_schema_v2_3.json` — 試合後振り返り

今後の **wire実装は `protocol_version="2.3.0"` のみ送受信**する。v1.3のプロンプト文書、v2.1の概念JSONは設計の履歴として保持するが、wire版として混在使用しない。互換が必要なら明示的変換器を介す。

`SELECT`, `PREP`, `NORMAL_COMMAND`, `PIVOT_TARGET`, `FAINT_REPLACEMENT`, `EFFECT_REPLACEMENT`, `REVIEW` を入力 `phase` に定義。出力は `SELECT`/`PREP`/`BATTLE`/`REVIEW` の4つのschemaに集約。後者の `BATTLE` は4種類の選択機会を共通構造で表現するための**出力分類**であり、交代理由を画像識別器に認識させるという意味ではない。

全フェーズの出力で同じキーをエコーする:

```text
protocol_version
request_id
match_id
decision_window_id
tactical_state_version
allowed_action_digest
decision_status       RECOMMENDED | NO_DECISION
reason_code
warning               string | null
```

**モデル出力のSchema準拠だけで表示してはいけない。** 最新のローカルスナップショットに対するID/版一致、合法候補、形態・技固定・選択窓を照合して初めて表示可能。

### 2.2 効果マスターは独立した `0.2.0`

- `Pokemon_Champions_effect_rule_schema_v0_2.json`
- `Pokemon_Champions_effect_rules_catalog_seed_v0_2.jsonl`

機械可読な効果定義の正本フィールド:

| 項目 | 意味 |
|---|---|
| `schema_version` | 効果レコード版 `0.2.0` |
| `rule_id` / `source_type` / `source_key` / `source_name` | 発動源と識別子 |
| `trigger` / `conditions` | 発動契機と条件。未整備は `UNKNOWN` 等のまま |
| `effects` | 検証可能な機械可読効果。未整備は空配列 |
| `duration` | ルール自体の持続時間候補。個別試合の開始ターンはここに置かない |
| **`confirmation_required`** | 効果の成立を確認する証拠条件。未整備なら空欄を「確認不要」と解釈しない |
| `verification_status` | `VERIFIED`, `PARTIAL`, `UNMAPPED`, `CONFLICT` |
| `source` | `file`, `field`, `excerpt`, `checked_date` をもつ出典情報 |
| `notes` | 根拠不足・例外・注意事項 |

旧v2.1概念名の移行マッピング:

```text
observation_required  → confirmation_required
source_file           → source.file
source_excerpt        → source.excerpt
activation_turn_known → 効果発動インスタンス/対戦状態側にのみ持つ
```

`confirmation_required=[]` かつ `UNMAPPED` の場合、**無条件自動確定してよいという意味ではない**。成立条件が不明という意味なので、未確定イベントとして扱う。ゲーム外作品の知識から能力上昇量やターン数を補填しない。

### 2.3 移行結果

旧892件（技510、特性216、持ち物166）から、旧レコードの内容を変更せず `schema_version` を追加して移行。`VERIFIED` 5、`PARTIAL` 7、`UNMAPPED` 880を維持。元データに効果を勝手に追加していない。JSON Schemaで892件検証済み。

> 現行のv0.2は「構造の統一と台帳の保全」の段階。**892件の効果を計算できるエンジンが完成したわけではない**。

## 3. API入力に必ず含める情報

### 3.1 envelope

`protocol_version`, `request_id`, `match_id`, `phase`, `decision_window_id`, `tactical_state_version`, `observation_revision`, `allowed_action_digest`, `deadline_ms_remaining`。

`decision_window_id` は `SELECT`/`PREP`/`REVIEW` にも専用のジョブIDを発行し、必須の形式を統一する。`observation_revision` は画面証拠の追加ごと、`tactical_state_version` は現在の意思決定に関わる確定/候補/不明/矛盾が変わったときに更新。番号を同一視しない。

`source_manifest` は開始時に**実際に読み込んだ4ファイル**のSHA-256を含む。新構築への更新は次戦から適用。試合途中のファイル更新やAPIの記憶は今回の実読込成功とみなさない。試合中のPythonによるファイル再読込・Web検索は行わない。

### 3.2 `allowed_actions` と指紋

構造:

```json
{
  "completeness": "COMPLETE",
  "moves": ["技A", "技B"],
  "pivot_moves": ["技B"],
  "switch_targets": ["控えA", "控えB"],
  "team_candidates": [],
  "mega_available": false,
  "choice_lock": null,
  "active_form": null
}
```

`allowed_action_digest` は `protocol_version`,`phase`,`completeness`,`moves`,`pivot_moves`,`switch_targets`,`team_candidates`,`mega_available`,`choice_lock`,`active_form` の正規化JSONを**UTF-8 + SHA-256**でハッシュ化。候補配列はソートしてハッシュ化。`sha256:<64文字の小文字hex>` を送信する。UIの表示順が変わっても候補集合が同じなら同一指紋。候補の区別には将来的に安定した個体/スロットIDを使う（同名候補をラベルだけで一意識別しない）。`pivot_moves` はChampions固有の確認済み対象のみ登録。

合法手の完備性が `PARTIAL`/`UNKNOWN`、または正しい候補が読み取れない場合は、**戦術的に選択肢を削って進めず `BLOCKED`** とする。人間による手動補正・画面再照合による解消を優先する。確定した候補がある場合でも、未知の候補を排除した最適化をしない。

### 3.3 `context` と `battle_state`

`context`:

- `own_team`: 自軍個体ごとの名前、**4技**、道具、特性、性格、配分、役割、Sメモ、温存優先度、メガフォーム。`SELECT`は6匹、試合中は選出3匹全員を必ず含める。
- `selected_three`: 実際の試合でロックした3匹。推奨選出との差異が観測された場合は実選出を優先（D06の再PREP詳細は別課題）。
- `opponent_preview_six`: 対戦相手のプレビュー6匹。推定の相手実選出3匹と混同しない。
- `live_matrix_rows_six`: 試合開始前に一括抽出済みのマトリクス6行。ターン中に再検索しない。
- `prep`: `COMPLETE/INCOMPLETE/STALE/NOT_STARTED`＋18対面の圧縮結果、温存/停止役/締め。未完を完了扱いしない。
- `gate_sha256`: 戦術ルール正本のハッシュ。

`battle_state`:

- 自軍/相手の現在場、残存/判明/未判明数、HPの確度、能力ランク、状態異常、フォーム・現在タイプ。
- 天候・フィールド・壁・設置状態と残りターン候補。
- 双方の技固定、判明技/道具/特性、実測行動順、直近の確定イベント。
- 未確定イベントIDと不明点。**推定は推定のまま**渡す。

`field_effects`, `stat_stages` 等の値は `value` ＋ `certainty=CONFIRMED/CANDIDATE/UNKNOWN/CONFLICT` ＋ `evidence_ids` で送る。HP推定は確認済み扱いにせず確度を明示。相手の不明持ち物、継続ターンの複数候補を一本化しない。

### 3.4 出力のフェーズ別意味

- `SELECT`: `selected_three` が異なる3匹、`lead` はそのうち1匹。Pythonが候補集合と照合。
- `PREP`: `matchups_18` を**1対面1行の短縮コード**として返し、勝ち筋3点が揃ったときのみ `prep_status=COMPLETE` が妥当。Pythonが選出3×相手6の**集合一致**と短縮コードの必須タグを検証。初手6対面は `ACT,KO,DPS,PRI,S,OUR,OPP,SAFE,PROC,LOCK,MEGA`、控え12対面は `S,OUR,OPP,PRI,SAFE,PROC` の項目が必須。実測情報がなければ `?`（不明）を認めるが、`PROC`（明示処理役評価）と初手6の`ACT`・`DPS`は「未評価」を意味する `?` のみでは完成判定を通さない。評価項目の省略も禁止。未完なら `INCOMPLETE` を残し「事前評価完了」と表示しない。
- `BATTLE`: `action_kind=MOVE/SWITCH/NONE`, `move`, `mega`, `switch_target`, `planned_pivot_target`, `keep_back` を必須フィールドとして持つ（非該当はnull）。`NORMAL_COMMAND`以外で技を選ばない。交代技の予定交代先はOpenAIが決定し、技発動後に盤面差分の影響を確認。\n- `REVIEW`: 試合終了後の別ジョブ。過去スナップショット不変のまま選出/プレイ/支援側の問題を分類。

## 4. OCR欠落・状態訂正の詳細仕様（D02）

### 4.1 三層モデル

1. **証拠イベント** `raw_observations` — `frame_seq`, 単調時刻, ROI, 読み取った文字/確度, スクリーンショット参照を保持。
2. **意味イベント** `battle_events` — `activation_event_id`, `effect_instance_id`, 成立候補、`CONFIRMED/FAILED/NEGATED/CONFLICT` を保持。
3. **現在状態プロジェクション** — イベント再生で算出。**推奨AIに送るのはこの現在状態のスナップショット**で、認識失敗の元文章ではない。

OCRは確認待ちの技表示・追加効果を一時保留。効果マスターが `VERIFIED` でも、今回の発動成立は別途観測が必要。`UNMAPPED` の効果は不明として保持する。

### 4.2 補正の優先順序

1. 直近のフレームバッファから戦闘テキスト/対象アイコン/残存数などを再解析。
2. 詳細状態表示等の**直接観測**と再照合（実際に表示できる項目は実機確認）。
3. 技・特性・持ち物マスターの条件付き効果と突き合わせる。発動未確認を確定しない。
4. ユーザーの明示訂正は `MANUAL_VERIFIED` の新イベントとして追記する。
5. なお衝突するなら `CONFLICT`、未判明なら `UNKNOWN/CANDIDATE` を保持する。

証拠品質の参照順: `MANUAL_VERIFIED > UI_DIRECT > BATTLE_TEXT > RULE_DERIVED`。**これは情報をどう照合するかのローカル順序であり、戦術ルールではない**。OCRが読めたというだけで誤読の可能性が消えるわけではなく、直接UIも信頼度条件を満たさなければ `CANDIDATE`。低優先根拠との不一致を隠さず `disagreement` に残す。同じ優先順位の確定根拠が衝突すれば `CONFLICT` とし、勝手に一方を採用しない。

### 4.3 訂正は上書きせず追記

```text
seq101  BattleText  "晴れ"      weather=sun      (evidence #a)
seq103  DirectUI    "雨"        weather=rain     (evidence #b)
        → 現在値 rain / 不一致警告あり
seq105  Manual      前の# aは誤OCR、weather=rain、supersedes=[#a]
        → 現在値 rain / 不一致解消
```

*ただし上の表示文はイベント流れの架空例であり、実際のChampionsの表示文を保証しない。*

上の訂正時、過去にAPIへ送った `seq101` 時点の入力は絶対に書き換えず、**次の判断には訂正済み状態**を使う。過去のAI判断を「当時知り得なかった情報」で再採点しない。

### 4.4 未解決イベントの版更新

新規の `PENDING_EFFECT` が観測されたとき、まだ確定状態が変化していなくても、**そのイベントが戦術判断に影響し得る場合は `tactical_state_version` を進める**。解決時にも進める。これをしないとOCR待ち中の旧応答が現在盤面のものとして誤表示される。参照コードに `mark_pending_event` / `resolve_pending_event` を用意した。

### 4.5 二重反映の防止・永続化

候補効果IDは `activation_event_id + rule_id + target_id + effect_index` の組み合わせ。能力変化テキストは同じ効果IDの**成立証拠**として統合し、能力ランクへ二重反映しない。

本実装のSQLiteには、少なくとも次の一意制約を必要とする。

```sql
CREATE UNIQUE INDEX IF NOT EXISTS ux_event_id ON battle_events(match_id,event_id);
CREATE UNIQUE INDEX IF NOT EXISTS ux_confirmed_effect
  ON confirmed_effects(match_id,effect_instance_id);
CREATE UNIQUE INDEX IF NOT EXISTS ux_turn_end ON turn_ends(match_id,turn_id);
CREATE UNIQUE INDEX IF NOT EXISTS ux_window_request
  ON api_calls(match_id,decision_window_id,tactical_state_version,allowed_action_digest);
```

SQLiteの追加更新は、イベントの追記・効果適用・状態更新を**1トランザクション**で行う。参照Pythonはインメモリ簡易版であり、このDBトランザクションは未実装。なお継続時間は `TURN_END_CONFIRMED` 時だけ減らし、途中交代/同じUIへの復帰では減らさない。

## 5. API送信直前のスナップショット確定（D01/D03/D07）

### 5.1 ウォーターマーク

- `capture_seq`: 取得フレームの単調連番。
- `observed_event_seq`: 観測されたイベントの単調連番。
- `processed_event_seq`: OCR/パーサ/リデューサが処理し終えた最後の連番。
- 入力窓が確認された時点で `fence_seq = observed_event_seq` を固定し、**`processed_event_seq >= fence_seq`** を基本の同期条件にする。
- 後続の新イベントも画面監視ワーカーが継続取得。重要差分は`state_version`を更新して旧ジョブを失効。

入力画面が現れた瞬間にAPI送信せず、関連イベント解析が追いつくまで**短い同期待機**を許す。ただし「いつまで待つか」はモデル・OCR・画面から測定する。対戦45秒/選出90秒/PREP40秒のLIVE時間規則を侵さないよう、局所同期の待機を期限予算の内側に収める。

### 5.2 Ready 判定

| 状態 | 条件 | API送信 |
|---|---|---|
| `WAIT_FOR_SYNC` | 入力窓・合法候補は確定、戦術に関係する直近イベントの解析待ち、残時間に余裕がある | まだ送らない |
| `READY_CONFIRMED` | 入力窓確認・合法候補完全・必須構築データ完備・処理待ちなし | 送る |
| `READY_WITH_UNCERTAINTY` | 重要な根拠は揃ったが未確定事実が残り、これ以上の待機が入力期限を危険にする | **不確実性を明示して送る**。合法候補は完全であることが条件 |
| `BLOCKED` | UI入力窓が不明、合法候補不完全、4ファイルの実読込未確認、選出3匹の4技欠落など実行に必要な根幹が欠ける | 推奨を表示しない。再認識/手動訂正を案内 |

`READY_WITH_UNCERTAINTY` は、未確定フィールドと根拠を **`battle_state.uncertainties`**, `snapshot_quality.pending_event_ids`, `material_unknowns` に入れること。推定を確定値に昇格させない。`BLOCKED` は**期限が迫っても解除しない**。Pythonによる代替戦術決定は禁止。

補足：現在の参照コードでは「処理待ちイベントの存在」と「期限切迫」を引数で受け取ってReady判定する。**待機時間の動的調整器・非同期ウォーターマークの実装は実機校正前であり未完成**。

### 5.3 送信→受領→表示の原子チェック

```text
[screen UI detects new independent choice]
  -> freeze decision_window_id
  -> wait (bounded) for OCR/effect reducer fence
  -> snapshot_quality assessment
  -> if BLOCKED: local UI reports incomplete data (no tactical fallback)
  -> if READY: freeze the immutable request payload and digest
  -> send to OpenAI (capture/OCR continues concurrently)
  -> receive JSON and validate schema
  -> re-read CURRENT match/window/state version/action digest
  -> validate IDs, legal actions, choice lock, mega, pivot target
  -> display only if matching and deadline still valid
  -> monitor screen until observed actual operation
```

旧回答のキャンセルは**利用/表示権の失効**を意味する。HTTP要求キャンセル可否や課金回避とは別。次の状態変化が来たら自動的に古い答えを採用しない。

新事実が **送信前** に入ったら送信内容へ反映。**応答待機中** に入ったら旧回答は破棄し、入力窓が残り期限内なら最新バージョンで再判断を依頼。**推奨表示後・操作確定前** に入ったら古い推奨表示を撤回して再評価。**実操作確定後** なら過去の決定を遡及変更せず、新しい戦闘事実として記録。

### 5.4 同じ交代UIの再利用

- 画像分類は `PARTY_MENU` 一つだけでよい。
- `NORMAL_COMMAND` で通常交代を提案し、ポケモン一覧へ進んだだけなら同じ入力窓を再利用。
- `PIVOT_TARGET` は実際の技成立で独立した選択窓が必要になった場合のみ。元OpenAIが予定した交代先が有効で、**予定の前提となる状態要素が変わっていなければ**新APIは不要。`previous_ai_plan` の根拠バージョンと依存事実の署名を比較する。
- ひんし後の投入先は新しいAI判断。その後の通常技選択もさらに新窓としてAI判断。
- 選択肢のないランダム強制交代に交代先選択APIを発火しない。

`PARTY_MENU`を見ただけで交代理由を作らず、`window_context` はローカル状態機械が割当てる。

## 6. 残り時間・PREPとの整合

- 選出3匹＋初手を先に回答。選出後40秒の必須PREPは初手6、控え12、勝ち筋3点のすべてを確認する。完成前に「事前評価完了」を出さない。
- PREPを待つために相手初手への行動推奨を遅らせない。相手初手が観測されたらPREP打切り。未完成を記録。
- 同期処理は戦闘中のWeb検索・ファイル再読込・重い詳細計算を呼ばない。
- API期限は入力期限とLIVEゲート上限の小さい方。安全余白・OCR安定時間は実測で調整。
- `NO_DECISION`/違法手/期限超過では人間向けのエラー表示のみ。Pythonが一手を補完しない。

## 7. API例と実装成果物

`Pokemon_Champions_API_example_request_v2_3.json` と `Pokemon_Champions_API_example_response_v2_3.json` は**架空のポケモンと技**によるコードテスト用の例。ゲーム固有の証拠として扱わない。サンプルのマスターハッシュは作成時点の実ローカルファイルから取得したが、サンプルの `frozen_at` は説明用の例示日時であり、マッチ開始時の実読込証拠ではない。

参照コード:

- `champions_contract_runtime_v2_3.py` — リクエスト/レスポンス/効果スキーマ検証、合法候補ハッシュ、追記証拠の再生、二重反映防止マーカー、送信可否判定、古い回答の破棄。
- `test_champions_contract_v2_3.py` — 状態訂正、選出、PREP、違法技、選択肢の指紋変化、未処理イベントによる旧応答失効、交代技計画など16件を単体試験。
- `build_champions_contract_v2_3.py` — 設計スキーマと892件台帳の再生成スクリプト。入力台帳を改変しない。

実行:

```bash
cd /mnt/data
python build_champions_contract_v2_3.py
python -m unittest -v test_champions_contract_v2_3.py
```

**実施結果 (2026-10-21)**: テスト16/16件成功。効果カタログ892/892件がJSON Schema検証を通過。これはデータ契約のテストであって、Champions実戦でのターン処理やOCR精度を保証するものではない。

## 8. 次工程に残す点（誤って完了扱いしない）

- `D06`: 人間の実選出が推奨と異なる場合の再PREP/選出変更不可時の制限。
- `D08`: レビューと次戦の並列実行・永続化。
- `D09`: 本番DBで過去送信要求を不変保存し、訂正イベントを再生する実装。今回のインメモリ参照コードは設計検証用。
- `D10–D12`: ファイル版固定の本番組込み、手動訂正操作、認証情報保護・費用。
- `D13`: PREPの不完全応答の再利用禁止/一部成果の扱いをアプリ画面へ反映。
- `V01–V08`: BlueStacks実映像のUI・本文・ターン終了・交代・詳細残ターン表示・規約確認。
- `M01–M05`: 認識速度、APIモデル/応答時間、費用、実戦受入基準。
- 効果データの `UNMAPPED` 880件の構造化と、Champions固有の根拠検証。

## 9. 出典・位置づけ

- 固定データ: 本プロジェクトのChampions技/特性/道具/選出マトリクス・LIVE判断ゲート・現行構築。
- OpenAI Responses API Structured Outputs: https://platform.openai.com/docs/guides/structured-outputs （`text.format`のJSON Schema利用可。ただし対応するSchema機能はモデルとAPIで確認）
- P0統合元: `Pokemon_Champions_P0_Implementation_Spec_v2_1.md`。

**要約**：wire契約はv2.3.0、効果マスターはv0.2.0に統一。過去証拠は追記・再生、送信済み入力は不変。API送信前にスナップショットの品質を判定し、古い回答/違法候補は表示しない。**勝負の一手を決めるのはOpenAIだけ。**
