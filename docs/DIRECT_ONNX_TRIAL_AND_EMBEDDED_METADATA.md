# 5分類ONNXを直接選択・試験利用する（2026-10-26）

## まず既存のモデルを使う

1. Windowsアプリの対戦支援画面で **「画面分類ONNXファイルを選択」** を押す。
2. `champions_screen_classifier.onnx` **ファイル**を直接選ぶ。フォルダーを選ばない。
3. 同じフォルダーに `champions_screen_classifier.json` があればアプリが自動読み込みし、5分類／schema v2／入力サイズ／正規化／SHA-256を照合する。ユーザーによるJSON指定は不要。
4. `validated: false` の場合は **「未検証モデル：試験利用の確認」** が出る。明示的に承認した場合のみモデル設定を保存する。画面上のステータスは **「未検証・試験モード」** と表示され、次回の対戦開始時にも確認する。
5. 承認しない場合は設定を書き換えない。モデルなしモードは前回の仕様どおり開始時警告・本人確認付きの低頻度画像変化検出を維持する。

### 過去に設定したフォルダーについて
旧設定 `screen_classifier_choice.json` の `directory` は引き続き読み込む。新規設定では個別の `model_path` と `trial_opt_in` を使用する。選んだモデルはローカルに存在するまま読み込み、秘密のスクリーンショットは送信・複製しない。

## ONNX単体で使う（JSONなし）

新形式のONNXには、5分類ラベル・入力形状・mean/std・閾値・validated等を ONNX 標準の `custom_metadata_map` に保存する。ローダーは同名JSONが存在すればそのSHA-256付き仕様を優先し、なければONNX内蔵メタデータを使用する。埋め込み先のメタデータキーは `pokemon_champions.screen_classifier.manifest_v2`。

今後、`scripts/train_screen_classifier.py` で学習して出力するONNXにはこのメタデータを自動で埋め込む。従来のJSONも互換性とSHA確認のため出力する。**ファイル内部に自己SHA-256を保存することはできない**ため、JSONなしモデルは埋め込み設定・ONNX実行時の形式検査で確認し、JSONペアと同等のハッシュ検証だとは主張しない。

### 既存のONNX＋JSONを、オリジナル無変更で1ファイル化する

教師画像・モデルの本体はGitHubやCIへアップロードしない。ローカルのtrainerコンテナで、オリジナルを保持したまま新ファイルへ変換する：

```bash
docker compose --profile training run --rm trainer python scripts/embed_screen_classifier_metadata.py \
  --model /workspace/private-training/output/champions_screen_classifier.onnx \
  --manifest /workspace/private-training/output/champions_screen_classifier.json \
  --output /workspace/private-training/output/champions_screen_classifier_standalone.onnx
```

入力ファイルが`private-training/output`にある場合の例。実際のファイル配置に合わせて指定すること。出力ファイルが既存なら中断し、入力ONNXもJSONも上書きしない。`validated`の真偽は元JSONから**変更しない**。

作成した `champions_screen_classifier_standalone.onnx` をWindowsアプリで直接選択すれば、隣にJSONがなくても読み込める。新しいモデルの精度は、この変換だけでは向上しない。

## 制約と検証
- 旧7分類・schema v1、偽のラベル、既存JSONとのハッシュ不一致は試験モードでも拒否する。
- 「未検証モデル」は本番性能が証明されたという意味ではない。誤送信・AIクレジット消費・行動提案の遅延は実機ログで再評価する。
- アプリによるONNX読み込みとファイル選択はWindows CIのオフラインテスト・モックで検査する。実際のBlueStacks対戦精度は別途検証が必要。
