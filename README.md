# Dual Sleeper (デュアル・スリーパー)

**日本語** | [🌐 English](README_EN.md)

**Dual Sleeper** は、`run.bat` をダブルクリックして起動するだけで、放置時にPCを段階的に自動スリープさせ、周辺の雷発生時に休止状態へ自動退避させるインテリジェント電源・防犯管理ツールです。

インストール作業やアカウント登録、通知設定を行わなくても、バックグラウンドで起動しておくだけでユーザーの離席・ゲーム・通話・AI学習・大容量通信・ゲームサーバー接続・周辺の雷発生を判定し、スリープおよび休止保護を実行します。

---

## ユースケースと動作仕様

日常や開発における以下のような状況を自動判定し、モニターの消灯およびPCのスリープを制御します。

1. **通常の離席時**
   * 無操作時間（デフォルト5分）でモニターを消灯し、さらに5分経過後にPC本体をスタンバイ（スリープ）にします。
2. **LINE / Discord / Zoom 等での通話時**
   * **[WASAPI オーディオセッション保護]** 通話中である限り音声ストリームを検知してスリープ移行を阻止します（モニターは消灯）。通話終了後にタイマーが再開し自動スリープします。
3. **YouTube等の動画閲覧時**
   * バッファ通信（State 1 固定しきい値 `network_limit_kbs`）や特定ウィンドウタイトルを検知し、モニター消灯およびスリープ移行を防止します。
4. **大容量ファイルのダウンロード時**
   * Downloadsフォルダ内の一時ファイル（`.crdownload`, `.part`）検知により、ダウンロードが完了するまでスリープ移行を阻止します（モニターは消灯）。
5. **ゲーム起動中の放置時**
   * GPU使用率が 30% 以上であっても、保護対象プロセス（python等）に含まれない場合は放置中と判定し、スリープを実行します。
6. **ゲームプレイ＋配信等の高トラフィック時**
   * モニター消灯中であっても、配信データ（5 Mbps以上の高トラフィック）を検知した場合、スリープ移行を阻止します。
7. **NVIDIA GPUでのAI学習・推論時**
   * GPUを消費しているプロセス名が `python.exe` や `llama-server` 等に該当する場合、処理中と判定してスリープ移行を自動阻止します。
8. **CPU高負荷・Windows Update進行時**
   * 動画エンコードや **Windows Update のインストール (`TiWorker.exe`等)** で CPU 80% 以上の高負荷状態が続く間、スリープ移行を自動阻止して安全に処理を完遂させます。
9. **ゲームサーバー稼働時 (パルワールド・マイクラ等)**
   * 指定ポート (`8211`, `2283`等) への外部/Tailscale/LAN接続がある間、スリープを絶対無効化します。全員離脱で5分後に自動スリープします。
10. **離席中の急な雷雨・豪雨発生時**
    * 画面消灯中（離席中）に端末周辺で雷や豪雨が検知された場合、「休止状態（ハイバネート）」へ自動移行してPCとデータを保護します。

---

## 🚀 はじめかた (Quick Start)

1. **ダウンロードと起動**
   * **新規取得:** `git clone https://github.com/OKPN/dual-sleeper.git` または Zip ダウンロードで取得します。
   * **最新版への更新:** 取得済みの方はフォルダ内で `git pull` を実行して最新版へ更新します。
   * **起動:** フォルダ内の **`run.bat`** をダブルクリックして起動します（Python の事前インストールは不要です）。
2. **通信速度の確認**
   * 起動後、PCを触らず少し放置するとコンソール画面にリアルタイムの通信速度（`Median KB/s`）が表示されます。
3. **設定の調整 (任意)**
   * 必要に応じて `config.json` を開き、画面消灯までの時間（`idle_limit_seconds`）や判定通信速度（`network_limit_kbs`）を調整します。
4. **そのまま放置運用**
   * 調整後はバックグラウンドで起動しておくだけで、全自動で省エネおよび保護を行います。

