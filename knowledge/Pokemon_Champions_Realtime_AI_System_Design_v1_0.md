# Pokémon Champions リアルタイムAI対戦支援システム 設計書 v1.0

- 作成日：2026-10-21
- 対象：Pokémon Champions、シングル・ランクマッチ、Windows PC／Androidエミュレータ
- 状態：実装前の設計案。対応環境、ゲーム画面レイアウト、計測値、利用規約適合性は未検証
- 最重要原則：**選出・PREP・ターンごとの戦術判断は必ずOpenAI API**。認識・状態管理・データ取得・出力検証は原則ローカル。

## 1. 目的・成功条件

対戦中の手入力を減らし、情報忘れ・技固定違反・タイプ誤認・PREP遅延などの支援ミスを減らす。目先の一匹より選出3匹全体の勝ち筋を優先し、長期勝率と意思決定の再現性を向上させる。

「Pythonが合理的と判断したからAPIをスキップする」という戦術的ショートカットは**禁止**。OpenAIが判断できないときは「判断未確定」と表示し、ローカルAIやルールベースの戦術を代替指示として表示しない。

## 2. システム構成

```text
BlueStacks 5 (人間がゲームを操作)
   │ Windows Graphics Capture / ADB screencap
   ▼
Capture Service ─→ 直近フレームのリングバッファ
   │
   ▼
Local Perception ─→ 画面種別検出 / テンプレート照合 / OCR / HPバー推定
   │                                      │
   │                        信頼度不足 → 要確認表示・手動補正
   ▼
Event Reconciler ─→ 重複排除 / 時系列並べ替え / 変化確定
   │
   ▼
State Store (SQLite + memory, canonical state, versioned)
   │
   ├── Static Data Loader / Matrix Parser / Rule Pack Compiler
   │
   ▼
Decision Scheduler ─→ 状況が「判断必要」になったときだけAPI呼出
   │
   ▼
OpenAI Responses API ─→ 選出 / 18対面PREP / 各ターン行動判断
   │
   ▼
Output Validator ─→ 技使用可否・技固定・状態鮮度・フォーマットを確認
   │
   ▼
PySide6 UI ─→ 推奨行動（人間が実行）
   │
   ▼
Log / Metrics ─→ SQLite / JSONL / 既存対戦ログへ後で転記
```

**責務分担**

| サブシステム | 実施すること | 戦術判断をするか | API消費 |
|---|---|---|---|
| Capture | Windows/ADBによるフレーム取得 | いいえ | 0 |
| Perception | ポケモン名・技・HP・メッセージ・画面種別認識 | いいえ | 原則0 |
| Event Reconciler | 画面から観測できた事実と未確認事項を整合 | いいえ | 0 |
| State Store | 状態/履歴/不明/推定を保持 | いいえ | 0 |
| Data Loader | Champions専用ファイル読込・名前正規化・6行抽出 | いいえ | 0 |
| OpenAI | 3匹選出・初手・PREP・各ターンの判断 | **はい、唯一の主体** | 必須 |
| Validator | 操作可能性、データ鮮度、技固定、明示的ルール違反を検出 | 代替戦術は選ばない | 0 |
| UI / Logs | 行動表示・手動訂正・記録 | いいえ | 0 |

## 3. 採用技術

- ゲーム：BlueStacks 5（利用・互換性・規約適合性は利用前に実機確認）。
- 画面取得：最初はADB `exec-out screencap -p`、性能不足ならWindows Graphics Capture相当へ切替。キャプチャ方式は差し替え可能なインターフェース化。
- 画面処理：Python、OpenCV、NumPy、OCR（PaddleOCR等は候補であり事前検証）。ポケモンの名前・アイコン・画面上のOCR領域を固定解像度ごとに校正。
- 保存：SQLite（canonical state, events, decisions, quality logs）、JSONL（再現可能な生イベント）、重要場面のスクリーンショット。
- API：OpenAI Responses API、JSON SchemaによるStructured Outputs。利用モデルは品質・速度実測後に選定。モデルIDを設定ファイルから変更可能にする。
- GUI：PySide6。BlueStacksとは別ウィンドウに表示。ゲーム操作の自動化は初期スコープに入れない。
- 設定・秘密情報：`.env`はローカルのみ、APIキーはWindows Credential Managerまたは環境変数へ。ログに秘匿値を残さない。

## 4. 固定データと正本

現行データの正本は以下。

