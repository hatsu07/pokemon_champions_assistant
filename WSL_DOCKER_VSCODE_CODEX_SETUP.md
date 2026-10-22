# WSL2 + Docker Desktop + VS Code + Codex — クリーン開発手順 (v2.6)

## 目的・境界

- **Windows**: BlueStacks 5 / Docker Desktop / VS Code。必要なら Windows 用 ADB 接続確認を実施。
- **WSL2 (Ubuntu)**: プロジェクトコード (例 `~/projects/pokemon-champions`)、Git、VS Code Remote-WSL、Codex IDE 拡張 (推奨)。
- **Docker Linux コンテナ**: Python 3.12、検証テスト、OCR (Tesseract 日本語)、ADB クライアント等、今後の OpenCV と OpenAI ランタイム。
- 戦術判断は **OpenAI API のみ**。Codex はアプリを開発するエージェントであり、ランクマの判断モデルとは別。
- Windows に Python / Node / OCR / アプリライブラリをインストールしない。WSL にもアプリの `pip install` を実行しない。

## Windows側の前提 (一度だけ)

1. WSL2 + Ubuntu を導入。未導入なら管理者 PowerShell で `wsl --install -d Ubuntu`、再起動が求められたら対応。既存 WSL があれば不要。
2. Docker Desktop をインストールし、Settings > General > **Use WSL 2 based engine** を選択。Settings > Resources > **WSL Integration** で開発する Ubuntu を有効にする。
3. Windows版 VS Code をインストールし、拡張 **WSL** (`ms-vscode-remote.remote-wsl`) と **Dev Containers** (`ms-vscode-remote.remote-containers`) を追加する。
4. VS Codeの **Codex – OpenAI's coding agent** (`OpenAI.chatgpt`) を使用する。拡張機能が実際にどの実行環境へインストールされるかを確認する。
5. BlueStacks 5 は引き続きWindows側で動作させる。コンテナがWindows GUIに直接アクセスできるとは仮定しない。

## ZIPからの開始 (Ubuntu/WSLターミナル)

以下の ZIP は「展開すると `pokemon_champions_codex_msi_v2_6` が現れる」構造。
`<WINDOWS_USER>` はご自身の Windows アカウントフォルダー名に置き換える。

```bash
mkdir -p ~/projects
cd ~/projects
unzip '/mnt/c/Users/<WINDOWS_USER>/Downloads/Pokemon_Champions_Codex_WSL_Docker_MSI_Handoff_v2_6.zip'
cd pokemon_champions_codex_wsl_docker_v2_5

docker version
docker compose version
docker compose build

docker compose run --rm dev python scripts/verify_package.py
docker compose run --rm dev python -m unittest discover -s baseline_v2_3 -p 'test_*.py' -v

code .
```

`docker` が見つからなければ Docker Desktop の WSL Integration とデーモン起動を確認する。WSL のUbuntu内にDocker Engineを二重インストールしない。

### VS Code + Codex（推奨: CodexはWSL、Python実行はDocker）

1. VS Code のステータスが `WSL: Ubuntu` 等になっていることを確認。
2. VS Code 拡張の Codex を **WSL側にインストール**し、ChatGPT アカウントでログインする。
3. `CODEX_START_PROMPT.md` を最初の依頼として渡す。
4. コード修正・レビューはCodex。テストやPython実行は `docker compose run --rm dev ...` に統一。
5. Codex に `sudo apt install` / `pip install` / `npm install` を Windows または WSL ホストで実行させない。

### VS Code Dev Containers で作業する（任意）

VS Code で F1 -> **Dev Containers: Reopen in Container** を実行。`.devcontainer/devcontainer.json` から Compose サービスを起動する。

- コンテナ内のPythonパスは `/usr/local/bin/python`。
- Dev ContainerでもCodex拡張を利用できるよう拡張IDを指定しているが、**VS Code拡張の実行場所・認証・サンドボックス制限により再ログインや調整が必要になる場合がある**。
- ログイン情報は自動共有しない。`~/.codex` を全権限でコンテナに自動マウントしない。
- この方式でCodexが起動しない場合、**CodexをWSL側で使い、Dockerで実行・テストする標準手順に戻す**。

