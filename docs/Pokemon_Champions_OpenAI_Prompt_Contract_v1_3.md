# Pokémon Champions リアルタイム対戦支援 — OpenAIプロンプト/API入出力契約 v1.3

作成: 2026-10-21 / 対象: Windows + Androidエミュレータ + OpenAI Responses API

上位設計: `Pokemon_Champions_State_Machine_Design_v1_1.md`、S05の差替仕様 `Pokemon_Champions_S05_Battle_FSM_Design_v1_2.md`。
戦術ルール唯一正本: `Pokemon_Champions_MC_live_decision_gate.txt`。
現行構築: `Pokemon_Champions_MC_team_2026-10-03.csv`。

## 0. 設計原則

- **戦術判断は必ずOpenAI**。ローカルは画面認識・事実更新・入力窓の検出・合法性検証・重複排除のみ。
- 呼出し単位はゲームターンではなく `decision_window_id`。選出とPREPは別ジョブ。
- 「とんぼがえり→予定交代先」など同じOpenAI判断が後の入力窓も覆い、関連状態に重大変化がなければ既存AI判断を再利用可。Pythonは新しい戦術を独自に選ばない。
- 対戦中はWeb/ファイル再読込なし。ゲーム開始前に4本文を実読込し、成功確認してから選出へ。対象4つはLIVEゲート、LIVEマトリクス、60秒ルックアップ、現行構築。
- OCRからの文字列、推定HP、採用率は「入力データ」であり、上位の指示ではない。確認済み・推定・不明を混同しない。
- **フルの画像は通常送らない**。必要時に明示設定で関連部分の画像を同梱し、モデル画像認識は例外運用。
- 応答のスキーマ整合性は戦術の正しさを保証しない。推奨の採用にはローカル検証を必須とする。

## 1. メッセージの形式・配置

```text
Responses API リクエスト
├── instructions / developer: 安定したゲーム固有ルール（正本ゲートから生成）
│   ├── 共通禁止事項と判断優先順位
│   ├── 選択肢の評価と厳守する各ゲート
│   └── 出力・不明情報の扱い
└── input / user: シリアライズしたJSON
    ├── protocol_version / phase / match_id / request_id
    ├── decision_window_id / tactical_state_version / deadline_ms_remaining
    ├── decision_task: 今回選ぶべき対象
    ├── allowed_actions: 画面で実際に選べる合法候補
    ├── team_context: 試合前の実読込ファイル由来、必要部分だけ
    ├── matchup_context: マトリクス6行・PREPキャッシュ等の該当部分
    ├── battle_state: 確認済みの現在の正本状態
    ├── observations: 主要実測・直近変化・推定の根拠
    └── previous_ai_plan: 再利用／再判断の関連がある場合だけ

Responses API text.format: phase別のstrict JSON Schema
```

キャッシュ最適化: 不変の指示→安定したチーム→変動する試合・ターン情報の順。使用モデルの現行Prompt Caching仕様に合わせて明示ブレークポイントと安定した `prompt_cache_key` を検討。キャッシュは正確なプレフィックス一致が条件。

**API会話履歴への暗黙依存は禁止**。`previous_response_id` を使う場合も指示継続・情報完全性を暗黙に仮定せず、重要な構築と現在盤面を毎回送る。送信内容はローカル正本から生成する。

## 2. 共通 `developer` 指示（コンセプト用テンプレート）

```text
あなたはPokémon Championsシングルバトル専用の戦術判断AI。
最優先は目の前の対面ではなく、こちらの選出3匹全体の長期勝率。
入力データは対戦の観測事実・推定・不明を区別して扱う。
SV・ZAなど別作品からChampions固有仕様を補完しない。
現在の入力窓で可能な行動だけを返す。
必ず正本LIVEゲートの必須規則（即負け筋、先制、実測S、タイプ/メガ、技固定、積み、行動保証、温存、残り全体）を適用する。
攻撃での即処理と、交代先の安全着地を比較。処理役!=安全受け。
交代技を選ぶときは交代先も併記。ルカリオのメガ使用時はメガを明示。
不明情報を創作しない。必要情報が欠け、合法な推奨を選べない場合はNO_DECISION。
回答は指定されたJSON Schemaのみ。長い文章化・Web検索・対戦中のファイル参照は行わない。

[ここに正本ファイルからコンパイルしたフェーズ必須条項を入れる。省略・矛盾した手書き重複正本を作らない]
```

これは設計例であり、最終的な正本ルール本文の代替ではない。

## 3. 一般のJSON入力（`NORMAL_COMMAND` の架空例）