| ファイル | 役割 |
|---|---|
| `Pokemon_Champions_MC_live_decision_gate.txt` | LIVE運用・時間・判断ゲートの**唯一正本** |
| `Pokemon_Champions_MC_live_selection_matrix.txt` | 選出・18対面の基礎評価（358行） |
| `Pokemon_Champions_MC_60sec_lookup_master.txt` | 矛盾・先制・積みなどの補完（358行） |
| `Pokemon_Champions_MC_team_2026-10-03.csv` | 現行自軍構築（6匹・技・持ち物等） |

必要時だけ別途使う詳細データ：使用可能ポケモン、技マスター、習得技、特性、持ち物、詳細支援マスター。対戦中の再読込は禁止。動的使用率は対戦開始前に更新する運用とし、マッチング後は外部取得をしない。SV・ZA等の別作品による欠損補完はしない。

### 4.1 事前ロードのハードゲート

`ランクマ開始`イベントを受け取った試合ごとに、必須4ファイルの**本文を実読込**し、ファイル名やハッシュの存在確認だけで成功扱いにしない。4/4成功を確認した場合のみ READY に進める。マトリクスとルックアップのキー・358行の対応検査を行う。不整合なら選出開始不可とし詳細を記録。

ロード時はローカルでパース・索引作成するが、全相手358×自軍6の事前展開はしない。ファイルの更新は対戦が終わるまで反映しない。

### 4.2 LIVE判断ルールの提供

APIの各フェーズへルールの該当部分を渡す。選出では `SELECTION`、PREPでは `PREP_40S`、各ターンでは `LIVE_FORCE_ORDER` と `ATTACK_GATE / SWITCH_GATE / PRIORITY_GATE / GUARANTEE_GATE` 等の全必須規則を保持。圧縮する場合は、既存ルールが欠落・逆転しないことをリグレッションテストで検証する。Pythonは合法性・安全ゲート違反を検出して却下できるが、代替技を戦術的に選ばない。

## 5. 画面認識の設計

### 5.1 画面種別

`HOME / QUEUE / TEAM_SELECTION / SELECTION_CONFIRMED / BATTLE_ANIMATION / TURN_INPUT / SWITCH_INPUT / MATCH_END / UNKNOWN` に分類。画面遷移は複数フレームの安定性とUI要素の組合せで確定し、アニメーション中の一瞬のテキストを取り逃さないようリングバッファに保持する。

### 5.2 認識対象

- 選出画面：相手6匹の名称/アイコン/フォーム、味方選出確定の有無。
- 対戦画面：場の両ポケモン、HPバー（未表示なら推定）、状態異常、能力ランク、天候/フィールド/壁/ステロ等、技選択欄、交代欄。
- 戦闘テキスト：使用技、交代、特性発動、持ち物消費、状態変化、能力変化、メガ/フォーム/タイプ変化、ひんし。
- 戦闘履歴：自軍残りと相手判明選出、実測行動順、実測被/与ダメージ、こだわり固定、ばけのかわ/ふうせん等の状態。

認識結果ごとに `observed|inferred|unknown`、`confidence`、`frame_id`、`observed_at`、`source_region` を持つ。採用率による型推測は観測済み技に昇格させない。HPバー由来の値は`estimated`とし、ユーザー手動修正で上書きできるようにする。

### 5.3 ローカル処理と画像AI例外

通常は画像をOpenAIへ送らない。ポケモンアイコンはテンプレート照合、テキストはローカルOCR、HPは画像処理で取得。認識が重要事項で不確かな場合は、画面内UIから手動訂正を促す。画像をOpenAIへ送るのは**明示的な例外設定**として、通常の判断リクエストに必要最小限の切り抜きを同梱できる設計に留める。認識専用APIの常時呼び出しはしない。

## 6. 対戦状態のデータモデル

```json
{
  "match_id": "local-uuid",
  "state_version": 17,
  "phase": "TURN_INPUT",
  "turn": 4,
  "selection": {
    "opponent_six": ["..."],
    "our_three": ["..."],
    "our_lead": "...",
    "opponent_revealed": ["..."]
  },
  "our_active": {
    "name": "...", "form": "...", "type_now": ["..."],
    "hp_pct": 72.0, "hp_estimated": true,
    "status": null, "stages": {}, "choice_lock": null
  },
  "opponent_active": {
    "name": "...", "form": "...", "type_now": ["..."],
    "hp_pct": 31.0, "hp_estimated": true,
    "known_moves": ["..."], "known_item": null, "known_ability": null
  },
  "board": {"weather": null, "terrain": null, "hazards": [], "screens": []},
  "observations": {"speed_order": [], "damage_history": []},
  "uncertainties": ["opponent item unknown"]
}
```