---

## 📚 各種オプション機能ガイド (Docs)

詳細な設定手順や活用方法については、以下の個別ガイドをご参照ください：

* 🌩️ **[落雷・豪雨自動保護ガイド](docs/lightning_protection.md)**  
  Googleマップの座標登録、自動休止状態設定、リアルタイム雷予報レポートの使い方。
* 📱 **[外部通知 ＆ スマホ双方向リモート操作ガイド](docs/notifications_remote.md)**  
  Discord Webhook 連携、Telegram Bot による `/status` 確認・1秒応答・スマホ遠隔スリープ延長。
* ⚡ **[電源プロファイル自動制御ガイド](docs/power_plan_control.md)**  
  放置消灯時の「省電力」自動切替、ゲーム時「究極のパフォーマンス」、AI/CPU高負荷（Windows Update）時の全自動プロファイル昇格。
* 🎮 **[ゲームサーバー保護ガイド](docs/game_server_protection.md)**  
  Palworld、Minecraft、HTTP 等のポート監視設定、複数ポート配列、Tailscale/LAN 対応。

---

## ⚙️ 設定ファイル一覧 (`config.json`)

| 分類 | パラメータ名 | 初期値 | 単位 | 説明 |
| :--- | :--- | :--- | :--- | :--- |
| **放置判定** | `idle_limit_seconds` | `300` | 秒 | 物理的な無操作（キー・マウス）で消灯判定を開始する時間 (5分)。 |
| | `network_check_duration_seconds` | `30` | 秒 | 放置判定のため通信速度を集計・監視する秒数。 |
| | `check_interval_seconds` | `5` | 秒 | 監視ループの実行間隔。 |
| | `standby_after_monitor_off_seconds` | `300` | 秒 | モニター消灯(State 2)後、システムがスリープするまでの待機時間 (5分)。 |
| | `force_monitor_off_idle_seconds` | `900` | 秒 | 無操作が長期間継続した際に強制消灯する時間 (15分)。 |
| | `force_sleep_on_dialog` | `false` | bool | 保存確認ダイアログ等でスリープ拒否された際、強制スリープするか。 |
| | `hibernate_start_hour` / `end_hour` | `0` / `0` | 時 | スリープの代わりに自動で休止状態(S4)にする時間帯（0で無効）。 |
| | `no_sleep_start_hour` / `end_hour` | `0` / `0` | 時 | スリープ・消灯を完全に禁止する時間帯（0で無効）。 |
| **通信判定** | `network_limit_kbs` | `30.0` | KB/s | **消灯前(State 1)** の通信判定しきい値（未登録動画の点灯維持用）。 |
| | `dynamic_network_margin_kbs` | `30.0` | KB/s | **消灯中(State 2)** の動的ベースライン通信量に加算するマージン速度。 |
| | `high_network_limit_kbs` | `625.0` | KB/s | 配信中などの高トラフィックとみなす保護しきい値（5 Mbps相当）。 |
| **ゲーム保護** | `game_server_protection.enabled` | `false` | bool | パルワールド等のゲーム専用ポート監視保護（初期無効）。 |
| | `game_server_protection.ports` | `[8211, 2283]` | 配列/数値 | プレイヤー接続を物理監視する対象ポート番号リスト。 |
| **通知・遠隔** | `discord_webhook_url` | `""` | URL | Discord Webhook URL。 |
| | `telegram_bot_token` / `chat_id` | `""` / `""` | 文字列 | Telegram Bot 連携トークン ＆ チャットID。 |
| | `wol_url` | `""` | URL | Web WoL (CloudWaker) による PC 遠隔起動 URL。 |
| | `download_completion_notification.enabled` | `false` | bool | 巨大DL・高通信完了時の自動通知（初期無効）。 |
| | `download_completion_notification.min_duration_seconds` | `600` | 秒 | DL完了通知の対象とする最小通信継続時間（10分）。 |
| | `download_completion_notification.trigger_condition` | `"state2_only"` | 文字列 | 通知条件（`"state2_only"` 離席中のみ, `"always"` 常時）。 |
| | `sleep_pending_seconds` | `30` | 秒 | スリープ直前の予告通知のカウントダウン表示時間。 |
| | `desktop_notification` | `"weather_only"` | 文字列 | Windows標準トースト通知（`"off"`, `"weather_only"`, `"all"`）。 |
| **復帰誤作動** | `wakeup_mouse_distance_px` | `100` | px | スリープ復帰直後、マウスの微振動を無視するピクセル距離。 |
| | `wakeup_mouse_grace_seconds` | `20` | 秒 | スリープ復帰後、OSの内部ノイズを無視する保護猶予時間。 |
| | `wakeup_active_threshold_seconds` | `5` | 秒 | 復帰後、連続で操作があった場合にアクティブとみなす秒数。 |
| **電源制御** | `power_plan_control.enabled` | `false` | bool | Windows電源プラン（省電力/バランス/高パフォーマンス）自動切替。 |
| | `power_plan_control.cpu_heavy_threshold_percent` | `80` | % | CPU高負荷保護（Windows Update等）を発動する使用率しきい値。 |
| | `power_plan_control.cpu_heavy_duration_seconds` | `5` | 秒 | CPU高負荷判定に必要な継続時間。 |
| | `power_plan_control.power_saver_on_idle_monitor_off` | `true` | bool | 放置消灯時に自動で「省電力」プランへ切り替えるか。 |
| | `power_plan_control.ultimate_on_game` | `true` | bool | GPUゲーム起動時に「究極のパフォーマンス」へ昇格するか。 |
| | `power_plan_control.high_performance_on_ai` | `true` | bool | AI推論・学習検知時に「高パフォーマンス」へ昇格するか。 |
| | `power_plan_control.high_performance_on_cpu` | `true` | bool | CPU高負荷検知時に「高パフォーマンス」へ昇格するか。 |
| **落雷保護** | `lightning_protection.enabled` | `false` | bool | 近隣の落雷・豪雨接近時の自動休止状態退避機能。 |
| | `lightning_protection.location` | `"35.681236, 139.767125"` | 緯度,経度 | PC設置場所の Google マップ座標（東京駅例）。 |
| | `lightning_protection.auto_hibernate` | `"state2_only"` | 文字列 | 自動休止の動作条件（`"state2_only"`, `"always"`, `"off"`）。 |
| **GPU保護** | `gpu_protect_processes` | `["python.exe", ...]` | リスト | AI学習・推論など保護対象とするGPUプロセス名一覧。 |
| | `gpu_protect_min_vram_mb` | `4000` | MB | AI保護プロセスが処理中か判断する最小VRAM消費量しきい値。 |
| | `gpu_limit_percent` | `40` | ％ | 保護対象プロセス実行時にスリープを阻止するGPU使用率しきい値。 |
| | `game_gpu_threshold_percent` | `30` | ％ | ゲーム等のGPU使用放置とみなす判定しきい値。 |
| | `keep_awake_window_titles` | `["youtube:20", ...]`| リスト | 点灯・稼働延長を行うウィンドウタイトルと時間（分）。 |
| **サーバモード**| `server_mode` | `"off"` | 文字列 | 高速画面消灯サーバモード（`"off"`, `"desktop"`, `"always"`）。 |
| | `server_mode_standby_delay_seconds` | `600` | 秒 | サーバモード時にモニター消灯後スリープするまでの遅延秒数。 |

