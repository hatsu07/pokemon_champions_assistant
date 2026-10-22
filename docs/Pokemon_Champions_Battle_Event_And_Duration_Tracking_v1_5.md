# Pokémon Champions 対戦イベント認識・継続ターン追跡仕様 v1.5

更新日: 2026-10-21
適用: Windows / Androidエミュレータ / Pythonローカル画像認識 / OpenAI API戦術判断
関連: S05詳細FSM v1.2、UI検出仕様 v1.4、プロンプト契約 v1.3、現行LIVE判断ゲート

## 1. 方針

- OpenAI APIは、選出・PREP・技選択・交代先などの戦術判断を行う。状態認識・計時・事実の記録で原則呼ばない。
- 対戦テキスト（画面下部）を**イベント発生検出**の主情報源にする。
- HPバー、状態異常アイコン、現在フォーム、天候/フィールド/壁の詳細表示を**現在状態の検証**に使う。
- 画像の見た目によるUI分類と、直前イベント・選択コンテキストは分離する。
- すべての読み取りに観測元、観測時刻、確度、ゲーム内ターン、マッチIDを付ける。確定・推定・不明を区別する。
- Champions固定マスターを優先し、他作品の知識から技効果・持続を自動補完しない。

## 2. 認識処理パイプライン

1. 画面を低負荷で継続キャプチャ（例: 8～12fps、実機で調整）。直近10～20秒のリングバッファを保持。
2. 画面下部テキスト領域の変化を検出。安定した新しいメッセージだけOCRする。全画面OCRを毎フレーム行わない。
3. Champions専用の技名・特性名・持ち物名・状態文言辞書で正規化する。メッセージが連続する場合、同一内容でも別イベントとして発生し得るため、画面変化エピソード単位でIDを付与する。
4. イベント正規化（actor/target/side/action/effect/amount/confidence）。対象の識別が曖昧なら未確定に保つ。
5. イベントリデューサでローカル状態更新。HPや盤面表示など他のセンサーと照合する。
6. 次の入力機会だけOpenAIへ最新の確定/推定/不明情報をまとめて渡す。

## 3. イベント分類

- MOVE_USED / MOVE_RESOLVED / MOVE_FAILED / MOVE_MISSED
- STAT_STAGE_CHANGED: stat, stage_delta または unknown
- STATUS_APPLIED / STATUS_CLEARED
- HAZARD_SET / HAZARD_REMOVED / HAZARD_LAYER_CHANGED
- WEATHER_SET / WEATHER_ENDED / WEATHER_OVERRIDDEN
- TERRAIN_SET / TERRAIN_ENDED / TERRAIN_OVERRIDDEN
- SCREEN_SET / SCREEN_ENDED / SCREEN_BROKEN / SCREEN_SIDE_CHANGED
- MEGA_EVOLVED / FORM_CHANGED / TYPE_CHANGED
- ABILITY_ACTIVATED / ITEM_ACTIVATED / ITEM_CONSUMED / ITEM_REVEALED
- SWITCH_OCCURRED / ACTIVE_CHANGED / FAINTED
- TURN_END_CONFIRMED / DECISION_WINDOW_OPENED / MATCH_FINISHED

ステータスの変化量、天候・壁・フィールドの継続ターンは事前に検証したChampionsルール辞書に依存する。元の固定技マスターCSVには継続ターンやすべての追加効果の数値列がないため、別の**Champions効果/継続マスター**を追加作成して保持する。

## 4. 状態モデル

```json
{
  "match_id":"match_001",
  "turn_id":8,
  "phase":"ANIMATION",
  "weather":{
    "kind":"sun","started_turn":7,
    "duration_candidates":[5,8],"remaining_range":[3,6],
    "source":"opponent_ability","confidence":"inferred"
  },
  "terrain":null,
  "screens":{
    "ours":{"reflect":null,"light_screen":null,"aurora_veil":null},
    "opponent":{"reflect":null,"light_screen":{"started_turn":8,"duration_candidates":[5,8]},"aurora_veil":null}
  },
  "hazards":{"ours":{"stealth_rock":true},"opponent":{"stealth_rock":false}},
  "active_pokemon":{"ours":{"status":"poison","stages":{"spa":-1}},"opponent":{"form":"mega","type_confirmed":false}},
  "last_events":[]
}
```

## 5. 継続ターンの管理