正本はSQLiteとメモリ内の同一状態。画面上の観測・履歴・前回APIの推奨を混同せず、**実際に行われた行動は画面結果を観測してから確定**する。APIが推奨しただけではHPや技使用履歴を更新しない。

## 7. イベント駆動状態遷移とAPI呼出

```text
WAITING
  └─ start_requested ─→ PRELOAD_4_FILES ──(4/4)──→ READY
READY
  └─ opponent_six_confirmed ─→ SELECT_API_REQUEST
SELECT_API_REQUEST
  └─ OpenAI selection valid ─→ DISPLAY_SELECTION / PREP_API_REQUEST
PREP_API_REQUEST
  ├─ OpenAI returns 18+WIN3 within 40s ─→ PREP_READY
  ├─ 40s timeout ─→ PREP_UNFINISHED
  └─ opponent lead appeared ─→ CANCEL_PREP / TURN_INPUT
TURN_INPUT
  └─ stable input state ─→ TURN_API_REQUEST ─→ VALIDATE ─→ DISPLAY_ACTION
DISPLAY_ACTION
  └─ actual action/animations observed ─→ UPDATE_STATE ─→ TURN_INPUT
(any phase)
  └─ match ended ─→ LOG_AND_CLOSE
```

**APIトリガーは原則3種類だけ。**

1. `opponent_six_confirmed`：選出3匹＋初手のOpenAI判断。相手6行はローカルから一括取得する。
2. `selection_locked`：OpenAIの18対面PREP（初手6、控え12、勝ち筋3点）。選出後すぐ開始する。
3. `turn_input_ready`：今の状態についてOpenAIに技/交代/メガを判断させる。死に出し入力もこのイベントに含む。

画面キャプチャ、OCR、アニメーション中のHP更新、技テキストの記録、単なる画面切替ではAPIを呼ばない。新しい入力可能状態が検出されても、同じ`state_version`の重複送信は防止する。ターンの途中で重大な新観測が判明した場合は次の有効状態に更新し、旧回答を破棄してOpenAI判断を更新する（時間・費用としては例外的な再呼出）。

## 8. 選出・PREPアルゴリズムの要求

### 8.1 選出API

ローカルはユーザー指定の6匹を正規化し、マトリクスの該当6行を一括取得してOpenAIに渡す。候補テンプレートA/B/C/Dと処理役の明示有無を含める。OpenAIが相手有力選出3匹・最大の勝ち筋・安全受けと処理役の区別・重要警戒を考慮して選出と初手を決める。候補の形式的作成はPythonでもよいが、**最終選出は必ずOpenAI**。

目標応答10〜15秒、既存LIVEゲートでのハード上限20秒。選出回答後は選出変更を提案しない。

### 8.2 PREP API

選出3匹×相手6匹＝18対面を、キャッシュ済み6行から展開。初手6対面は第一行動・即処理・攻撃対面操作・S・先制・双方最大打点・受け出し・技固定後・メガ後まで評価。控え12対面もS・打点・積み・安全受け・処理/温存を圧縮評価。さらに `top_preserve / stopper_vs_setup_fast / endgame_closer` を生成。

PREP時の60秒ルックアップ参照は**原則2対面まで**、不足・矛盾・先制・積み・重大警戒に限定。40秒の絶対期限または相手初手出現で必ず終了。`18対面全件+勝ち筋3点`の妥当性を検証して初めて完了扱い。未完成は支援ミス `PREP_UNFINISHED` として記録する。初手出現時は未完成でも行動判断APIを優先。

### 8.3 ターンAPI

現対面の最新状態と保持済みPREP、技・タイプ・持ち物・実測結果・控えを送り、**必ずOpenAIに推奨行動を選ばせる**。判断順は「即負け筋→先制→実測S→現在タイプ→技固定→積み→行動保証→温存→残り3匹の勝ち筋」。技候補4つを現タイプで評価し、交代候補は着地時の想定最大打点と安全性を区別。こだわり固定、メガ明示、交代技の交代先同時指定を保持。

目標は既存ゲートに従い通常5秒、状態変化時10秒、ハード上限15秒。これは未実測の目標値。45秒のゲーム操作制限を使い切らない。

## 9. OpenAI API契約

Responses APIを利用。入力はフェーズに応じて次の5区分を固定順で構成：

