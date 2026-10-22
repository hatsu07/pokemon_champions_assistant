> **注意（v2.6）**: このファイルは旧開発手順の参考資料です。現在の正式手順は `WSL_DOCKER_VSCODE_CODEX_SETUP.md`、Windows実行とMSI配布仕様は `docs/Pokemon_Champions_WSL_Docker_Windows_MSI_Design_v2_6.md` を優先します。

# Windows setup (WSL + Docker, v2.5)

**このパッケージの正規手順は [`WSL_DOCKER_VSCODE_CODEX_SETUP.md`](WSL_DOCKER_VSCODE_CODEX_SETUP.md) です。**

古いv2.4の「WindowsにPython venvを入れる」手順は、ローカル環境を汚したくないという方針に適合しないため廃止しました。

WindowsにはBlueStacks・Docker Desktop・VS Codeを置き、WSLにソースとCodex拡張、Docker内にPython/OCR/ADB/テスト環境を閉じ込めます。