Championsについて外部専用資料で確認した基本値:
- 天候: 原則5ターン、対応する延長いわにより8ターン。
- フィールド: 原則5ターン、グランドコートにより8ターン。
- ひかりのかべ/リフレクター/オーロラベール: 原則5ターン、ひかりのねんどにより8ターン。
- ステルスロック等の設置技: 上記の5/8ターンタイマーを使わず、場から除去された観測イベントで解除。

**実装上の重要事項:** 外部のChampions専用解説では、相手側の延長道具が未判明の場合、ゲーム上の残りターン表示が初めは通常の5ターン表記となり、5ターンを超えて実際に継続した際に8ターン側の表示へ移行する例が報告されている。よって「画面が4/5と表示された」ことだけでは、延長道具不所持を確定しない。

- `started_turn` を技/特性で効果が実際に成立したターンとして記録する。
- 効果発動したターンも持続ターンに含め、ターン終了が確定した時のみ1回減算する。
- `TURN_END_CONFIRMED` は同じターンに1回だけ。とんぼがえり後の交代先選択、ひんし後の死に出し、テキスト表示完了ではカウントを進めない。
- 天候・フィールドが別種に書き換わったら、前の状態は消去して新イベントから起算。壁は陣営ごと・種類ごとに管理。解除技や効果消滅も観測する。
- 詳細情報表示で読める残りターンは**現在状態の直接観測**として優先するが、表示が隠している延長候補は直ちに棄却しない。
- 画面上の明示的な終了メッセージ・消失確認・解除メッセージがあれば予測計算より優先する。

## 6. ターン進行の例

相手が第7ターンにひかりのかべを成立させ、持ち物不明:
- 第7ターン発動直後: 壁有効。duration_candidates=[5,8]
- 第7ターン終了: 通常なら残4、延長ありなら残7。相手側の表示が4/5でも持ち物不明のまま。
- 第8ターン終了: 通常なら残3、延長ありなら残6。
- 通常5ターンで切れるはずの時点を超えて維持されたら8ターン型を確定方向に更新。
- 技による破壊が観測されたら残りターンに関わらず即解除。

## 7. 異常・品質条件

- OCR読み飛ばし: 最新状態に飛躍があれば直近フレームバッファを再照合。誤推定した継続ターンを確定情報へ昇格させない。
- 重複OCR: 同じ表示の連続フレームから同一イベントを重複適用しない。ただし同じ文言が別行動で再発した場合は独立イベントとして処理する。
- ターン境界不明: 残りターンは数値確定しない。`turn_boundary_confidence=uncertain` とし、現在盤面の明示表示を優先。
- 特性/持ち物判明: 自軍は構築データ、相手は観測済み/未確認を分ける。統計的採用率で確定しない。
- 戦術影響あり: 不確定な残ターン範囲や能力ランクはOpenAIの判断JSONへそのまま渡す。OCRを理由にAPI戦術判断を省略しない。

## 8. 受入テスト

- 特攻低下イベント1回でランクが一度だけ変化し、異なる技の再低下は別イベントとして計数する。
- とんぼがえり→交代先選択の間に天候カウントを減らさない。
- ひんし→死に出し→次の入力画面でもターンの二重カウントを行わない。
- 相手の壁が4/5と表示されても8ターン候補を維持する。
- 「アイススピナー」等でフィールド消失を観測すればタイマーを終了する。
- 交代後、能力ランクを入れ替わった個体に持ち越さず、壁・設置技は陣営の状態として残す。
- 天候やフィールドの書き換え時に前のタイマーを破棄する。
- APIへ現在の確定・推定・不明を分けて送れる。

## 9. 確認した参照資料（2026-10-21）

- 既存ローカル正本: Pokemon_Champions_MC_move_master.csv / Pokemon_Champions_MC_ability_master.csv / Pokemon_Champions_MC_usable_held_items.csv / Pokemon_Champions_MC_live_decision_gate.txt
- https://gamewith.jp/pokemon-champions/561710 （フィールド持続、詳細情報表示）
- https://gamewith.jp/pokemon-champions/561577 （天候持続、詳細情報表示）
- https://game8.jp/pokemon-champions/793020 （ひかりのねんど延長）
- https://champions.pokewiki.net/場の状態 （天候・フィールド効果）
- https://champions.pokewiki.net/もちもの考察 （相手側の延長アイテム隠蔽表示に関する報告）

全ての効果が正確に画面下部でどう表記されるかは、BlueStacks録画による文字列辞書の採取・検証が必要。