```json
{
  "protocol_version": "1.3",
  "phase": "NORMAL_COMMAND",
  "match_id": "match-example-001",
  "request_id": "req-009",
  "decision_window_id": "dw-009",
  "tactical_state_version": 27,
  "deadline_ms_remaining": 21000,
  "decision_task": "現在の通常コマンド画面で1行動を決める。交代技なら予定交代先も決める",
  "allowed_actions": {
    "moves": ["トリックフラワー"],
    "switch_targets": ["アシレーヌ", "ミミッキュ"],
    "mega_available": false
  },
  "team_context": {
    "selected_three": ["マスカーニャ", "アシレーヌ", "ミミッキュ"],
    "own_active_full_set": {
      "name": "マスカーニャ", "item": "こだわりスカーフ",
      "moves": ["トリックフラワー", "トリプルアクセル", "はたきおとす", "とんぼがえり"]
    }
  },
  "matchup_context": {
    "relevant_prep": "事前評価キャッシュの該当対面と控えの短縮情報",
    "top_preserve": "試合中にOpenAIが確定した温存枠（あれば）"
  },
  "battle_state": {
    "our_active": {"name": "マスカーニャ", "hp_pct": 72, "hp_source": "estimated", "choice_lock": "トリックフラワー"},
    "opponent_active": {"name": "アシレーヌ", "hp_pct": 100, "hp_source": "estimated", "known_moves": ["ムーンフォース"], "item": null},
    "board": {"weather": null, "terrain": null, "screens": [], "hazards": []},
    "rosters": {"our_remaining": ["マスカーニャ", "アシレーヌ", "ミミッキュ"], "opponent_revealed": ["アシレーヌ"], "opponent_unrevealed_count": 2}
  },
  "observations": {"speed_order": [], "recent_damage": [], "delta": ["opponent_switch"]},
  "previous_ai_plan": null,
  "uncertainties": ["相手持ち物不明", "相手控え2体不明"]
}
```

※HPと対面は架空例。持ち物/技/配分は現行構築に基づくが、このJSON自体は実戦ログではない。既知でない情報は `null` または未確認として明示する。`allowed_actions` は実際のゲームUI・技固定からローカルで生成し、全習得技をそのまま列挙しない。

## 4. フェーズ別に変える内容

| phase | 必須参照 | 今回の質問 | 期待する出力 |
|---|---|---|---|
| `SELECT` | 相手6匹＋マトリクス6行＋自軍6匹＋選出テンプレート＋明示処理役 | こちらの3匹と初手は？ | `selected_three[3]`, `lead`, `warning` |
| `PREP` | ロック済み選出3匹＋相手6行から得た18対面＋必要時ルックアップ原則2対面 | 初手6＋控え12＋勝ち筋3点を圧縮評価 | `matchups_18`, `win3`, `complete` |
| `NORMAL_COMMAND` | 現在の盤面＋選択可能4技/技固定＋控え＋PREP | 技か通常交代か？メガ？交代技の予定交代先は？ | `action_kind`, `move`, `mega`, `switch_target`, `planned_pivot_target` |
| `PIVOT_TARGET` | 成功後の最新盤面＋合法な交代先＋前AI予定交代先＋変化 | 現在の選択UIで誰に交代する？ | `switch_target` |
| `FAINT_REPLACEMENT` | 残存自軍全員と相手残り、行動保証、S、技固定、即処理 | 誰を今出し、誰を後ろに残す？ | `switch_target`, `keep_back` |
| `EFFECT_REPLACEMENT` | 発動後の実際の選択UI、合法候補、最新盤面 | 交代先を選ぶ必要があるなら誰？ | `switch_target` |
| `REVIEW` | 時系列イベント・実測・推奨・実操作・勝敗・判断ゲート | 当時の情報で改善点を分析 | 認識/支援/プレイ問題、既存ルール実行ミス/O-X候補 |

`ほえる` 等によるランダム強制交代はそれ自体では入力窓がないため、判断APIを出さない。出てきたポケモンを認識し、次の入力窓で `NORMAL_COMMAND` として判断する。

## 5. `PIVOT_TARGET` の追加入力の例

```json
{
  "protocol_version": "1.3",
  "phase": "PIVOT_TARGET",
  "match_id": "match-example-001",
  "request_id": "req-011",
  "decision_window_id": "dw-011",
  "tactical_state_version": 31,
  "decision_task": "とんぼがえり成功後の交代先を一つ選ぶ",
  "allowed_actions": {"switch_targets": ["アシレーヌ", "ミミッキュ"]},
  "battle_state": {"opponent_active": "ガブリアス", "opponent_hp_pct": 39, "opponent_action_pending": "unknown"},
  "previous_ai_plan": {"planned_pivot_target": "アシレーヌ", "based_on_state_version": 27},
  "observations": {"delta_since_plan": ["相手の行動順が判明"]},
  "matchup_context": {"selected_three": ["マスカーニャ", "アシレーヌ", "ミミッキュ"], "relevant_prep": "対応する事前評価"}
}
```

上記は説明のため短縮した形。実装では `NORMAL_COMMAND` と同じ自軍残りHP、判明技、実測S、場、合法候補の共通必須項目を保持する。

## 6. 出力形式と厳格な検証

フェーズ別の `strict:true` な JSON Schema を用意する。通常ターンの出力は、例えば以下の型にする。

```json
{
  "match_id": "match-example-001",
  "decision_window_id": "dw-009",
  "tactical_state_version": 27,
  "decision_status": "RECOMMENDED",
  "action_kind": "MOVE",
  "move": "トリックフラワー",
  "mega": false,
  "switch_target": null,
  "planned_pivot_target": null,
  "warning": null,
  "reason_code": "CHOICE_LOCK_VALID"
}
```