1. **静的ルール**：必要なLIVE判断ゲートの規則と禁止事項（安易な要約で欠落させない）。
2. **自軍定義**：現行構築6匹、選出後は実選出3匹を中心にする。
3. **マトリクス・PREP要約**：選出時は相手6行、対戦中は選出後の必要な対面キャッシュ。
4. **最新確定状態**：HP、状態異常、メガ、技固定、行動順、技/持ち物/特性、盤面、残り3匹。
5. **今回のイベント**：選出、PREP、ターン入力、死に出しなど。

APIはチャットUIのプロジェクトファイルを自動参照しないため、Python側から必要な内容を送る。OpenAI会話状態への暗黙依存を避け、重要な判明情報を毎回の`canonical state`で明示する。静的プレフィックスを固定してプロンプトキャッシュを利用するが、効果は実測で評価する。

Structured Outputsの主要フィールド例：

```json
{
  "match_id": "local-uuid",
  "request_id": "req-uuid",
  "state_version": 17,
  "phase": "TURN",
  "decision_status": "recommended",
  "action_type": "move",
  "move_name": "トリックフラワー",
  "switch_target": null,
  "mega": false,
  "warning": null,
  "reason_code": "CHOICE_LOCK_VALID"
}
```

選出レスポンスには`our_three`, `lead`、PREPには`matchups_18`と`win3`を返すフェーズ別スキーマを用意する。フィールドのスキーマ整合性は保証されても、戦術の正しさは保証されないためValidatorで再検証する。出力は技名一つ、交代先などの短い表示に変換する。

## 10. API使用量の最小化

**API利用回数の計画値：** 1試合Nターンの場合、選出1回＋PREP1回＋各ターンN回＝`N+2回`を基本とする。再判断、OCR例外、ネットワーク再試行は追加回数となりうる。

削減策：

- 常時映像をAPIへ送らない。OCR・HP・画面分類はローカル。
- 戦闘アニメーションや同一フレーム変化ではAPIを呼ばない。
- 相手6行は一括取得し、全358行や全習得技を毎回送らない。
- 現在のOpenAI判断に必要な情報は削らず、静的指示→動的状態の順序を固定してキャッシュしやすくする。
- 出力は短いStructured Outputs。詳しい分析は対戦後の任意呼出しに回す。
- まず単一モデルを基準に応答時間・判断精度を測定し、必要なら選出/PREP/ターンでモデル設定を分ける。勝率悪化を伴う安易な小型化はしない。
- API 429、予算上限、503に対する再試行はdeadlineとサーバーの`Retry-After`を尊重。ターンを越えて無制限再試行しない。

費用は選択モデルと入力/出力トークン・キャッシュ命中率・試合ターン数から測定し、価格は運用時点の公式価格を採用。固定の金額は本設計時点では未推計。

## 11. 鮮度・障害時のフェイルセーフ

すべてのリクエストに`match_id`, `request_id`, `state_version`, `phase`, `turn`, `deadline_monotonic`を付与。応答受領時、最新の試合/局面と一致しない結果を破棄する。API側で古い要求がキャンセル不能でもUIには表示しない。

- **OCRが不確か**：補助表示を「未確認」にし、重要事項の手動補正を受け付ける。推測を事実にしない。
- **4ファイル未読込**：READY扱いにしない、APIにも選出可能と伝えない。
- **API障害・429・遅延**：`判断未確定`を表示。Pythonによる戦術的代行はしない。
- **PREPが間に合わない**：40秒で中断、未完成記録、相手初手への行動APIを優先。
- **AI推奨が技固定などに違反**：Validatorで表示停止。余裕があれば最新状態でAIへ修正要求、時間切れなら判断未確定。
- **使用者が別の技を実行**：画面で観測した実際の技・交代を正として状態更新する。
- **相手フォームやタイプが変化**：旧タイプ相性・旧推奨を無効化し、現状態で再判断。

## 12. UI画面

BlueStacks右側の小型ウィンドウ。常時表示項目：`phase / turn / 認識状態 / API状態 / 現在自軍・相手 / HP / 推奨技 or 交代先 / 重要警戒 / 最終更新時刻 / 残り秒数`。必要なボタンは`ランクマ開始`、`認識を訂正`、`判断を再取得（明示操作）`、`ログ確認`。ゲーム内操作の代行は行わない。

UI状態は少なくとも`準備完了 / 認識中 / OpenAI判断中 / 推奨確定 / 判断未確定 / 情報が古い / 対戦終了`で区別する。短い推奨文を大きく表示し、詳細分析は折り畳む。

