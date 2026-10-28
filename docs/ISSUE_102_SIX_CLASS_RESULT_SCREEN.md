# Issue #102/A — 6分類学習・結果画面からの勝敗判定

この変更の目的は、既存のローカル実画像から6分類を**再学習可能**にし、
二重に安定検出した `RESULT_SCREEN` と直前の戦闘OCRから、同じ1回の
GPT結果判定で `WIN / LOSS / UNKNOWN` を得ることです。
モデルを学習・配置しただけでは実機精度は確認済みになりません。

## 新しい分類仕様

`TEAM_SELECTION`、`TURN_ACTION`、`SWITCH_SELECTION`、
`BATTLE_EVENT`、`RESULT_SCREEN`、`OTHER`。
`UNKNOWN` は低信頼推論の保留値で、教師ラベルではありません。
結果画面の最終的なWIN/LOSSを分類器が直接推定することはありません。
モデルmanifestの `schema_version=3` と6分類出力を一致させます。

## 既存ローカル画像の安全な移行

**元の7分類データ・5分類データ・モデルを削除も上書きもしません。**
`RESULT` のラベル済み画像がローカルにある場合、新しいディレクトリへ
`RESULT_SCREEN` としてコピーします。旧 `MATCHMAKING` は `OTHER`、
`MOVE_MENU` は `TURN_ACTION`、`PARTY_MENU` は
`SWITCH_SELECTION` へコピー。従来の `BATTLE_EVENT` と `UNKNOWN` は
**人間が個別確認**して正しい6分類へコピーします。

```bash
OLD="/mnt/c/Users/<WindowsUser>/AppData/Local/PokemonChampionsAssistant/private-training/screens"
NEW="/mnt/c/Users/<WindowsUser>/AppData/Local/PokemonChampionsAssistant/private-training/screens-six-v3"

# まず予行演習: 原本や新しい学習先は作りません
python3 scripts/prepare_six_class_screen_dataset.py --source "$OLD" --destination "$NEW"

# 原本とプレビュー件数を確認し、NEWが未作成のときだけコピー
python3 scripts/prepare_six_class_screen_dataset.py --source "$OLD" --destination "$NEW" --apply

# 検査通過・独立セッションが6クラス揃うまで学習しない
TRAINING_DATA_DIR="$NEW" ./scripts/train_screen_classifier_docker.sh --validate-only

# 同じローカルデータから別アーカイブへ6出力モデルを再学習
TRAINING_DATA_DIR="$NEW" ./scripts/train_screen_classifier_docker.sh --gpu --epochs 12 --pretrained --archive-output
```

学習と独立した試合をholdoutにする。6分類モデルの精度・速度と
`RESULT_SCREEN` の誤検知・見逃しをWindows実環境で検証する。
`validated` はデータ不足や品質ゲート未達なら `false` とする。
モデルとJSONは対にしてローカルで管理し、未検証モデルは
ユーザーの明示的な試験同意なしで実戦用として選択しない。
旧5分類モデルを6出力と偽るマニフェスト変換は禁止。

## 結果画面とGPT判定

1. 戦闘中、`RESULT_SCREEN` を2フレーム連続・confidence>=0.95・
   margin>=0.50で安定検出したら候補にする。その他の画面を結果扱いしない。
2. 安定検出した**結果画面の正確なフレームID**について、ローカル非同期OCRを
   キューへ1回登録する。メッセージ領域が前フレームと同じ画像でも、確定した
   結果フレームのOCRは省略しない。**処理完了通知が届くまで秒数の上限を設けず**
   非同期で待つ。GUI操作と手動勝敗確定はその間も利用できる。
   完了後、実際の結果画面OCRと直前最大10件の戦闘情報を
   `frame_id`、時刻、確信度付きで抽出してGPTへ渡す。
   他の戦術リクエストが実行中なら完了を待ってから一度だけ結果確認する。
3. **画像は追加送信しない。** GPTにはOCRのみを渡し、
   結果 `WIN/LOSS/UNKNOWN` と引用した原文断片・出典フレームを要求。
   出典に存在しない引用や、個別ポケモンのひんししかないときは
   `RESULT_UNCONFIRMED` とし、勝敗を自動確定しない。
4. 勝敗確定時だけ対戦をENDEDにし、旧戦術結果を表示しない。
   OCRが失敗・無効化された場合、処理完了でも文字が取れない場合、
   GPTが認識不能・矛盾を返した場合は手動結果確定を残す。
   OCR処理時間を理由にGPTへ早送りせず、45秒ターン期限も適用しない。
   GPT通信のネットワーク障害対策のタイムアウトは別管理（現在120秒）。

**制約:** 現在のGitHubにはユーザーの実ゲーム画像を置かない。
学習精度、BlueStacks・スマホミラーリングでの実測、1試合を通じた
45秒期限内成功率は別の実機検証であり、Windows CIと混同しない。
