# Issue #139 / Phase 0 — Pokémon Showdown Champions 技術PoC

本番アプリ・GPT経路・ゲーム操作は変更しない。合成データを使う隔離実験としてのみ利用する。

## 対象と根拠
- upstream: https://github.com/smogon/pokemon-showdown
- 固定commit: ccfaa20f32af363569cd21a9d501b2bc1ded26b1 （2026-10-30参照）
- format: [Gen 9 Champions] BSS Reg M-C / mod champions
- Node >= 22.18.0 (upstream package.json)
- upstream: config/formats.ts, data/mods/champions/{scripts,moves,abilities,items,learnsets}.ts, sim/{index,battle,state,battle-stream}.ts
- LICENSE: upstream MIT。依存npmの配布条件、ソース変更通知、SBOMは別途確認し、MITのみで依存物を許諾済みとしない。

## 再現手順（WSL2 + Docker）
リポジトリルートで実行。WindowsホストへPython/Nodeを追加しない。

    docker build -f poc/showdown_phase0/Dockerfile -t champions-phase0 .
    docker run --rm champions-phase0
    docker run --rm --entrypoint node champions-phase0 --test test_decision.cjs

Dockerfileはupstream SHAが期待値と完全一致することを確認してから npm ci / build を行う。実行時は外部通信しない。動的計測JSONを保存する場合のみ、ユーザーが選んだローカル領域へ --out パスを指定する（機密画面や対戦履歴は使用しない）。

## 実験の内容
- 子Nodeプロセスを常駐させてローカルJSONL IPC。request ID、有限タイムアウト、古い/重複応答の破棄を実験する。
- Battle.toJSON()/Battle.fromJSON()による3対3の途中状態の複製と同一RNG状態での分岐再現を確認する。
- HP、PP、状態異常、能力ランク、持ち物消費、こだわり固定、天候、壁、強制交代を合成条件で試す。任意のメガ後履歴の復元は明示的にUNSUPPORTED。
- 同じ観測可能局面・同一行動に対して未判明の相手技/道具が異なる2仮説を進め、結果の相違と観測元不変を検証する。未知値を観測確定に変換しない。
- 20回ウォームアップ後、6種類×20回=120件のケースを試す。直列化・復元・合法選択・分岐解決を別カウンタに分け、成功した分岐解決のみ速度計測の分母とする。強制交代は交代先の実際の変化を検証する。
- 完全な実機中途状態の復元ではないため、合成条件を全通過しても途中復元ゲートはPARTIAL止まり。

## レビュー指摘への対応（PR #143）
- P0：PR head.shaを明示してcheckoutし、ビルド前に実checkout SHAと一致検証。Draft中のWindows CI skippedはSuccessではない。
- P1：Docker内に正本の固定CSV4件を読取り専用コピー。audit.cjsはCHAMPIONS_MASTERS_DIR入力とSHA256・比較件数を出力し、欠損時NOT_RUNで失敗する。静的一致は実機証明ではない。
- P1：serialized_ok / reconstructed_ok / choice_legal / branch_resolved / unsupportedをカテゴリ別に記録。合法な行動と分岐解決を確認しない状態は成功件数へ加えない。
- P1：同じ観測盤面と同じ行動で未知の相手型を2種別々に評価し、結果の違いと元状態の独立性を確認する。
- P1：全120件のステージと時間、およびマスターSHA・監査差分はGitHub Actionsログへ機械可読形式で書き出す。Step Summaryには集計を記録。Actions Artifacts/Releaseには出力しない。ログ保持期限に従うため長期保存が必要ならローカルの--outで保存する。ゲーム実画像・対戦履歴・秘密情報は扱わない。
- NO_GOは現時点の安全側の評価で、将来の最終採否判定器ではない。必須ゴールデン項目・未検証範囲はgolden_gate_requirements.csvに示す。
## ゲートと実戦投入の禁止
A = 計算互換性。compatibility.csvの個別項目をChampions固定マスター・公式情報・実機ゴールデンケース（観測日時、レギュレーション、期待値、実測値）で監査する。現時点はUNKNOWN。Showdownにモジュールがあることは合格条件ではない。

B = 途中局面復元。残HP/ひんし/能力変化/状態異常/残PP/消費済み持ち物/技固定/メガ履歴/天候/壁等を、不完全な実ゲーム観測から正しく再構成し、同一seedでの一致・分岐相互不干渉を実証する。今回の合成状態だけでは合格としない。未対応ケースを明示する。

双方が合格かつ必要な計算仕様の互換が確認されるまで、採用判定はNO_GO。確定KO、安全受け、行動順保証の根拠にしてはならない。実戦モードのfeature flag切替と探索は本Issueの対象外。

## 配布・E2E未解決
ビルドコンテナは製品ではない。Nodeランタイムとコンパイル済みShowdownのWindows onedir同梱、オフライン起動、npm依存物通知・SBOM、クラッシュ復旧、Windows実測は別ゲート。Node単体の性能と「Androidミラー→OCR→PySide6表示」のE2Eは別記録。P95 3秒は目標案であって測定済みの保証ではない。

代替案：Champions専用に検証された狭い範囲の計算器を作り、未知局面はUNKNOWNに倒す。別エンジン（poke-engine等）のChampions対応と状態復元・ライセンス・Windows同梱を比較し、検証できないものを代替採用しない。

## 検証状況
- 2026-10-30にLinux Dockerによる固定Showdownビルド・合成PoC・静的監査をGitHub Actionsで実行。修正後HEADでの結果はPR #143の最新CIを参照。以前のSHAの成功を代替にしない。
- 初回静的監査でPP 33件・種族値21件・タイプ4件・習得技1件（計59件）の差分を検出。時点やフォーム等の要因を含むため、ゲーム内での不一致が確定したものではない。
- 未実施：独立した実ゲーム内ゴールデンケース、部分観測からの途中状態復元、任意のメガ履歴、Windows配布とNode IPC、Androidミラーから推奨表示までのE2E、依存物SBOM/通知。
- 両ゲートは未合格。本番採用やマージはしない。
