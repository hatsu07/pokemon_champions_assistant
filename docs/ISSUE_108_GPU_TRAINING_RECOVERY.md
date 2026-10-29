# Issue #108 — Docker GPU学習の起動経路とONNX成果物の安全性

## 問題と修正

以前のGPU学習は `python scripts/train_screen_classifier.py` の直接起動に対し、
学習後に `from scripts.embed_screen_classifier_metadata import embed_manifest` を行っており、
コンテナの `PYTHONPATH=/workspace/src` では `scripts` のimportに失敗する場合がありました。
検証163/163バッチ到達後でも `exit=1` となり、ONNXが未完成の状態で残るおそれがありました。

今回の修正では **Windows画面収集GUI → WSL → Docker** の操作方式を変えず、
Docker内の学習起動を **`python -m scripts.train_screen_classifier`** に統一します。
ONNX/ONNX Runtime/メタデータ埋め込みのimportは学習前に検証します。

成果物は出力フォルダー内の非公開の一時ディレクトリで作り、
メタデータ・JSONのSHA-256・ONNX単体とJSON併用のモデル読込を検証してから、
**既存ファイルを上書きしない方法**でONNXとJSONを公開します。
失敗時は未完成のONNX/JSONを正式な出力名で残しません。
既存のONNX/JSONがある出力先への学習は開始前に拒否します。
通常の再学習では必ず `--archive-output` を利用してください。

## 修正版を導入した後の確認（実機GPUは別途）

1. 修正PRのCI（合成6分類のCPUテスト、Dockerラッパー実行、SHA-256・内蔵メタデータ検証）が成功したことを確認します。
2. WSLでローカルリポジトリの修正版を取得し、**6分類schema v3**のラベル済みデータを指定します（旧5分類／7分類のデータを直接流用しません）。
3. GPUをコンテナ内部から診断してから、学習データを検証します。

```bash
# パスは実際の、リポジトリ外にある6分類ラベル済みscreensディレクトリへ変更する
export TRAINING_DATA_DIR="/path/to/screens-six-v3"
./scripts/train_screen_classifier_docker.sh --gpu --check-gpu
./scripts/train_screen_classifier_docker.sh --validate-only
```

4. 1エポックの実機スモークを行います。

```bash
./scripts/train_screen_classifier_docker.sh --gpu --epochs 1 --archive-output
```

5. 問題なければ、従来どおり明示的に事前学習済み重みを指定して12エポック実行します。
収集GUIの「承認済みデータをGPU再学習」でも、修正版のWSLリポジトリを指定すれば同じDockerラッパーを経由します。

```bash
./scripts/train_screen_classifier_docker.sh --gpu --epochs 12 --pretrained --archive-output
```

6. `exit=0`、個別の `private-training/output/run-.../` にある
`champions_screen_classifier.onnx` と `champions_screen_classifier.json` の両方、
内蔵メタデータ、JSONのSHA-256、モデル読込を確認します。
`validated` は従来の品質ゲートでのみ決まり、試験用モデルが自動で正式承認されることはありません。
実画面の認識精度は別の独立対戦で検証してください。

## 以前の失敗結果の扱い

Issue #108発生時のONNX・ログ・アーカイブは**削除・上書きしないでください**。
旧コードではメタデータ埋め込み前に失敗し、JSONが作られていない可能性があります。
不完全なONNXを成功モデルとして利用したり、既存の`validated`を勝手に変更したりしません。
旧出力を安全に復旧できるかは個別に調査し、未検証のまま自動修復は行いません。

CIは実画像・学習済みモデルを使用せず合成画像だけで検証します。
**CI成功はCUDA実機での学習成功や実戦認識精度を保証しません。**
GPUの `exit=0` と成果物の正常性を実機で別途確認して、Issue #108の完了判定を行います。
