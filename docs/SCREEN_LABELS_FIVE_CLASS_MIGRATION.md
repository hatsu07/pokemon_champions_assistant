# Pokémon Champions 画面分類：5分類仕様と旧7分類データ移行

2026-10-24採用。対象は**対戦支援に必要な画面の検出**。画面分類器のラベルだけで技名・特性・持ち物等の事実を確定してはいけない。旧7分類のPNGやONNXを**自動削除・変更しない**。

## 分類の正本（モデルschema_version=2）

| 新ラベル | 入れる画像 | 対戦支援側の扱い |
|---|---|---|
| `TEAM_SELECTION` | 相手6匹確認・自軍選出3匹と初手を入力できる画面 | 行動判断対象 |
| `TURN_ACTION` | 各ターンの始まりでプレイヤーが技・行動を選べる画面 | 行動判断対象 |
| `SWITCH_SELECTION` | 自主・強制交代で交代先を選択できる画面 | 行動判断対象 |
| `BATTLE_EVENT` | 技・特性・持ち物・交代・メガシンカ・能力変化・HP変化など、**新たな対戦情報を読み取れる**イベント画面 | 情報抽出の候補画面。画面を分類しただけでは確定しない |
| `OTHER` | 上記以外すべて。単なる待機・結果・意味のない演出・通常UI | 原則行動要求しない |

**分類の優先順位:** 実際に入力可能な3画面なら対応する操作ラベル。イベント情報の表示が主で現在入力できない画面は`BATTLE_EVENT`。それ以外は`OTHER`。同一画面で操作可否が分かりにくい場合は元画像の確認を優先する。

**`UNKNOWN`は学習クラスではない**：推論の信頼度・マージン不足は暫定の`UNKNOWN`（未確定）を返せるが、学習用フォルダには含めない。AIが未判定とした画像を正解`OTHER`に自動変換しない。

**イベント取得の別責務:** `BATTLE_EVENT`の判定はイベントが存在する可能性の通知であり、技名・対象・能力値・メガ後タイプ等の抽出ではない。ChatGPTへ送るのは人間または別の画像/テキスト抽出処理で**実際に判明した情報のみ**。同じ画面の連続通知は抑制するが、違う重要イベントを見逃さないかは実機で検証が必要。結果画面は`OTHER`なので、勝敗判定・対戦終了は分類器以外の既存対戦状態管理や今後の明示的な終了検出で扱い、完備済みとは主張しない。

## 旧7分類の元データは保持

従来 `MATCHMAKING` / `TEAM_SELECTION` / `MOVE_MENU` / `PARTY_MENU` / `BATTLE_EVENT` / `RESULT` / `UNKNOWN` のPNGを、新しい学習ルートに**コピー**して作る。

| 旧ラベル | 移行方法 |
|---|---|
| MATCHMAKING | OTHER（安全に自動コピー） |
| TEAM_SELECTION | TEAM_SELECTION（安全に自動コピー） |
| MOVE_MENU | TURN_ACTION（安全に自動コピー） |
| PARTY_MENU | SWITCH_SELECTION（安全に自動コピー） |
| RESULT | OTHER（安全に自動コピー） |
| BATTLE_EVENT | **人間が画像ごとに確認**：対戦情報を含むものだけBATTLE_EVENT、単なる演出はOTHER |
| UNKNOWN | **人間が画像ごとに確認**：正しい5ラベルへ割り振る。判断できなければ学習対象にしない |

Windowsのローカル `screens` は原則 `%LOCALAPPDATA%/PokemonChampionsAssistant/private-training/screens` を確認する。環境や保存先設定によって異なるため、最初にGUI内の保存先を確認する。WSLで元のフォルダを特定したら次を実行（実際の環境のパスに置き換える）。

```bash
# WSL例。移行元を読み取り専用の根拠として残す
OLD="/mnt/c/Users/<WindowsUser>/AppData/Local/PokemonChampionsAssistant/private-training/screens"
NEW="/mnt/c/Users/<WindowsUser>/AppData/Local/PokemonChampionsAssistant/private-training/screens-five-v2"

# まず実コピーしない確認
python3 scripts/prepare_five_class_screen_dataset.py --source "$OLD" --destination "$NEW"

# プレビュー結果とバックアップを確認後、NEWが存在しないときだけ実行
python3 scripts/prepare_five_class_screen_dataset.py --source "$OLD" --destination "$NEW" --apply
```

**重要:** 既存7分類PNGも旧ONNXとマニフェストも保持する。コピー対象で同名衝突、パス不正、元フォルダと新フォルダの入れ子、保存先に既存データがある場合は安全側で中断する。すべてのクラスで**独立した複数セッション**を確保し、上記の要確認画像だけ人間が`BATTLE_EVENT`または`OTHER`などへコピーした後に学習する。元ファイル名（対戦セッションID付き）は保持する。新しい訓練ルートには5クラスだけを置き、`_UNLABELLED`は学習しない。

```bash
# 新しいデータルートで学習前検査。GPU学習の前に必須
TRAINING_DATA_DIR="$NEW" ./scripts/train_screen_classifier_docker.sh --validate-only

# 検証が通り、ラベルとセッションを確認した後に実際の再学習
TRAINING_DATA_DIR="$NEW" ./scripts/train_screen_classifier_docker.sh --gpu --epochs 12 --pretrained --archive-output
```

現行のONNX読み込みは`schema_version=2`、かつ5ラベルの完全一致を要求する。**旧schema_version=1・7出力のモデルは読み込めない**。検証済み5分類モデルがない間、画面分類器を利用した自動送信の精度・運用を保証しない。画像の人手ラベル付け・収集は引き続き可能。

## 受入基準

- 学習・GUI・自動候補生成・ONNXマニフェスト・画面トリガーが5ラベルに統一される
- 旧ONNXの読み込み拒否、推論の低信頼`UNKNOWN`と教師`OTHER`の区別をテスト
- 3操作画面、BATTLE_EVENT、OTHERの分類結果でイベントと操作トリガーを混同しない
- 移行はドライラン既定・元ファイル無変更・既存の別データ無上書き・要確認画像未コピー
- オフライン合成テストは成功しても、実機のメガ・特性・持ち物表示や戦術の正確性は未検証
