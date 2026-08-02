# 📱 外部通知 ＆ スマホ双方向リモート操作ガイド

Dual Sleeper は、Discord への一方向通知（Webhook）および Telegram Bot による双方向リモート操作・1秒応答に対応しています。

---

## 🟢 1. Discord Webhook 連携（一方向通知 ＆ Wake on LAN）

1. Discord サーバーのチャンネル設定 ➔「連携サービス」➔「ウェブフックを作成」をクリックします。
2. Webhook URL をコピーし、`config.json` の `"discord_webhook_url"` に貼り付けます。

```json
"discord_webhook_url": "https://discord.com/api/webhooks/..."
```

---

## 🤖 2. Telegram Bot 連携（双方向リモート操作 ＆ 1秒応答）

スマホの Telegram アプリから、リアルタイムのステータス確認、スリープ延長、スリープモード切り替えが行えます。

### 登録手順
1. Telegram アプリで `@BotFather` を検索し、`/newbot` と送ってトークンを取得し、`"telegram_bot_token"` に設定します。
2. Telegram で `@userinfobot` にメッセージを送ると自分のチャットIDが表示されるため、それを `"telegram_chat_id"` に設定します。

```json
"telegram_bot_token": "8880606679:AAGGLvQLswfWz...",
"telegram_chat_id": "6641481510"
```

### 利用可能なリモートコマンド
* **/status** : 現在の状態、リアルタイム通信速度、動的通信上限 (ベース ＋ マージン)、ゲームサーバー接続数を表示。
* **/sleep** : 次回スリープ移行時の挙動をトグル切り替え（`スリープ` / `休止状態` / `自動`）。
* **/server** : 高速消灯サーバーモードの切替（`オフ` / `デスクトップ時` / `常時`）。
* **/weather** : 端末周辺の現在の気象・落雷警報状況および 12時間 CAPE 雷予報を表示。

### スリープ予告割り込み機能
スリープ移行直前の 30 秒間の予告通知が届いた際、Telegram で文字や数字を1文字送信するだけで、画面は消灯したまま **スリープ移行を 10 分間一時延長** できます。

---

## 🌐 3. Web WoL (CloudWaker) による PC 遠隔起動連携

スリープ通知（Discord / Telegram）受信時、スマホからタップするだけで自宅 PC を遠隔起動できる Web WoL サービス [CloudWaker](https://cloudwaker.k7m.f5.si/) との連携方法です。

### 設定手順
1. スマホやPCのブラウザで **[CloudWaker (https://cloudwaker.k7m.f5.si/)](https://cloudwaker.k7m.f5.si/)** にアクセスします。
2. 画面の指示に従って PC の MAC アドレスや Wake-on-LAN 起動データを設定し、発行された **専用起動 URL**（例: `https://cloudwaker.k7m.f5.si/?data=...`）をコピーします。
3. `config.json` の `"wol_url"` にコピーした URL を貼り付けて保存します。

```json
"wol_url": "https://cloudwaker.k7m.f5.si/?data=..."
```

### 💡 活用メリット
* PC がスリープに入る直前の通知（Discord / Telegram）の中に、自動的に **`⚡ PC遠隔起動リンク`** が添付されます。
* 後から外出先で「PC を起動してファイルを操作したい」「リモートアクセスしたい」と思った際、スマホで通知内のリンクをタップするだけで、自宅 PC を即座に遠隔起動（WoL）できます！
