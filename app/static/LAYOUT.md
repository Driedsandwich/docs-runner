# A3 — Containment map / implementation plan

A3 の構成表。画像は形と配置の参照のみ。文言・状態・数値は jobs API の値を使う。

| 区画 | 役 | 中身 | 部品 | 見本のどこを写すか |
| --- | --- | --- | --- | --- |
| 操作列 | 入力 | URL、Run audit / Stop / Re-run、good / stale / hostile、制限、No secrets | form、label、button、fieldset、ナビ | PC 左208px、スマホ48pxアイコン列 |
| スマホメニュー | 操作列を開く | 同じ入力部品、Close、区画へのリンク | 左310px drawer、スクリーン、focus trap、Escape、復帰 | 画像3、右80pxの図面を残す |
| 図面ヘッダー | 監査の識別 | A3 CONTAINMENT MAP、Preview data / live、Run ID、観測時刻 | 上罫線、等幅ラベル | PC上端、スマホの製品ヘッダー |
| 見出し | 現在の結果 | 実行状態と結果（Preview data は明示）、集計、URL、coverage | h1、紫の縦線、短い補助文 | 画像1の上部、画像2のタイトル |
| 入口 | 手順の選択 | 手順名・番号・記号・全文status、抽出時の注入検出 | 縦リスト、選択button、接続矢印、入口の停止線 | 左手順列、スマホのIncoming steps |
| 中央境界 | 実行環境の内外 | SANDBOX、制限・秘密なし・隔離表示 | 二重2px境界、角のマーク、状態ラベル | 画像1の中央大枠、画像2の箱 |
| 箱内証拠 | 選択手順の結果 | command、exit、time、limits、stderr/stdout、修正提案、ID、source line、準備・fixture | 証拠展開、pre、copy、wrap、全文dialog | 中央のStep03、変更案にUnverified suggestion / Not applied |
| ライフサイクル | 箱の状態 | Creating / Isolated / Destroying / Destroyed + 確認時刻 | 独立した下帯 | 中央箱の底辺、破棄中も証拠保持 |
| 右停止経路 | 隔離イベント（得点と別） | Hostile probe、Run ID、network / limit / root、イベント時刻と証拠 | 一本ずつ追加する停止線＋ラベル、詳細展開 | 画像1右列、画像2下部の縦経路。注入は入口側 |
| 下側の次ジョブ | 継続の観測 | 次の仕事のID、sandbox、状態（通常は手動実行） | 独立境界、API追跡 | 画像1下部左。自動で実行するのは無害なfixtureだけ |
| 結果帯 | 文書監査の集計 | passed / failed / unverified、health、verified、coverage | 横罫線、status札、得点 | 画像1下部右 |
| フッター | 文脈 | Docs Runner / A3、fixture説明 | 横罫線、補助文 | 画像1最下端 |

## データ方針

- 3 つの見本は DOCSRUNNER_PUBLIC_ORIGIN の /demo-docs/<name> を通常の仕事として送る。取得・LLM・Docker・判定を実行し、実結果を SSE で配信する。fixture は DOCSRUNNER_UI_FIXTURES=1 の開発環境で Preview data を選択した場合のみ。
- fixture の手順は独立sandbox、準備・読取り専用fixtureを証拠に記載。実際の従来実行は共有sandboxであり、その事実と実際の制限を表示する。未取得の値は — とする。
- 元の失敗と提案を保持。既存APIが再試行した場合はその試行を別表示し、Not applied と誤表示しない。
- 得点は passed / (passed + failed) の四捨五入。未検証と未完了を成功に含めない。分母0は Not scored / —。隔離イベントを分母に入れない。
- 各イベントに所属Run ID、時刻、Sandbox ID（抽出イベントは未作成）、ポリシー・根拠を保存。注入検出は抽出時の信頼境界として表示する。
- Stop は追加の停止APIで実際にタスクをキャンセルし、確認された最終snapshotまで追跡する。

## 抜けの点検（実装前）

- [x] スマホメニュー：44px操作領域、背景scroll停止、trap、Escape、Close、元トリガー復帰。PCの入力を複製しない。
- [x] 空：未接続の入口、破線境界、No active sandbox、Not scored。
- [x] 読み込み：Creating、現在の手順のみ経路を強調、同じ実行ボタンがStop。
- [x] 失敗：role=alert、再試行、既存証拠を残す。SSE障害は接続状態として区別。
- [x] 長いURL/出力：min-width:0、折返し、出力内部だけscroll、独立全文dialog。
- [x] 15手順：縦リスト内部のscroll、選択行の視認性、mobileは自然な縦配置。
- [x] 未実行：exit/time/outputはNot executed、鍵入力を作らない。
- [x] 破棄：Destroyingの間は箱を残し、確認後のみDestroyed＋時刻。
- [x] 動き：観測された停止線を順番に追加、短い境界強調、reduced-motionでは無効。
- [x] フォント2種以内：system sans / system mono、外部assetsなし。
- [x] 1440 / 390、menu、空、loading、error、長いURL、15手順をローカルheadlessで点検。

## 作業順

1. 本構成表 → 2. 実行中・完了の主画面とAPI値の接続 → 3. PC/スマホを撮影・調整 → 4. 同じ部品で空・loading・error → 5. 操作・コントラスト・試験・patch出力。
