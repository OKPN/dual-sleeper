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

---

## 📥 大容量ダウンロード / 高トラフィック完了のスマホ自動通知

Steam ゲームのアップデート、大容量ファイル、AIモデルチェックポイントのダウンロードなど、**指定時間（初期値: 10分）以上続いていた高トラフィック通信が収束した際、スマホへ完了通知を送信** できます。

```json
"download_completion_notification": {
  "enabled": true,
  "min_duration_seconds": 600,
  "trigger_condition": "state2_only"
}
```

### ⚙️ 設定オプション
* **`enabled`**: 機能を有効化（`true` / `false`）。
* **`min_duration_seconds`**: 通知対象とする最小通信継続時間（秒単位。`600` で10分間）。
* **`trigger_condition`**: 
  * `"state2_only"` **(推奨・初期値)**: 離席中・画面消灯中 (State 2) にダウンロードが完了した場合のみスマホへ通知。作業中 (State 0/1) の通知をスルーします。
  * `"always"`: 作業中であってもダウンロード完了時に常時通知します。

---

## 🤖 AI生成・処理完了のデスクトップ ✕ スマホ自動通知

ComfyUI、SD-WebUI、PyTorch、llama-server 等による重い生成タスクが終了した際、**Windows画面右下のポップアップ通知（トースト）およびスマホ（Discord / Telegram）へ完了通知を即座に送信** できます。

```json
"ai_completion_notification": {
  "enabled": true,
  "min_duration_seconds": 30,
  "trigger_condition": "always",
  "desktop_toast": true
}
```

### ⚙️ 特長と設定オプション
* **5秒間デバウンス（誤通知防止）**:  
  ステップの切り替わりやバッチ生成の合間で一瞬だけ GPU 使用率がドロップした際に誤通知しないよう、**5 秒間連続で判定が解除された際** に初めて「完了」と判定します。
* **`enabled`**: 機能を有効化（`true` / `false`）。
* **`min_duration_seconds`**: 通知対象とする最小AI処理継続時間（秒単位。`30`秒未満の軽い生成はスルー）。
* **`trigger_condition`**: 
  * `"always"` **(推奨・初期値)**: PC前で別作業中であっても、離席中であっても常に完了通知を発行。
  * `"state2_only"`: 離席消灯中 (State 2) のみ通知。
* **`desktop_toast`**: Windows標準の画面右下トースト通知（`true` / `false`）。PCの前で別タブ作業をしていても「ピコン♪」とポップアップ音で完了が分かります。