## 運用コマンド

```bash
# Linux + Python + OCR/ADB を確認
docker compose run --rm dev python --version
docker compose run --rm dev tesseract --list-langs
docker compose run --rm dev adb version

# パッケージと契約テスト
docker compose run --rm dev python scripts/verify_package.py
docker compose run --rm dev python -m unittest discover -s baseline_v2_3 -p 'test_*.py' -v

# コンテナの対話シェル
docker compose run --rm dev bash

# Dev Container が起動済みのときのテスト
docker compose exec dev python -m unittest discover -s baseline_v2_3 -p 'test_*.py' -v

# 停止 / このプロジェクトのComposeネットワークを片付ける
docker compose down
```

`docker compose down` はこのComposeで管理するコンテナ・ネットワークを停止・削除する。ホストに置いたコードは消えない。イメージやDocker全体のキャッシュをすべて消すコマンドではない。

## 完成アプリのWindows実行・MSI配布

開発ツールはコンテナ内ですが、完成したアプリはWindowsに通常のアプリとしてインストールします。**実行時にWSL/Docker/Python開発環境は不要**です。`docs/Pokemon_Champions_WSL_Docker_Windows_MSI_Design_v2_6.md` が正式なWindows配布仕様です。

- PyInstallerはWindowsランナーで実行してonedirの配布物を作成。
- WiXによるper-user MSIはWindows環境でビルド。
- 初回の配布は署名、ライセンス、クリーンなWindows VMでのインストール・アップグレード・アンインストール試験が済んだ後に限定。
- ZIP同梱ファイルは**開発スターターであって完成MSIではありません**。

## BlueStacks接続は別途実機検証

BlueStacks 5 設定 > 詳細設定 > Android Debug Bridge を有効化できる（BlueStacks公式）。最初に **Windows側で接続先・ポートを確かめる**。ポート番号を固定値と決めつけない。

Docker Desktop では `host.docker.internal` がコンテナからWindowsホストへ向かうDNS名として用意されている。ただし、BlueStacksのADBポートが Windows の `127.0.0.1` **だけで待ち受けている場合、コンテナから直接届くとは限らない**。接続できないときは、無闇にADBポートをLAN公開しない。

接続方式を次の順に試験する。

1. WindowsのBlueStacks/ADB接続確認（Windows側の接続先を観測）。
2. コンテナから `host.docker.internal` で安全に到達可能か調査。
3. 直接不可なら、最小権限のWindowsローカル画面キャプチャブリッジ（ユーザー承認・ローカル限定通信・署名/認証）を設計し、録画経由でローカル検証。

Windows GUIはLinuxコンテナにそのまま存在しない。Win32 API の画面キャプチャ実装をコンテナ内で実行できるとは想定しない。ADBをゲーム自動操作に使用しない。

## 機密・セキュリティ

- `OPENAI_API_KEY` は現段階では不要。Phase 3 で `.env` (gitignore済) 又は安全な secret injection で渡す。サンプル・ZIP・ログには入れない。
- `docker.sock` を開発コンテナにマウントしない。ホストの `.ssh`、`.aws`、`~/.codex` 等も自動で渡さない。
- キャプチャした画像/録画は個人情報を含む可能性があるため、最初は架空のテストデータで検証。データの保存範囲・保持期限は明示する。
- WSL / Docker Desktop / VS Code はホスト側に導入されるため、**PC完全無変更ではない**。アプリ実行環境・依存ライブラリを隔離する設計。

## 公式ドキュメント

- https://docs.docker.com/desktop/features/wsl/use-wsl/
- https://code.visualstudio.com/docs/remote/wsl
- https://learn.microsoft.com/en-us/windows/dev-environment/docker/dev-containers
- https://developers.openai.com/codex/ide
- https://support.bluestacks.com/hc/ja/articles/23925869130381-BlueStacks-5-Android-Debug-Bridge%E3%82%92%E6%9C%89%E5%8A%B9%E3%81%AB%E3%81%99%E3%82%8B%E6%96%B9%E6%B3%95
