# Dual Sleeper (English)

[🌐 日本語 (Japanese)](README.md) | **English**

> **Dual Sleeper** is a **context-aware power management application** that intelligently controls monitor screen-off and system sleep based on the user's real-time activities and behavior in front of the PC.

Simply double-click `run.bat` to launch. It automatically manages screen dimming, system sleep, and emergency hibernation during nearby lightning storms for AI workstations, gaming PCs, and remote servers.

No complex installation or account registration required. It runs quietly in the background, automatically detecting user idleness, unlisted video streaming, game server connections, voice calls, AI training, heavy CPU tasks (including Windows Update), and nearby lightning activity.

---

## 🌟 Key Features & Use Cases

1. **Two-Tier Network Architecture (State 1 & State 2)**
   * **State 1 (Screen On / Pre-Off):** Protects active unlisted video streams (TVer, NicoNico, web players) using a fixed threshold (`network_limit_kbs`: 30.0 KB/s) and moving median filter to prevent unexpected screen dimming.
   * **State 2 (Screen Off / Standby Wait):** Applies a dynamic baseline + margin (`dynamic_network_margin_kbs`: 30.0 KB/s) to absorb background cloud sync noise and smoothly transition to system sleep.
2. **3-Second Debouncing Filter**
   * Sub-second pulse noise (<3 seconds) is 100% ignored. Only sustained activity (>=3 seconds, such as Immich photo browsing or file transfers) resets the sleep countdown.
3. **Game Server Protection (`game_server_protection`)**
   * Monitors active external client connections on target ports (e.g., Palworld `8211`, Minecraft `25565`, Immich `2283`) over WAN, Tailscale, or LAN.
   * **Players Connected (>=1):** Absolutely blocks system sleep (keeps timer reset).
   * **All Players Disconnected (0):** Automatically resumes standby countdown and sleeps after 5 minutes.
4. **AI, CPU & Windows Update Protection**
   * Prevents system sleep while NVIDIA GPUs are running AI processes (`python.exe`, `llama-server`) with >=4GB VRAM.
   * Protects system sleep during heavy CPU tasks (>=80% CPU usage) such as video encoding, compilation, and **Windows Update installation (`TiWorker.exe`)**.
5. **WASAPI Audio & Large Download Protection**
   * Keeps the system awake during active voice calls (Discord, LINE, Zoom) via Windows Core Audio API.
   * Detects temporary download files (`.crdownload`, `.part`) to delay sleep until downloads complete.
6. **Lightning & Heavy Rain Protection (Open-Meteo Integration)**
   * Automatically escalates standby to **Hibernation (S4: Power Off)** when lightning, heavy rain, or high CAPE index is detected near your location while away.

---

## 🚀 Quick Start

1. **Download & Clone**
   * **New Installation:** `git clone -b experiment https://github.com/OKPN/dual-sleeper.git` (or download ZIP).
   * **Update Existing:** Run `git pull` inside the folder.
2. **Run**
   * Double-click **`run.bat`** (Embedded Python 3.11 included; no Python installation needed).
3. **Check Network Speed**
   * Leave the PC untouched for a moment to observe your baseline network speed (`Median KB/s`) in the console.
4. **Enjoy Automated Protection**
   * Runs in the background with zero setup required out of the box!

---

## 📚 Detailed Documentation (Docs)

* 🌩️ **[Lightning Protection Guide](docs/lightning_protection.md)**  
  Google Maps coordinate setup, auto-hibernation configuration, and weather reports.
* 📱 **[Notifications & Remote Control Guide](docs/notifications_remote.md)**  
  Discord Webhook, Telegram Bot commands (`/status`, 1-second response), and Web WoL (CloudWaker) one-tap wake-up links.
* ⚡ **[Power Plan Control Guide](docs/power_plan_control.md)**  
  Dynamic switching between Power Saver (idle), Ultimate Performance (gaming), and High Performance (AI / CPU).
* 🎮 **[Game Server Protection Guide](docs/game_server_protection.md)**  
  Port monitoring settings for single or multiple ports over Tailscale and LAN.

---

## ⚙️ Configuration Reference (`config.json`)

| Category | Parameter | Default | Unit | Description |
| :--- | :--- | :--- | :--- | :--- |
| **Idle** | `idle_limit_seconds` | `300` | sec | Physical inactivity time before screen off (5 min). |
| | `network_check_duration_seconds` | `30` | sec | Network speed monitoring window duration. |
| | `check_interval_seconds` | `5` | sec | Main loop polling interval. |
| | `standby_after_monitor_off_seconds` | `300` | sec | Standby delay after screen turns off (5 min). |
| | `force_monitor_off_idle_seconds` | `900` | sec | Forced screen off timeout for long idle (15 min). |
| **Network** | `network_limit_kbs` | `30.0` | KB/s | **State 1** fixed network limit for video stream protection. |
| | `dynamic_network_margin_kbs` | `30.0` | KB/s | **State 2** margin added above dynamic baseline. |
| | `high_network_limit_kbs` | `625.0` | KB/s | High-traffic threshold for streaming (5 Mbps). |
| **Game Server**| `game_server_protection.enabled` | `false` | bool | Enable active port connection protection. |
| | `game_server_protection.ports` | `[8211, 2283]` | array | Target port list (Palworld, Minecraft, Immich). |
| **Notifications**| `discord_webhook_url` | `""` | URL | Discord Webhook URL. |
| | `telegram_bot_token` / `chat_id` | `""` / `""` | string | Telegram Bot Token and Chat ID. |
| | `wol_url` | `""` | URL | Web WoL (CloudWaker) one-tap wake-up URL. |
| **Power Plan** | `power_plan_control.enabled` | `false` | bool | Dynamic Windows Power Scheme switching. |
| | `power_plan_control.cpu_heavy_threshold_percent` | `80` | % | CPU load threshold for Windows Update / encoding. |
| **Lightning** | `lightning_protection.enabled` | `false` | bool | Emergency hibernation on nearby lightning alert. |
| | `lightning_protection.location` | `"35.681236, 139.767125"` | lat,lon | Coordinates for Open-Meteo weather scans. |

---

## ❓ Frequently Asked Questions (Q&A)

### Q. How to change the screen dimming timer?
**A.** Modify `"idle_limit_seconds"` (in seconds) in `config.json`. For example, set `"idle_limit_seconds": 600` for 10 minutes.

### Q. How to change the sleep countdown timer after screen off?
**A.** Modify `"standby_after_monitor_off_seconds"` (in seconds) in `config.json`.

### Q. How to keep the screen awake for specific applications?
**A.** Add window title keywords to `"keep_awake_window_titles"` in `config.json` (e.g. `["youtube:20", "obs:360"]`).

### Q. How to prevent sleep during specific GPU applications?
**A.** Add executable names to `"gpu_protect_processes"` in `config.json` (e.g. `["python.exe", "sd-webui.exe"]`).

---

## Credits & License

* **Project Lead / Developer:** OKPN
* **AI Co-Developer:** [Google Antigravity](https://deepmind.google/) (Google DeepMind)
* **License:** Distributed under the [MIT License](LICENSE). Copyright (c) 2026 OKPN.
* **Weather API:** Weather data provided by [Open-Meteo.com](https://open-meteo.com/) ([CC BY 4.0](https://creativecommons.org/licenses/by/4.0/))