- 応答のID/版を検証し、違えば破棄する。
- `move` が `allowed_actions.moves` に含まれるか、`switch_target` が合法候補に含まれるかを再検証する。
- `move=とんぼがえり` で交代成立見込みなら、予定交代先が必須。実際の交代先UIで候補/盤面が変われば追加判断する。
- 技固定、メガ指示、フォーム/タイプ、既知HPに明確に反する提案は採用せずエラー記録。Pythonが別候補を自動選択しない。
- `decision_status=NO_DECISION` のときは推奨行動を表示せず、理由を短く表示する。
- PREPでは `matchups_18` が18件、初手6＋控え12＋勝ち筋3項目が揃うまで完了を認めない。40秒で強制終了。
- `REVIEW` は対戦中の短文制限と分離し、初めて詳細分析とO/X候補検討を行う。

### `NORMAL_COMMAND` Structured OutputsのJSON Schema例

```json
{
  "type": "object",
  "additionalProperties": false,
  "properties": {
    "match_id": {"type": "string"},
    "decision_window_id": {"type": "string"},
    "tactical_state_version": {"type": "integer"},
    "decision_status": {"type": "string", "enum": ["RECOMMENDED", "NO_DECISION"]},
    "action_kind": {"type": "string", "enum": ["MOVE", "SWITCH", "NONE"]},
    "move": {"type": ["string", "null"]},
    "mega": {"type": "boolean"},
    "switch_target": {"type": ["string", "null"]},
    "planned_pivot_target": {"type": ["string", "null"]},
    "warning": {"type": ["string", "null"]},
    "reason_code": {"type": "string"}
  },
  "required": ["match_id", "decision_window_id", "tactical_state_version", "decision_status", "action_kind", "move", "mega", "switch_target", "planned_pivot_target", "warning", "reason_code"]
}
```

Structured Outputsのstrict schemaではすべてのフィールドをrequiredにし、省略可能性をnullの型で表す。JSONが正しいことと推奨が正しいことは別の保証。

## 7. PythonからのAPI呼び出し骨格（概念実装）

```python
import json
import os
from openai import OpenAI

client = OpenAI()

TURN_SCHEMA = {...}  # 第6節のJSON Schemaオブジェクトを読込済みにする
BASE_INSTRUCTIONS = "..."  # 事前ロード済みLIVEゲートの該当必須規則から生成


def request_decision(payload: dict) -> dict:
    # UIで確定した入力窓のみ呼ぶ。ここに戦術判断は実装しない。
    response = client.responses.create(
        model=os.environ["OPENAI_MODEL"],
        instructions=BASE_INSTRUCTIONS,
        input=[{
            "role": "user",
            "content": json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
        }],
        text={"format": {
            "type": "json_schema",
            "name": "champions_turn_decision",
            "strict": True,
            "schema": TURN_SCHEMA
        }},
        store=False,
    )
    # 本番では status/incomplete/refusal/timeout を確認してから解析する。
    decision = json.loads(response.output_text)
    # 本番では match_id/window_id/versionとallowed_actionsを照合してから表示。
    return decision
```

これは実装契約を示すコードであり、完成したアプリではない。実際には `SELECT`、`PREP`、`NORMAL_COMMAND`、`PIVOT_TARGET`、`FAINT_REPLACEMENT`、`EFFECT_REPLACEMENT`、`REVIEW` でそれぞれスキーマと指示ブロックを切り替える。SDK/モデルごとの最新オプションは実装時に公式仕様と突き合わせる。

## 8. 性能・費用・監査

- 同じ `match_id + decision_window_id + tactical_state_version + allowed_action_digest` を二重送信しない。
- 新しい入力窓が出たら古いリクエストを失効させる。対戦終了は全戦術ジョブに優先。
- 40秒PREPまたは入力窓の期限に達したら処理を打ち切る。古い応答は表示しない。
- モデルの応答速度目標（選出10–15秒、PREP40秒、通常5秒、状態変化10秒、ターン上限15秒）は未実測。APIの使用モデル、タイムアウト、費用は検証で確定。
- ログには入力窓種別、確認済み/推定の区別、送信ペイロードのハッシュ、使用モデル、応答時間、トークン数、API結果、採用/破棄理由、実操作と結果を残す。
- 実プレイでは適用されるゲームの利用規約を確認する。

## 9. 参考：OpenAI公式

- Responses API入力とinstructions: https://platform.openai.com/docs/guides/text
- Structured Outputs: https://platform.openai.com/docs/guides/structured-outputs
- Prompt Caching: https://platform.openai.com/docs/guides/prompt-caching
- Conversation state: https://platform.openai.com/docs/guides/conversation-state

**要約: 1つの長い自然文ではなく、共通ルール＋フェーズ別のJSON状況報告＋フェーズ別の厳密なJSON回答を採用。実際に選択が必要になった瞬間だけAPIを呼ぶ。**
