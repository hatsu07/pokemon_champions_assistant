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

GPU学習の起動方式・失敗時の出力保護・修正後の再実行手順は [Issue #108の検証手順](ISSUE_108_GPU_TRAINING_RECOVERY.md) を参照。

学習と独立した試合をholdoutにする。6分類モデルの精度・速度と
`RESULT_SCREEN` の誤検知・見逃しをWindows実環境で検証する。
`validated` はデータ不足や品質ゲート未達なら `false` とする。
モデルとJSONは対にしてローカルで管理し、未検証モデルは
ユーザーの明示的な試験同意なしで実戦用として選択しない。
旧5分類モデルを6出力と偽るマニフェスト変換は禁止。

## 結果画面とGPT判定

1. 戦闘中、`RESULT_SCREEN` を2フレーム連続・confidence>=0.95・
   margin>=0.50で安定検出したら候補にする。その他の画面を結果扱いしない。
2. `RESULT_SCREEN` は**直前に取得済みのバトルメッセージをGPTに送るトリガー**であり、
   結果画面をOCRするトリガーではない。戦闘イベント中に `BATTLE_EVENT` などで
   非同期OCRへ登録したフレームを、結果画面の安定検出時点で固定する。
   未完了フレームがあれば**OCR完了通知を受けるまで秒数制限なしで待つ**。
   戦闘中と異なり入力時間制限はなく、GUIや手動勝敗確定は引き続き利用可能。
   待機中に分類器が一時的に揺れても結果候補を上書きせず、後からキューに
   入った画像のOCRで待機を引き延ばさない。OCR処理が利用不能なら手動確定。
3. 完了後、`LocalBattleStateTracker` の元の記録から、
   **結果画面を検出した時刻より前のバトル/システムメッセージOCR最大10件**を
   時系列・フレームID・信頼度付きでGPTへ送る。
   `RESULT_SCREEN`の画像もOCRも追加送信せず、結果 `WIN/LOSS/UNKNOWN`
   と引用元を照合する。
   出典に存在しない引用や、個別ポケモンのひんししかないときは
   `RESULT_UNCONFIRMED` とし、勝敗を自動確定しない。
4. 勝敗確定時だけ対戦をENDEDにし、旧戦術結果を表示しない。
   結果画面までのバトルメッセージOCRが未完了のまま失敗した場合、
   完了後も過去のメッセージが存在しない場合、GPTが判定不能・矛盾を
   返した場合は手動確定を残す。OCR処理時間を理由にGPTへ早送りせず、
   45秒ターン期限も適用しない。
   GPT通信のネットワーク障害対策のタイムアウトは別管理（現在120秒）。

**制約:** 現在のGitHubにはユーザーの実ゲーム画像を置かない。
学習精度、BlueStacks・スマホミラーリングでの実測、1試合を通じた
45秒期限内成功率は別の実機検証であり、Windows CIと混同しない。