## 13. ログと品質評価

記録項目：試合日付・勝敗・相手6匹・双方選出・初手・観測実行行動・各ターン推奨・盤面スナップショット・OCR訂正・PREP完成状況・API遅延・トークン利用量・費用推計・支援ミス分類・人間戦/CPU疑い/不明。支援の誤りは`認識`, `状態管理`, `選出`, `PREP`, `ターン判断`, `出力検証`, `時間超過`に分類。

LIVE判断ゲート内CASE_REGRESSIONや既存対戦ログをリプレイ試験に利用する。O/X補助評価は**対戦後のみ**実行し、対戦中に新しい重い評価を追加しない。

### 受入基準案

| テスト | 合格条件 |
|---|---|
| 事前ロード | 必須4/4実読込、索引整合。失敗時完了宣言ゼロ |
| 相手6匹認識 | 正規名・フォーム・順序を保持。不明は不明扱い |
| 選出判断 | 必ずOpenAIレスポンス。最終選出後に不意の変更なし |
| PREP | 18対面＋WIN3を期限内に確認。未完成を偽装しない |
| ターン判断 | 推奨はOpenAI由来。ローカルは検証のみ |
| 状態保持 | HP・技固定・観測済みS・メガ/タイプ・選出が消えない |
| 古い応答の処理 | `state_version`違いの指示を画面に表示しない |
| 判断品質 | 過去の支援ミス再現テストをすべて実行して評価 |
| 応答時間 | 選出20秒、PREP40秒、ターン15秒の上限を試験で評価 |
| API効率 | 原則`ターン数+2`、無関係な画面監視によるAPI利用0 |

※OCR精度や応答速度の絶対達成率は実機測定後に確定する。

## 14. 開発段階

1. **MVP-A：画面取得**：BlueStacksを固定解像度で実行し、画面種別/キャプチャ/リングバッファと対戦テキストの記録を確認。
2. **MVP-B：認識・状態管理**：相手6匹、技名、HP、交換、メガ、状態変化、技固定を自動認識し、人間が訂正可能にする。
3. **MVP-C：OpenAI選出**：4ファイル事前ロード、358行検査、相手6行一括取得、選出1API、結果表示を実装。
4. **MVP-D：OpenAI PREP／ターン**：18対面＋WIN3、最新状態付きAPI、レスポンス整合性検証、応答遅延制御を実装。
5. **MVP-E：リプレイ検証・ログ連携**：録画済み試合で画面認識と判断精度、支援ミス、遅延・費用を検証し、ランクマに導入できるか判断。

最初の実機検証に必要な資料：BlueStacksで動作するゲーム画面（選出・対面・行動選択・技発動メッセージ・交代・対戦終了）、解像度、Windowsの動作環境。ゲームの利用規約と対戦中の外部支援可否も別途確認する。

## 15. 未決事項・制約

- BlueStacksでのChampions実動作、ADB/キャプチャ速度、画面レイアウトとUIテーマは未検証。
- 使用するOpenAI APIモデル、各フェーズの速度、認識誤り率、費用はベンチマーク後に確定。
- このプロジェクトのChatGPTチャット履歴・メモリがOpenAI APIへ自動共有されるわけではない。外部アプリ側で必要なファイルと状態を明示的に提供する。
- ゲーム内で観測できない相手の行動や隠し情報は、統計から確定しない。
- ランクマに外部認識・助言システムを適用可能かはゲームの規約確認が必要。初期検証は録画試合で実施。

## 16. 参照先

- BlueStacks ADB設定：https://support.bluestacks.com/hc/ja/articles/23925869130381
- OpenAI Structured Outputs：https://developers.openai.com/api/docs/guides/structured-outputs
- OpenAI Prompt Caching：https://developers.openai.com/api/docs/guides/prompt-caching
- OpenAI Rate limits：https://developers.openai.com/api/docs/guides/rate-limits
- OpenAI Conversation State：https://developers.openai.com/api/docs/guides/conversation-state
- OpenAI Latency optimization：https://developers.openai.com/api/docs/guides/latency-optimization

**結論：画像と状態はローカル、選出と戦術は常にOpenAI、結果の合法性と鮮度をローカルで確認する。1試合のAPI呼出は基本的に選出1＋PREP1＋行動入力回数。既存のLIVE判断ゲートを唯一正本として長期勝率改善を目指す。**