---

## 📱 Telegram コマンド一覧

Telegram Bot から以下のコマンドを送信して、スマホから遠隔操作・状態確認が行えます：

* `/status` : 現在の状態、通信速度、**動的通信上限 (ベース ＋ マージン)**、**ゲームサーバー接続数**、GPU・電源プランをリアルタイム表示。
* `/sleep` : 次回スリープ移行時の挙動をトグル切り替え（`スリープ` / `休止状態` / `自動`）。
* `/server` : 高速消灯サーバーモードの切替（`オフ` / `デスクトップ時` / `常時`）。
* `/weather` : 端末周辺の現在の気象・落雷警報状況および 12時間 CAPE 雷予報を表示。

---

## ❓ よくある質問と回答 (Q&A)

### Q. 無操作時の消灯までの時間を長くする（または短くする）には？
**A.** `config.json` の **`"idle_limit_seconds"`**（単位: 秒）の数値を変更します。  
例えば、無操作から画面消灯までの時間を 10 分間にしたい場合は `"idle_limit_seconds": 600` に設定します。

### Q. 消灯後のスリープまでの時間を長くする（または短くする）には？
**A.** `config.json` の **`"standby_after_monitor_off_seconds"`**（単位: 秒）の数値を変更します。  
例えば、画面が消灯してからスリープするまでの待機時間を 10 分間にしたい場合は `"standby_after_monitor_off_seconds": 600` に設定します。

