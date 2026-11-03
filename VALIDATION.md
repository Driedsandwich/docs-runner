# Docs Runner — 実装・検証記録

## 作成したもの

- `app/`: FastAPI の仕事 API／SSE、公開ページ取得、robots.txt、接続先 IP 検査と固定、
  Vultr 用 LLM 窓口、JSON 型検証と 1 回の再試行、決定的な判定、仕事の制御、利用上限。
- `sandbox/`: 1 仕事に 1 実行コンテナ。read-only、非 root、cap-drop ALL、
  no-new-privileges、tmpfs、512 MiB、128 PID、1 CPU、既定 60 秒／手順。
  runsc を登録済みなら使用し、未登録なら runc。ホストの環境変数を実行命令に継承しない。
- `proxy/`: 仕事ごとの Squid。既定 6 ドメイン＋文書のドメインを許可。
  内部 IP、メタデータ IP、制御サービスの公開 IP を拒否し、拒否ドメインを記録。
- `demo_docs/`: 英語の good／stale／hostile。隠し注入文、curl パイプ、削除命令、
  外部送信、フォークボムの見本を含む。
- 最小 HTML／素の JavaScript 画面、MIT LICENSE、README の骨組み、
  名前のみの `.env.example`、固定した依存ライブラリ。
- `deploy/`: Ubuntu VM 向け Compose、Caddy、gVisor 手順、異常終了後の回収タイマー。

## 検証結果（初回実装時）

- Python **3.12.16** で `.venv/bin/python -m pytest -q` を実行。
- **93 passed、0 failed、7 skipped**。
- 7 件は `docker` 印の実コンテナ結合試験。実行環境に Docker がないためスキップ。
- 偽の LLM と箱による仕事の全体フロー、再実行、注入フラグ、資格情報の分類、
  期待結果、接続先検査、取得サイズ、API／SSE、同時実行、日次／仕事別の LLM 上限、
  タイムアウト、失敗時の破棄確認を検証した。
- `compileall` 成功。Python wheel の作成成功。画面、デモ HTML、プロキシ設定が
  wheel に含まれることを確認した。Docker イメージの build は実施していない。

## 実装上の区別と未検証部分

- 元の手順の失敗は、修正案の再実行が通っても `failed` のまま残す。
  修正結果は `retry_status` と 2 回目の証拠に分けて返す。
- 正常終了して期待結果がない場合は `no_expectation`。非ゼロ終了や実行上限は
  期待結果の有無にかかわらず実際の失敗として記録する。
- 時間切れなどで箱を停止した後は、その箱で再実行できないため残りを `unsupported` にする。
  全体の作業時間は 300 秒まで。後始末には別途、有限の待ち時間を設ける。
- 独立性のため、各仕事の実行コンテナに専用のプロキシ用コンテナを添える。
  鍵を持つ制御サービスは仕事の内部ネットワークには参加しない。
- HTTPS の許可済みドメインへの通信内容は検査しない。許可リストは送信内容の
  検査機能ではない。拒否ログは保存量に上限があり、監査の完全性が不明な場合は
  `audit_complete=false`。cgroup の観測ができない場合は `metrics_available=false`。
- 文書の意味の抽出には LLM の誤りがあり得る。期待結果の原文引用を報告に残す。
  ファイルは `/work` の通常ファイルのみ照合し、シンボリックリンクは成功扱いにしない。
- 仕事と報告はメモリ内、最大 200 件／1 時間。再起動をまたぐ報告保存は未実装。
  日次予算と接続元の回数制限は SQLite で保持する。
- URL の query／fragment、認証、ブラウザ描画、対話操作、GPU は対象外。
  デモも公開 URL を HTTP 取得し、同じ robots.txt・DNS/IP 検査を通す。ローカル URL の例外は設けていない。
- 実 Vultr API、実 Docker／gVisor／Squid、Compose／HTTPS の稼働は未検証。
  GitHub への公開、Vultr VM の作成、公開デモの配備、画面の作り込みは未実施。

## Vultr VM で最初に確かめること

1. Docker Engine 28 以上、cgroup v2、Docker のネットワーク隔離規則、runsc の登録を確認。
2. `.env` を VM 内だけで設定し、選択したモデル ID、JSON 応答、`max_tokens`、
   制限と遅延を実際の Vultr Serverless Inference で確認する。ここは **要確認**。
3. イメージを 1 度 build し、`RUN_DOCKER_TESTS=1 .venv/bin/pytest -m docker -v` を実行。
   pypi 許可、禁止先／直接通信／メタデータの拒否、read-only、時間／PID／メモリ上限、
   コンテナとネットワークの削除を確認する。
4. good → stale → hostile → good を実行し、注入フラグ、元の失敗と修正結果、
   拒否ログ、破棄記録、次の仕事の継続を確認する。
5. Caddy の HTTPS、接続元制限、日次予算の再起動後の維持、回収タイマーを確認する。

詳しい起動コマンドと公式資料へのリンクは `deploy/README.md` を参照。

## 実デモ経路への変更（2026-11-04）

- 3 つの見本は `DOCSRUNNER_PUBLIC_ORIGIN/demo-docs/<name>` を通常の HTTP 取得・
  注入検知・抽出・Docker 実行・判定に渡す。ローカルファイルへの取得省略を撤去。
- fixture は `DOCSRUNNER_UI_FIXTURES=1` の開発環境で明示的に選択した場合のみ。
  画面に `Preview data` を表示し、既定では API と JobManager の両方で拒否する。
- A3 の表示に実際の試行、箱 ID、制限、拒否ログ、後始末を接続。SSE は実仕事も
  各時点の snapshot を持つ。注入と実行時の観測を分け、未観測の結果を補わない。
- Python 3.12.16、`.venv/bin/pytest -q --basetemp=.tmp/pytest`：
  **123 passed, 18 skipped in 2.69s**。18 件は Docker CLI と明示的な有効化を要する
  実コンテナ試験。この環境には Docker CLI がなく、実 Docker／実 Vultr は未検証。
- 既存の fixture 試験は明示的に有効化して同じ assertions を維持。旧ローカル取得の
  試験は、見本 URL も取得例外を与えないことへ強化。Docker 試験の実行・隔離・
  cleanup の assertions は維持し、実表示イベントの確認を追加した。
- V8 で JavaScript の構文と操作部分を検証。3 ボタンの公開 URL、既定での実行モード、
  明示的な Preview data 選択、URL 編集時の切替を確認。ブラウザの道具は使用していない。
- VM での実行・確認手順は `deploy/README.md`。公開設定で good → stale → hostile →
  good を実行し、API の試行・拒否ログ・制限・新しい箱 ID と cleanup を確認する。