### Q. 特定のアプリを使用時に点灯させ続けるには？
**A.** `config.json` の **`"keep_awake_window_titles"`** リストに、該当アプリの使用時にウィンドウタイトル欄へ必ず表示されるキーワードを登録します。  
*(※ オプションで `:分数` を付けると指定時間延長できます)*  
例: `"keep_awake_window_titles": ["youtube:20", "obs:360", "電卓"]`

### Q. 特定の GPU 利用アプリを利用中に PC をスリープさせないようにするには？
**A.** `config.json` の **`"gpu_protect_processes"`** リストに、対象アプリの exe 名（実行ファイル名）を追加登録します。  
例: `"gpu_protect_processes": ["python.exe", "sd-webui.exe", "lmstudio.exe"]`

---

## バージョン履歴 (Changelog)

### v1.4.0 (2026-08-02)
* **2 段構え通信判定設計 (Two-Tier Architecture):**  
  消灯前(State 1)は未登録動画の点灯維持用固定しきい値 (`network_limit_kbs`: 30 KB/s)、消灯中(State 2)は背景ノイズ自動吸収用の動的上限 (`ベース ＋ マージン`) に役割を完全分離。
* **3 秒持続確認タイマー (3-Second Debouncing):**  
  ミリ秒〜1秒の「ぷっぷ」というパルスノイズを100%無視し、Immich等の 3秒以上持続した操作通信のみを確実に検知してスリープタイマーをリセット。
* **ゲームサーバー保護機能 (`game_server_protection`):**  
  Palworld、Minecraft、HTTP 等、複数ポート（`ports: [8211, 2283]`）の外部/Tailscale/LAN 接続を物理検知。プレイヤー接続中はスリープを絶対無効化、全員離脱で5分後に自動スリープ。
* **ドキュメントの最適化 (Docs フォルダ分離):**  
  Quick Start を超シンプル 4 ステップに凝縮し、落雷保護・通知リモート・電源制御・ゲームサーバー保護の詳細ガイドを `docs/` ディレクトリへ構造化分離。

### v1.3.0 (2026-07-29)
* **トリプルハイブリッド動的通信上限エンジンの導入:**  
  移動中央値 (Moving Median) と下位サンプルによる「平常時ベースライン自動学習」を導入。

### v1.0.0 (2026-07-22) - ファースト・メジャーリリース
* **ポータブル化:** Windows用 組み込み型 Python 3.11 (`python_embed/`) 同梱。

---

## クレジット ＆ 貢献者 (Credits & Contributors)

* **Project Lead / Developer:** OKPN
* **AI Co-Developer:** [Google Antigravity](https://deepmind.google/) (Google DeepMind)
* **License:** Distributed under the [MIT License](LICENSE). Copyright (c) 2026 OKPN.
* **Weather & Forecast API:** Weather data provided by [Open-Meteo.com](https://open-meteo.com/) ([CC BY 4.0 License](https://creativecommons.org/licenses/by/4.0/))
