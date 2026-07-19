import ctypes
import json
import os
import sys
import time
import datetime
import socket
import urllib.request
import urllib.error
import subprocess
import psutil
import glob
import msvcrt
import threading

# Windows API 定義
class LASTINPUTINFO(ctypes.Structure):
    _fields_ = [("cbSize", ctypes.c_uint), ("dwTime", ctypes.c_uint)]

class POINT(ctypes.Structure):
    _fields_ = [("x", ctypes.c_long), ("y", ctypes.c_long)]

# GUIDの定義 (Downloadsフォルダの自動取得用)
class GUID(ctypes.Structure):
    _fields_ = [
        ("Data1", ctypes.c_ulong),
        ("Data2", ctypes.c_ushort),
        ("Data3", ctypes.c_ushort),
        ("Data4", ctypes.c_ubyte * 8)
    ]

# FOLDERID_Downloads の GUID ({374DE290-123F-4565-9164-39C4925E467B})
FOLDERID_Downloads = GUID(
    0x374DE290, 0x123F, 0x4565,
    (ctypes.c_ubyte * 8)(0x91, 0x64, 0x39, 0xC4, 0x92, 0x5E, 0x46, 0x7B)
)

HWND_BROADCAST = 0xFFFF
WM_SYSCOMMAND = 0x0112
SC_MONITORPOWER = 0xF170

# グローバルステータス変数 (Telegramリモートスレッド共有用)
force_power_mode = None
current_state_num = 0
current_idle_sec = 0.0
current_net_speed = 0.0
current_gpu_util = 0
telegram_offset = 0

# Telegram割り込みスリープ延長用グローバル変数
is_sleep_pending = False
telegram_extend_request = False

def get_idle_duration():
    """最後にマウス・キーボード操作があってからの経過時間（秒）を取得します。"""
    lii = LASTINPUTINFO()
    lii.cbSize = ctypes.sizeof(LASTINPUTINFO)
    if ctypes.windll.user32.GetLastInputInfo(ctypes.byref(lii)):
        tick_count = ctypes.windll.kernel32.GetTickCount()
        # 32ビット符号なし整数のオーバーフローに対応するためのマスク処理
        millis = (tick_count - lii.dwTime) & 0xFFFFFFFF
        return millis / 1000.0
    return 0.0

def get_last_input_time_raw():
    """最後の入力イベントのタイムスタンプ（TickCount）を取得します。"""
    lii = LASTINPUTINFO()
    lii.cbSize = ctypes.sizeof(LASTINPUTINFO)
    if ctypes.windll.user32.GetLastInputInfo(ctypes.byref(lii)):
        return lii.dwTime
    return 0

def get_mouse_position():
    """現在のマウスカーソルの座標 (x, y) を取得します。"""
    pt = POINT()
    if ctypes.windll.user32.GetCursorPos(ctypes.byref(pt)):
        return pt.x, pt.y
    return 0, 0

def get_active_window_title():
    """現在アクティブなウィンドウのタイトルを取得します（小文字で返却）。"""
    try:
        hwnd = ctypes.windll.user32.GetForegroundWindow()
        length = ctypes.windll.user32.GetWindowTextLengthW(hwnd)
        if length > 0:
            buf = ctypes.create_unicode_buffer(length + 1)
            ctypes.windll.user32.GetWindowTextW(hwnd, buf, length + 1)
            return buf.value.lower()
    except Exception:
        pass
    return ""

def is_desktop_active():
    """現在デスクトップ画面またはタスクバーがアクティブウィンドウになっているか判定します。"""
    try:
        hwnd = ctypes.windll.user32.GetForegroundWindow()
        class_name = ctypes.create_unicode_buffer(256)
        ctypes.windll.user32.GetClassNameW(hwnd, class_name, 256)
        name = class_name.value
        # Progman, WorkerW (デスクトップ背景/アイコン), Shell_TrayWnd (タスクバー)
        return name in ("Progman", "WorkerW", "Shell_TrayWnd")
    except Exception:
        pass
    return False

def get_server_mode_type(config):
    """設定された server_mode の値を解析して、対応するモード文字列を返します。
    後方互換性のため True/False も判定します。
    """
    val = config.get("server_mode", "off")
    if val is True:
        return "desktop"
    if val is False:
        return "off"
    
    val_str = str(val).strip().lower()
    if val_str in ("desktop", "always", "off"):
        return val_str
    return "off"

def turn_off_monitor():
    """モニターの電源をオフにします。"""
    ctypes.windll.user32.PostMessageW(HWND_BROADCAST, WM_SYSCOMMAND, SC_MONITORPOWER, 2)

def turn_on_monitor():
    """モニターの電源をオンにし、マウス入力をシミュレートして復帰を促します。"""
    ctypes.windll.user32.PostMessageW(HWND_BROADCAST, WM_SYSCOMMAND, SC_MONITORPOWER, -1)
    
    # 確実に復帰させるため、マウスカーソルを少し動かして戻す
    pt = POINT()
    if ctypes.windll.user32.GetCursorPos(ctypes.byref(pt)):
        ctypes.windll.user32.SetCursorPos(pt.x + 1, pt.y + 1)
        time.sleep(0.05)
        ctypes.windll.user32.SetCursorPos(pt.x, pt.y)

def go_to_sleep(hibernate=False):
    """システムをスタンバイ（スリープ）または休止状態（ハイバネート）にします。"""
    try:
        # SetSuspendState(hibernate, force, disableWakeup)
        # hibernate=True (1) で休止状態、False (0) でスリープ
        if hibernate:
            res = ctypes.windll.powrprof.SetSuspendState(1, 0, 0)
            # OS側で休止状態が無効化されているなどの理由で失敗した場合(戻り値が0)、通常のスタンバイにフォールバック
            if not res:
                print(f"{get_timestamp()} [警告] 休止状態の実行に失敗しました。通常のスタンバイ（スリープ）を実行します。")
                ctypes.windll.powrprof.SetSuspendState(0, 0, 0)
        else:
            ctypes.windll.powrprof.SetSuspendState(0, 0, 0)
    except Exception as e:
        print(f"{get_timestamp()} [警告] 電源状態の変更に失敗しました: {e}")

def is_hibernate_time(start_hour, end_hour):
    """現在時刻が休止状態（ハイバネート）を適用する時間帯にあるか判定します。"""
    if start_hour is None or end_hour is None:
        return False
    if start_hour == 0 and end_hour == 0:
        return False
    
    now = datetime.datetime.now()
    current_hour = now.hour
    
    if start_hour <= end_hour:
        # 同一日の範囲 (例: 0:00 - 7:00)
        return start_hour <= current_hour < end_hour
    else:
        # 日をまたぐ範囲 (例: 23:00 - 6:00)
        return current_hour >= start_hour or current_hour < end_hour

def is_no_sleep_time(start_hour, end_hour):
    """現在時刻が「スリープ禁止（モニター消灯のみ許可）」を適用する時間帯にあるか判定します。"""
    if start_hour is None or end_hour is None:
        return False
    if start_hour == 0 and end_hour == 0:
        return False
    
    now = datetime.datetime.now()
    current_hour = now.hour
    
    if start_hour <= end_hour:
        # 同一日の範囲 (例: 12:00 - 18:00)
        return start_hour <= current_hour < end_hour
    else:
        # 日をまたぐ範囲 (例: 22:00 - 6:00)
        return current_hour >= start_hour or current_hour < end_hour

def get_computer_name():
    """PC名を取得します。"""
    return socket.gethostname()

def get_downloads_folder():
    """Windows APIから、現在のDownloadsフォルダの絶対パスを取得します。"""
    buf = ctypes.c_wchar_p()
    res = ctypes.windll.shell32.SHGetKnownFolderPath(
        ctypes.byref(FOLDERID_Downloads), 0, None, ctypes.byref(buf)
    )
    if res == 0:
        path = buf.value
        ctypes.windll.ole32.CoTaskMemFree(buf)
        return path
    return os.path.join(os.path.expanduser("~"), "Downloads")

def is_downloading_active(downloads_dir):
    """ダウンロードフォルダ内にブラウザの一時ファイルが存在するかチェックします。"""
    if not downloads_dir or not os.path.exists(downloads_dir):
        return False
    crdownload_files = glob.glob(os.path.join(downloads_dir, "*.crdownload"))
    part_files = glob.glob(os.path.join(downloads_dir, "*.part"))
    return (len(crdownload_files) + len(part_files)) > 0

def send_discord_notification(webhook_url, message):
    """DiscordのWebhookにメッセージを送信します。"""
    if not webhook_url:
        return
    
    payload = json.dumps({"content": message}).encode("utf-8")
    req = urllib.request.Request(
        webhook_url,
        data=payload,
        headers={"User-Agent": "Mozilla/5.0", "Content-Type": "application/json"}
    )
    try:
        # タイムアウトを15秒に設定して送信
        with urllib.request.urlopen(req, timeout=15) as response:
            pass
    except Exception as e:
        print(f"\n{get_timestamp()} [警告] Discord通知の送信に失敗しました: {e}")

def send_telegram_notification(bot_token, chat_id, message):
    """TelegramのBot APIを使ってメッセージを送信します。"""
    if not bot_token or not chat_id:
        return
        
    url = f"https://api.telegram.org/bot{bot_token}/sendMessage"
    payload = json.dumps({
        "chat_id": chat_id,
        "text": message,
        "parse_mode": "Markdown"
    }).encode("utf-8")
    req = urllib.request.Request(
        url,
        data=payload,
        headers={"User-Agent": "Mozilla/5.0", "Content-Type": "application/json"}
    )
    try:
        # タイムアウトを15秒に設定して送信（ネットワーク遅延に対応）
        with urllib.request.urlopen(req, timeout=15) as response:
            pass
    except Exception as e:
        print(f"\n{get_timestamp()} [警告] Telegram通知の送信に失敗しました: {e}")

def send_notifications(config, message):
    """設定されているすべての通知サービス（Discord, Telegram）にメッセージを送信します。"""
    # Discord
    webhook_url = config.get("discord_webhook_url", "")
    if webhook_url:
        send_discord_notification(webhook_url, message)
        
    # Telegram
    bot_token = config.get("telegram_bot_token", "")
    chat_id = config.get("telegram_chat_id", "")
    if bot_token and chat_id:
        send_telegram_notification(bot_token, chat_id, message)

def get_gpu_status(protect_processes):
    """
    NVIDIA GPUの使用率(%) と、現在GPUを使用している保護対象プロセスの有無を判定します。
    戻り値: (gpu_utilization_percent, is_protect_process_active)
    """
    gpu_util = 0
    protect_active = False
    
    if not protect_processes:
        return 0, False
        
    try:
        # 1. GPU使用率を取得
        util_output = subprocess.check_output(
            ["nvidia-smi", "--query-gpu=utilization.gpu", "--format=csv,noheader,nounits"],
            shell=True,
            stderr=subprocess.DEVNULL
        ).decode("utf-8").strip()
        gpu_util = int(util_output)
        
        # 2. 現在GPUを使用しているプロセス名一覧を取得
        proc_output = subprocess.check_output(
            ["nvidia-smi", "--query-compute-apps=process_name", "--format=csv,noheader"],
            shell=True,
            stderr=subprocess.DEVNULL
        ).decode("utf-8").strip()
        
        if proc_output:
            active_procs = [p.strip().lower() for p in proc_output.split("\n") if p.strip()]
            for protect_p in protect_processes:
                p_name_lower = protect_p.lower()
                for active_p in active_procs:
                    if p_name_lower in active_p:
                        protect_active = True
                        break
                if protect_active:
                    break
    except Exception:
        # nvidia-smiが実行できない環境では0%とみなし、保護も無効とする
        pass
        
    return gpu_util, protect_active

class NetworkMonitor:
    def __init__(self):
        self.last_io_by_nic = self._get_filtered_io()
        self.last_time = time.time()

    def _get_filtered_io(self):
        """Tailscaleなどの特定アダプターを除外した、全体の送受信バイト数の合計を返します。"""
        try:
            io_dict = psutil.net_io_counters(pernic=True)
            total_sent = 0
            total_recv = 0
            for nic_name, io in io_dict.items():
                # アダプター名に "tailscale" (大文字小文字無視) が含まれる場合はスキップ
                if "tailscale" in nic_name.lower():
                    continue
                total_sent += io.bytes_sent
                total_recv += io.bytes_recv
            return {"bytes_sent": total_sent, "bytes_recv": total_recv}
        except Exception as e:
            # エラー発生時は全体の通信量でフォールバック
            io = psutil.net_io_counters()
            return {"bytes_sent": io.bytes_sent, "bytes_recv": io.bytes_recv}

    def get_speed(self):
        """前回の呼び出しからの平均通信速度（KB/s）を計算して返します（Tailscale除外）。"""
        current_io = self._get_filtered_io()
        current_time = time.time()
        elapsed = current_time - self.last_time
        
        if elapsed <= 0:
            return 0.0
        
        sent = current_io["bytes_sent"] - self.last_io_by_nic["bytes_sent"]
        recv = current_io["bytes_recv"] - self.last_io_by_nic["bytes_recv"]
        total_kb = (sent + recv) / 1024.0
        speed = total_kb / elapsed
        
        self.last_io_by_nic = current_io
        self.last_time = current_time
        return speed

def disable_quick_edit():
    """Windowsコンソールの簡易編集モード(QuickEdit Mode)を無効化し、誤クリックによるフリーズを防止します。"""
    try:
        kernel32 = ctypes.windll.kernel32
        # 標準入力のハンドルを取得 (STD_INPUT_HANDLE = -10)
        h_input = kernel32.GetStdHandle(-10)
        mode = ctypes.c_uint()
        if kernel32.GetConsoleMode(h_input, ctypes.byref(mode)):
            # ENABLE_QUICK_EDIT_MODE (0x0040) を取り除く
            # ENABLE_EXTENDED_FLAGS (0x0080) も一緒に設定して適用する
            new_mode = (mode.value & ~0x0040) | 0x0080
            kernel32.SetConsoleMode(h_input, new_mode)
    except Exception:
        pass

def load_config():
    """設定ファイルを読み込みます。存在しない場合はデフォルト値を返します。"""
    default_config = {
        "idle_limit_seconds": 10,
        "network_limit_kbs": 20.0,
        "network_check_duration_seconds": 10,
        "check_interval_seconds": 5,
        "standby_after_monitor_off_seconds": 10,
        "hibernate_start_hour": 0,
        "hibernate_end_hour": 0,
        "no_sleep_start_hour": 0,
        "no_sleep_end_hour": 0,
        "force_monitor_off_idle_seconds": 900,
        "discord_webhook_url": "",
        "telegram_bot_token": "",
        "telegram_chat_id": "",
        "sleep_pending_seconds": 30,
        "wakeup_mouse_distance_px": 100,
        "wakeup_mouse_grace_seconds": 20,
        "wakeup_active_threshold_seconds": 5,
        "gpu_protect_processes": ["python.exe", "python"],
        "gpu_limit_percent": 10,
        "high_network_limit_kbs": 625.0,
        "server_mode": "off",
        "server_mode_standby_delay_seconds": 600
    }
    config_path = os.path.join(os.path.dirname(__file__), "config.json")
    if os.path.exists(config_path):
        try:
            with open(config_path, "r", encoding="utf-8") as f:
                lines = f.readlines()
            
            # コメント行(// や #)を除去してからJSONとして読み込む
            clean_lines = []
            for line in lines:
                stripped = line.strip()
                if stripped.startswith("//") or stripped.startswith("#"):
                    continue
                if "//" in line:
                    line = line.split("//")[0]
                elif "#" in line:
                    line = line.split("#")[0]
                clean_lines.append(line)
                
            config_content = "".join(clean_lines)
            config = json.loads(config_content)
            
            # デフォルト値のキーが欠落している場合に補完
            for key, val in default_config.items():
                if key not in config:
                    config[key] = val
            return config
        except Exception as e:
            print(f"設定ファイルの読み込みに失敗しました。デフォルト値を使用します。エラー: {e}")
    return default_config

def save_config(config):
    """設定オブジェクトを config.json に上書き保存（永続化）します。"""
    config_path = os.path.join(os.path.dirname(__file__), "config.json")
    try:
        with open(config_path, "w", encoding="utf-8") as f:
            json.dump(config, f, indent=2, ensure_ascii=False)
    except Exception as e:
        print(f"[エラー] 設定の保存に失敗しました: {e}")

def get_timestamp():
    """現在の時刻を [MM/DD HH:MM:SS] フォーマットの文字列で返します。"""
    return datetime.datetime.now().strftime("[%m/%d %H:%M:%S]")

def telegram_worker(bot_token, chat_id, pc_name):
    """Telegramのロングポーリング受信を専門に行う非同期ワーカースレッドです。"""
    global force_power_mode, telegram_offset
    global current_state_num, current_idle_sec, current_net_speed, current_gpu_util
    global is_sleep_pending, telegram_extend_request
    
    if not bot_token or not chat_id:
        return
        
    print(f"{get_timestamp()} [システム] Telegramリモート受信スレッドを起動しました。(ロングポーリング監視)")
    
    # 起動時の古い過去ログを処理しないよう、最新のupdate_idを取得してoffsetを初期化
    try:
        url = f"https://api.telegram.org/bot{bot_token}/getUpdates"
        req = urllib.request.Request(
            url,
            data=json.dumps({"limit": 1, "timeout": 0}).encode("utf-8"),
            headers={"User-Agent": "Mozilla/5.0", "Content-Type": "application/json"}
        )
        with urllib.request.urlopen(req, timeout=10) as response:
            res_data = json.loads(response.read().decode("utf-8"))
            if res_data.get("ok") and res_data.get("result"):
                telegram_offset = res_data["result"][-1]["update_id"] + 1
    except Exception:
        pass

    while True:
        try:
            url = f"https://api.telegram.org/bot{bot_token}/getUpdates"
            payload = {
                "offset": telegram_offset,
                "timeout": 30, # 30秒間Telegramサーバー側で接続を維持（ロングポーリング）
                "allowed_updates": ["message"]
            }
            req = urllib.request.Request(
                url,
                data=json.dumps(payload).encode("utf-8"),
                headers={"User-Agent": "Mozilla/5.0", "Content-Type": "application/json"}
            )
            # タイムアウトは接続維持時間(30秒)より少し長めの40秒を設定
            with urllib.request.urlopen(req, timeout=40) as response:
                res_data = json.loads(response.read().decode("utf-8"))
                if not res_data.get("ok"):
                    time.sleep(5)
                    continue
                    
                for update in res_data.get("result", []):
                    telegram_offset = update["update_id"] + 1
                    message = update.get("message")
                    if not message:
                        continue
                        
                    # セキュリティ：登録されたあなたのChat IDからのメッセージのみ処理する
                    sender_chat_id = str(message.get("chat", {}).get("id", ""))
                    if sender_chat_id != str(chat_id):
                        continue
                        
                    text = message.get("text", "").strip()
                    if not text:
                        continue
                        
                    # ===== スリープ警告中（カウントダウン30秒中）の割り込み処理 =====
                    if is_sleep_pending:
                        telegram_extend_request = True
                        reply_text = f"🟢 **[{pc_name}]** スリープ移行を一時的に10分間延長しました。(モニター消灯状態維持)"
                        print(f"\n{get_timestamp()} [リモート設定] Telegramから割り込み入力を受信したため、スリープ移行を10分間延長します。")
                        send_telegram_notification(bot_token, chat_id, reply_text)
                        continue

                    # 通常時のコマンド解析 (大文字小文字を区別せず前置部分を取得)
                    text_lower = text.lower()
                    text_parts = text_lower.split()
                    cmd = text_parts[0]
                    
                    reply_text = ""
                    if cmd in ("/sleep", "sleep"):
                        force_power_mode = "sleep"
                        reply_text = f"🟢 **[{pc_name}]** 次回終了モードを強制的に「スタンバイ (スリープ)」に予約しました。(復帰時に解除)"
                        print(f"\n{get_timestamp()} [リモート予約] Telegramから「スタンバイ (スリープ)」の強制予約を受信しました。")
                    elif cmd in ("/hibernate", "hibernate"):
                        force_power_mode = "hibernate"
                        reply_text = f"🟢 **[{pc_name}]** 次回終了モードを強制的に「休止状態 (ハイバネート)」に予約しました。(復帰時に解除)"
                        print(f"\n{get_timestamp()} [リモート予約] Telegramから「休止状態 (ハイバネート)」の強制予約を受信しました。")
                    elif cmd in ("/cancel", "cancel"):
                        force_power_mode = None
                        reply_text = f"🟢 **[{pc_name}]** 電源予約をキャンセルしました。(通常の時間帯制御に戻ります)"
                        print(f"\n{get_timestamp()} [リモート予約] Telegramから予約キャンセルを受信しました。")
                    elif cmd in ("/status", "status"):
                        state_names = {0: "通常状態 (State 0)", 1: "通信監視中 (State 1)", 2: "消灯中 (State 2)"}
                        state_str = state_names.get(current_state_num, "不明")
                        mode_str = force_power_mode.upper() if force_power_mode else "なし (通常時間帯制御)"
                        reply_text = (
                            f"📊 **[{pc_name}] 現在のステータス**\n"
                            f"·状態: {state_str}\n"
                            f"·無操作時間: {current_idle_sec:.1f} 秒\n"
                            f"·通信速度: {current_net_speed:.1f} KB/s\n"
                            f"·GPU使用率: {current_gpu_util} %\n"
                            f"·手動予約: {mode_str}"
                        )
                    elif cmd in ("/server", "server"):
                        config = load_config()
                        if len(text_parts) > 1:
                            sub_cmd = text_parts[1]
                            if sub_cmd in ("off", "desktop", "always"):
                                config["server_mode"] = sub_cmd
                                save_config(config)
                                reply_text = f"🟢 **[{pc_name}]** サーバモードを `{sub_cmd.upper()}` に変更し、設定ファイルを更新しました。"
                                print(f"\n{get_timestamp()} [リモート設定] Telegramからサーバモード変更を受信: {sub_cmd.upper()}")
                            else:
                                reply_text = f"❌ **[{pc_name}]** 無効なモードです。`off`, `desktop`, `always` から選択してください。"
                        else:
                            current_mode = get_server_mode_type(config)
                            reply_text = (
                                f"⚙️ **[{pc_name}] サーバモード設定**\n"
                                f"現在のモード: `{current_mode.upper()}`\n\n"
                                f"変更するには以下のように送信してください:\n"
                                f"· `server off` (通常運用)\n"
                                f"· `server desktop` (デスクトップ時のみ)\n"
                                f"· `server always` (常時適用)"
                            )
                        
                    if reply_text:
                        send_telegram_notification(bot_token, chat_id, reply_text)
                        
        except Exception as e:
            # ネット切断等の一時的な例外は、ログを汚さないためスルーして5秒後に再試行
            time.sleep(5)

def main():
    global force_power_mode
    global current_state_num, current_idle_sec, current_net_speed, current_gpu_util
    global is_sleep_pending, telegram_extend_request

    # 簡易編集モードを無効化
    disable_quick_edit()

    # Discord Webhook & Telegram テスト送信のコマンドライン引数判定
    if len(sys.argv) > 1 and sys.argv[1] == "--test-webhook":
        config = load_config()
        discord_url = config.get("discord_webhook_url", "")
        telegram_token = config.get("telegram_bot_token", "")
        telegram_chat = config.get("telegram_chat_id", "")
        
        if not discord_url and not (telegram_token and telegram_chat):
            print("[エラー] config.json に通知先（Discord または Telegram）が設定されていません。")
            sys.exit(1)
            
        pc_name = get_computer_name()
        test_message = f"🔔 **[{pc_name}]** Webhookテスト通知です。このメッセージが見えていれば連携は成功しています！"
        
        if discord_url:
            print(f"Discord Webhookのテスト送信を行っています... (URL: {discord_url[:30]}...)")
            send_discord_notification(discord_url, test_message)
        if telegram_token and telegram_chat:
            print(f"Telegramのテスト送信を行っています... (Chat ID: {telegram_chat})")
            send_telegram_notification(telegram_token, telegram_chat, test_message)
            
        print("テストメッセージの送信を試みました。スマホや各アプリを確認してください。")
        sys.exit(0)

    print("=" * 60)
    print(" Dual Sleeper - 段階的電源管理システム")
    print("=" * 60)
    
    config = load_config()
    print("現在の設定:")
    print(f"  ・無操作しきい値      : {config['idle_limit_seconds']} 秒")
    print(f"  ・通常通信しきい値    : {config['network_limit_kbs']} KB/s")
    print(f"  ・高通信しきい値      : {config.get('high_network_limit_kbs', 625.0)} KB/s (配信等保護用)")
    print(f"  ・通信監視時間        : {config['network_check_duration_seconds']} 秒")
    print(f"  ・監視ポーリング間隔  : {config['check_interval_seconds']} 秒")
    
    standby_limit = config.get("standby_after_monitor_off_seconds", 0)
    if standby_limit > 0:
        print(f"  ・システムスリープ遅延: {standby_limit} 秒 (モニター消灯後)")
        start_h = config.get("hibernate_start_hour")
        end_h = config.get("hibernate_end_hour")
        if start_h is not None and end_h is not None and (start_h > 0 or end_h > 0):
            print(f"  ・夜間休止状態の時間帯: {start_h}:00 〜 {end_h}:00 (それ以外はスタンバイ)")
        else:
            print("  ・夜間休止状態の時間帯: 無効")
    else:
        print("  ・システムスリープ遅延: 無効 (モニター消灯のみ)")
        
    no_sleep_start = config.get("no_sleep_start_hour", 0)
    no_sleep_end = config.get("no_sleep_end_hour", 0)
    if no_sleep_start > 0 or no_sleep_end > 0:
        print(f"  ・スリープ禁止時間帯  : {no_sleep_start}:00 〜 {no_sleep_end}:00 (モニター消灯のみ実行)")
    else:
        print("  ・スリープ禁止時間帯  : 無効")
        
    force_off_limit = config.get("force_monitor_off_idle_seconds", 0)
    if force_off_limit > 0:
        print(f"  ・強制モニター消灯    : {force_off_limit} 秒 (無操作継続時, 通信の有無を問わず)")
    else:
        print("  ・強制モニター消灯    : 無効")
        
    gpu_limit = config.get("gpu_limit_percent", 0)
    gpu_procs = config.get("gpu_protect_processes", [])
    if gpu_limit > 0 and gpu_procs:
        print(f"  ・GPU保護しきい値     : {gpu_limit} % (対象: {', '.join(gpu_procs)})")
    else:
        print("  ・GPU保護設定         : 無効")
        
    webhook_url = config.get("discord_webhook_url", "")
    tg_token = config.get("telegram_bot_token", "")
    tg_chat = config.get("telegram_chat_id", "")
    
    notifications = []
    if webhook_url:
        notifications.append("Discord")
    if tg_token and tg_chat:
        notifications.append("Telegram")
        
    if notifications:
        print(f"  ・外部通知サービス    : {', '.join(notifications)} (猶予: {config.get('sleep_pending_seconds', 30)} 秒)")
    else:
        print("  ・外部通知サービス    : 無効 (通知先URL・ID未設定)")
        
    # モニター復帰マウス移動距離しきい値の出力
    print(f"  ・モニター復帰マウス距離: {config.get('wakeup_mouse_distance_px', 100)} px (大きく動かした時のみ復帰)")
    
    # 復帰後の設定出力
    print(f"  ・復帰後判定猶予時間  : {config.get('wakeup_mouse_grace_seconds', 20)} 秒 (OSノイズ回避用)")
    print(f"  ・復帰判断アクティブ値: {config.get('wakeup_active_threshold_seconds', 5)} 秒 (猶予終了時の判定しきい値)")
    
    # 高速消灯・サーバモードの出力
    mode_val = get_server_mode_type(config)
    server_delay = config.get("server_mode_standby_delay_seconds", 600)
    if mode_val == "desktop":
        mode_desc = f"有効 (デスクトップ時のみ | 消灯: 30秒+30秒 | スリープ遅延: {server_delay}秒)"
    elif mode_val == "always":
        mode_desc = f"有効 (常時適用 | 消灯: 30秒+30秒 | スリープ遅延: {server_delay}秒)"
    else:
        mode_desc = "無効"
    print(f"  ・高速消灯サーバモード: {mode_desc}")
    
    # ダウンロードフォルダの自動取得
    downloads_dir = get_downloads_folder()
    print(f"  ・ダウンロードフォルダ: {downloads_dir}")
    print("=" * 60)
    print("【キーボード操作】 s:次回強制スタンバイ | h:次回強制ハイバネート | c:予約解除")
    print("【リモート操作】   Telegram Bot から /sleep, /hibernate, /cancel, /status が利用可能")
    print("=" * 60)
    print("監視を開始します。終了するには Ctrl+C を押してください。\n")

    # Telegram受信バックグラウンドスレッドの起動
    pc_name = get_computer_name()
    if tg_token and tg_chat:
        tg_thread = threading.Thread(
            target=telegram_worker, 
            args=(tg_token, tg_chat, pc_name), 
            daemon=True
        )
        tg_thread.start()

    net_monitor = NetworkMonitor()
    
    # 状態定義:
    # 0: 通常状態（無操作時間を見守る）
    # 1: 通信監視状態（無操作状態になり、ネットワークの低通信が継続するのを待つ）
    # 2: 消灯状態（モニターがオフ。操作があるのを待つ）
    state = 0 
    
    low_net_start_time = None
    low_net_standby_start_time = None
    monitor_off_input_time = None
    last_wakeup_time = time.time()
    
    # リトライ制御用変数
    is_retrying = False # スリープ失敗時のリretry中フラグ
    retry_start_time = None # リretry開始 of 物理時刻
    has_sent_10min_warning = False # 10分経過警告の送信済みフラグ
    
    # マウス座標記録用
    last_mouse_x, last_mouse_y = 0, 0
    
    # スリープ復帰後の猶予タイマー関連
    wakeup_grace_until = 0
    user_active_during_grace = False
    wakeup_mouse_x, wakeup_mouse_y = 0, 0

    # メディア強制点灯用変数
    media_force_on_until = 0
    last_detected_media_title = ""
    media_extensions = (".mp4", ".mkv", ".avi", ".mov", ".wmv", ".flv", ".webm", ".jpg", ".jpeg", ".png", ".gif", ".bmp", ".webp")

    # 一時的な延長時間記憶用
    extended_standby_limit = 0

    try:
        while True:
            # 常に非同期でローカルのキーボード入力をチェック
            while msvcrt.kbhit():
                try:
                    char_code = msvcrt.getch()
                    if char_code in (b'\x00', b'\xe0'):
                        msvcrt.getch()
                        continue
                    ch = char_code.decode("utf-8").lower()
                    if ch == "s":
                        force_power_mode = "sleep"
                        print(f"\n{get_timestamp()} [手動予約] 次回スリープ移行時、強制的に「スタンバイ (スリープ)」を実行します。(復帰時にリセット)")
                    elif ch == "h":
                        force_power_mode = "hibernate"
                        print(f"\n{get_timestamp()} [手動予約] 次回スリープ移行時、強制的に「休止状態 (ハイバネート)」を実行します。(復帰時にリセット)")
                    elif ch == "c":
                        force_power_mode = None
                        print(f"\n{get_timestamp()} [手動予約] 予約された電源モードを解除しました。(通常設定の時間帯制御に戻ります)")
                except Exception:
                    pass

            # 常にネットワーク速度を更新しておく（正確な差分計測のため）
            speed = net_monitor.get_speed()
            
            # 物理的な無操作時間を取得し、最後のアクティブ時刻を計算
            physical_idle = get_idle_duration()
            current_time = time.time()
            physical_active_time = current_time - physical_idle
            
            # 設定を毎ループ再読み込み（稼働中に設定変更できるようにする）
            config = load_config()

            # ===== 【新機能】アクティブウィンドウのメディアファイル検知 =====
            current_title = get_active_window_title()
            has_media = any(ext in current_title for ext in media_extensions)
            
            if has_media:
                # 前回の検知ファイルからタイトル名が変わった（＝新しく開いた）瞬間にのみタイマーを設定する
                if current_title != last_detected_media_title:
                    last_detected_media_title = current_title
                    # 10分間（600秒）の強制点灯をセット
                    media_force_on_until = time.time() + 600.0
                    print(f"\n{get_timestamp()} [メディア検知] 新しいメディアファイル（...{current_title[-40:]}）のオープンを検知しました。10分間 (600秒) の強制点灯モードに入ります。")
            else:
                # メディアがアクティブでなくなったら記録をクリアして、再度同じファイルを開いた時に反応できるようにする
                last_detected_media_title = ""

            # ===== 高速消灯・サーバモードにおける直接遷移判定 =====
            mode_val = get_server_mode_type(config)
            is_server_active = False
            if mode_val == "always":
                is_server_active = True
            elif mode_val == "desktop":
                is_server_active = is_desktop_active()

            # desktopモードの場合は、デスクトップ表示（アクティブウィンドウなし）を検知した瞬間に
            # 無操作時間の経過を待たずに、直接通信監視状態（State 1）へ移行してカウントを開始する
            if state == 0 and mode_val == "desktop" and is_desktop_active():
                state = 1
                low_net_start_time = time.time()
                print(f"\n{get_timestamp()} [状態遷移] デスクトップ表示（サーバモード）を検知したため、直接「通信監視状態（State 1）」から開始します。")

            # ===== 各状態における動的しきい値の設定 =====
            if is_server_active:
                limit_sec = 30
                net_check_duration = 30
                # サーバモード時のスリープ遅延を取得
                raw_standby_limit = config.get("server_mode_standby_delay_seconds", 600)
            else:
                limit_sec = config['idle_limit_seconds']
                net_check_duration = config['network_check_duration_seconds']
                raw_standby_limit = config.get("standby_after_monitor_off_seconds", 0)

            # 一時的な延長がセットされている場合は、それを最優先する
            if extended_standby_limit > 0:
                standby_limit = extended_standby_limit
            else:
                standby_limit = raw_standby_limit

            # ===== 【メディア強制点灯モード処理】 =====
            is_media_forced = (time.time() < media_force_on_until and media_force_on_until > 0)
            if is_media_forced:
                # メディアウィンドウが非アクティブ化された（閉じられた、または別ウインドウへ切り替えられた）場合
                # 最初に検知したファイルタイトルが現在のタイトルから消失したかをチェック
                if last_detected_media_title not in current_title:
                    print(f"\n{get_timestamp()} [状態遷移] メディアウィンドウの非アクティブ化（またはクローズ）を検知したため、強制点灯を打ち切り、通常監視（State 0）へ移行します。")
                    media_force_on_until = 0
                    state = 0
                    last_wakeup_time = time.time()
                    net_monitor.get_speed()
                    continue

                # 10分間はすべての操作チェックや省エネ状態への遷移を完全に無視する
                last_wakeup_time = time.time() # 監視タイマーの基点を現在にし続ける
                current_state_num = 0
                current_idle_sec = 0.0
                current_net_speed = speed
                
                # GPUステータスの更新
                gpu_limit = config.get("gpu_limit_percent", 0)
                gpu_procs = config.get("gpu_protect_processes", [])
                gpu_util, gpu_protect_active = get_gpu_status(gpu_procs)
                current_gpu_util = gpu_util
                
                mode_status = f" | 予約: {force_power_mode.upper() if force_power_mode else 'なし'}"
                print(f"\r{get_timestamp()} [メディア強制点灯中] 残り時間: {int(media_force_on_until - current_time)}秒 | 通信: {speed:.1f} KB/s{mode_status}  ", end="", flush=True)
                
                time.sleep(config['check_interval_seconds'])
                continue
            elif media_force_on_until > 0:
                # ちょうど10分が満了した瞬間
                media_force_on_until = 0 # タイマーをクリア
                state = 1 # 直接「通信監視状態 (State 1)」へ遷移！
                low_net_start_time = time.time() # 通信量の監視を開始
                # 無操作時間はすでに満了しているものとして偽装（ダミー時刻セット）
                last_wakeup_time = time.time() - config['idle_limit_seconds']
                print(f"\n{get_timestamp()} [状態遷移] メディア強制点灯時間が終了しました。放置の可能性があるため、通信監視状態（State 1）へダイレクト移行します。")
                continue

            # 物理入力の時刻と、モニター復帰時刻 of いずれか新しい方を最終アクティブ時刻とする
            effective_active_time = max(physical_active_time, last_wakeup_time)
            idle_sec = current_time - effective_active_time
            
            # グローバルステータスの更新（Telegramスレッドへのリアルタイム情報共有用）
            current_state_num = state
            current_idle_sec = idle_sec
            current_net_speed = speed
            gpu_limit = config.get("gpu_limit_percent", 0)
            gpu_procs = config.get("gpu_protect_processes", [])
            gpu_util, gpu_protect_active = get_gpu_status(gpu_procs)
            current_gpu_util = gpu_util
            
            # 【共通の割り込み処理】長時間の無操作で強制モニターオフにする判定
            force_off_limit = config.get("force_monitor_off_idle_seconds", 0)
            if state != 2 and force_off_limit > 0 and idle_sec >= force_off_limit:
                print(f"\n{get_timestamp()} [実行] 長時間の無操作 ({idle_sec:.1f} 秒) を検知したため、通信状態を問わずモニターをオフにします。")
                turn_off_monitor()
                time.sleep(1.0) # 消灯時のシステムラグやマウスの微振動をやり過ごす
                state = 2
                monitor_off_input_time = get_last_input_time_raw()
                last_mouse_x, last_mouse_y = get_mouse_position()
                low_net_standby_start_time = None
                time.sleep(config['check_interval_seconds'])
                continue
            
            if state == 0:
                # 【通常状態】
                desktop_status = " (サーバモード)" if is_server_active else ""
                mode_status = f" | 予約: {force_power_mode.upper() if force_power_mode else 'なし'}"
                print(f"\r{get_timestamp()} [稼働中] 無操作時間: {idle_sec:.1f}/{limit_sec}秒{desktop_status} | 通信速度: {speed:.1f} KB/s{mode_status}  ", end="", flush=True)
                
                # 操作がない時間がしきい値を超えたら、通信監視状態に遷移
                if idle_sec >= limit_sec:
                    state = 1
                    low_net_start_time = None
                    print(f"\n{get_timestamp()} [状態遷移] 無操作時間（{limit_sec}秒）を超えました。ネットワーク通信量の監視を開始します。")

            elif state == 1:
                # 【通信監視状態】
                # スリープ復帰直後の猶予期間中か判定
                is_grace_period = (time.time() < wakeup_grace_until)
                
                if is_grace_period:
                    # 猶予期間中：ユーザーが本当に手でマウスを動かしたかを追跡
                    curr_x, curr_y = get_mouse_position()
                    dx = abs(curr_x - wakeup_mouse_x)
                    dy = abs(curr_y - wakeup_mouse_y)
                    limit_px = config.get("wakeup_mouse_distance_px", 100)
                    
                    if dx >= limit_px or dy >= limit_px:
                        # 猶予期間中に「100px以上の本物の移動」を一度でも検知したらフラグON
                        user_active_during_grace = True
                        
                    print(f"\r{get_timestamp()} [復帰猶予中] 残り猶予: {int(wakeup_grace_until - time.time())}秒 | 操作検知: {'あり' if user_active_during_grace else 'なし'}  ", end="", flush=True)
                else:
                    # 猶予期間が終了した瞬間（または通常の遷移フェーズ）の分岐処理
                    if wakeup_grace_until > 0:
                        wakeup_grace_until = 0 # 1回だけ判定を実行するためにクリア
                        
                        # 判定基準:
                        # (a) 20秒の猶予期間内に、一度でも100px以上の意図的なマウス移動があったか
                        # (b) または、猶予終了時の直近の無操作時間がしきい値（デフォルト5秒）未満であるか
                        threshold_sec = config.get("wakeup_active_threshold_seconds", 5)
                        is_real_user_active = user_active_during_grace or (idle_sec < threshold_sec)
                        
                        if is_real_user_active:
                            print(f"\n{get_timestamp()} [状態遷移] 復帰猶予中に本物の操作を検知したため、通常監視（State 0）へ移行します。")
                            state = 0
                            last_wakeup_time = time.time()
                            net_monitor.get_speed()
                            # ユーザーが明示的に操作したため、一時予約は解除する
                            force_power_mode = None
                            extended_standby_limit = 0 # 復帰時は一時延長を解除
                            continue
                        else:
                            print(f"\n{get_timestamp()} [状態遷移] 復帰猶予中に操作ノイズ以外は検知されなかったため、モニターを消灯して消灯状態（State 2）へ移行します。")
                            turn_off_monitor()
                            time.sleep(1.0)
                            state = 2
                            monitor_off_input_time = get_last_input_time_raw()
                            last_mouse_x, last_mouse_y = get_mouse_position()
                            low_net_standby_start_time = None
                            continue

                    # 通常のState 1：監視中にユーザーが操作を再開したら通常状態に戻る
                    if idle_sec < limit_sec:
                        state = 0
                        low_net_start_time = None
                        print(f"\n{get_timestamp()} [状態遷移] 操作を検知したため、通常監視に戻ります。")
                        extended_standby_limit = 0 # 復帰時は一時延長を解除
                        continue
                
                # ファイルダウンロード中であるかチェック
                is_downloading = is_downloading_active(downloads_dir)
                
                # 通信速度がしきい値以下、または「ブラウザがファイルダウンロード中」の場合
                if speed <= config['network_limit_kbs'] or is_downloading:
                    if low_net_start_time is None:
                        low_net_start_time = time.time()
                    
                    elapsed_low_net = time.time() - low_net_start_time
                    dl_status = " (ダウンロード検出中)" if is_downloading else ""
                    print(f"\r{get_timestamp()} [通信監視中] 低通信継続: {elapsed_low_net:.1f}/{net_check_duration}秒 | 通信速度: {speed:.1f} KB/s{dl_status}  ", end="", flush=True)
                    
                    # 低通信の状態が指定時間続いたらモニター消灯
                    if elapsed_low_net >= net_check_duration:
                        print(f"\n{get_timestamp()} [実行] モニターをオフにします。")
                        turn_off_monitor()
                        time.sleep(1.0) # 消灯時のシステムラグやマウスの微振動をやり過ごす
                        state = 2
                        monitor_off_input_time = get_last_input_time_raw()
                        last_mouse_x, last_mouse_y = get_mouse_position()
                        low_net_standby_start_time = None # スタンバイ監視用タイマーを初期化
                else:
                    # 通信量がしきい値を超えたら計測タイマーをリセット
                    if low_net_start_time is not None:
                        print(f"\n{get_timestamp()} [情報] 通信量上昇を検知したためタイマーをリセットします。速度: {speed:.1f} KB/s")
                    low_net_start_time = None
                    print(f"\r{get_timestamp()} [通信監視中] 通信待機中... | 通信速度: {speed:.1f} KB/s  ", end="", flush=True)

            elif state == 2:
                # 【消灯状態】
                # 1. マウスが大きく動かされたか（指定ピクセル以上）だけで復帰判定を行う（キー入力やクリックは除外）
                curr_x, curr_y = get_mouse_position()
                dx = abs(curr_x - last_mouse_x)
                dy = abs(curr_y - last_mouse_y)
                limit_px = config.get("wakeup_mouse_distance_px", 100)
                
                if dx >= limit_px or dy >= limit_px:
                    print(f"\n{get_timestamp()} [復帰] マウスの移動を検知しました。状態遷移（State 0）を行います。")
                    state = 0
                    last_wakeup_time = time.time() # 復帰した瞬間を基準時として記録
                    net_monitor.get_speed() # 復帰待ちの間の通信量をリセット
                    is_retrying = False # 操作復帰時にリトライフラグをクリア
                    retry_start_time = None
                    has_sent_10min_warning = False
                    force_power_mode = None # 操作復帰時は手動予約をクリアする
                    extended_standby_limit = 0 # 復帰時は一時延長を解除
                    continue

                # 2. スタンバイ判定のためのネットワーク監視およびGPU監視
                if standby_limit > 0:
                    # 高トラフィック（配信など）のしきい値を取得
                    high_net_limit = config.get("high_network_limit_kbs", 625.0)
                    
                    # ファイルダウンロード中であるかチェック
                    is_downloading = is_downloading_active(downloads_dir)
                    
                    # スリープ禁止時間帯（モニター消灯のみ）かチェック
                    is_no_sleep = is_no_sleep_time(config.get("no_sleep_start_hour"), config.get("no_sleep_end_hour"))
                    
                    # 【スリープを許可する条件】
                    # ※GPUは前段で測定した値をそのまま流用
                    is_gpu_busy_with_python = (gpu_limit > 0 and gpu_util >= gpu_limit and gpu_protect_active)
                    allow_sleep = (not is_gpu_busy_with_python) and (speed < high_net_limit) and (not is_downloading) and (not is_no_sleep)
                    
                    # 【リretry中の10分継続警告チェック】
                    if is_retrying and retry_start_time is not None and not has_sent_10min_warning:
                        elapsed_retry = time.time() - retry_start_time
                        if elapsed_retry >= 600.0:  # 10分
                            send_notifications(
                                config,
                                f"⚠️ **[{pc_name}]** スリープのリトライが10分以上継続しています。Windows Updateや他の常駐アプリ（DontSleep等）によってスリープが阻害されている可能性があります。"
                            )
                            has_sent_10min_warning = True
                            print(f"\n{get_timestamp()} [警告] リトライが10分継続したため、警告通知を送信しました。")
                    
                    if allow_sleep:
                        if low_net_standby_start_time is None:
                            low_net_standby_start_time = time.time()
                        
                        elapsed_low_net_standby = time.time() - low_net_standby_start_time
                        print(f"\r{get_timestamp()} [モニターOFF] スリープ待機: {elapsed_low_net_standby:.1f}/{standby_limit}秒 | 通信: {speed:.1f} KB/s | GPU: {gpu_util}%  ", end="", flush=True)
                        
                        # スリープ状態での終了時、予約ログを出力
                        if force_power_mode:
                            print(f" (予約適用: {force_power_mode.upper()})", end="", flush=True)
                        
                        # スリープ監視時間経過でシステムをサスペンド/ハイバネート
                        if elapsed_low_net_standby >= standby_limit:
                            # スリープか休止状態かの最終決定
                            if force_power_mode == "hibernate":
                                use_hibernate = True
                                mode_desc = "手動予約「休止状態 (ハイバネート)」"
                            elif force_power_mode == "sleep":
                                use_hibernate = False
                                mode_desc = "手動予約「スタンバイ (スリープ)」"
                            else:
                                start_h = config.get("hibernate_start_hour")
                                end_h = config.get("hibernate_end_hour")
                                use_hibernate = is_hibernate_time(start_h, end_h)
                                mode_desc = "時間帯設定に従い、「休止状態」" if use_hibernate else "時間帯設定に従い、「スタンバイ」"
                            
                            mode_name = "休止状態 (ハイバネート)" if use_hibernate else "スタンバイ (スリープ)"
                            pending_sec = config.get("sleep_pending_seconds", 30)
                            
                            canceled = False
                            cancel_reason = ""
                            
                            # リトライ時ではない場合のみ、スマホへスリープ予告通知と猶予時間の監視を行う
                            if not is_retrying:
                                print(f"\n{get_timestamp()} [スリープ予告] {pending_sec}秒後にシステムを {mode_name} に移行します。({mode_desc})")
                                send_notifications(
                                    config,
                                    f"🔔 **[{pc_name}] まもなく {mode_name} に移行します。**\n"
                                    f"({mode_desc})\n"
                                    f"スマホから何か文字・数字を送信すると、移行を一時的に10分間延長（モニター消灯維持）します。"
                                )
                                
                                # 猶予期間中の割り込み（操作検知）の監視
                                start_pending_time = time.time()
                                monitor_off_input_time_before = get_last_input_time_raw()
                                
                                # グローバル割り込み受付フラグの初期化
                                is_sleep_pending = True
                                telegram_extend_request = False
                                
                                while time.time() - start_pending_time < pending_sec:
                                    # 1. 物理デバイスでの操作検知
                                    current_input = get_last_input_time_raw()
                                    if current_input != monitor_off_input_time_before:
                                        canceled = True
                                        cancel_reason = "physical"
                                        break
                                    
                                    # 2. Telegramからの「なんでも1文字入力」によるスリープ延長割り込み検知
                                    if telegram_extend_request:
                                        canceled = True
                                        cancel_reason = "telegram"
                                        break
                                        
                                    time.sleep(0.5) # 0.5秒おきに操作チェック
                                
                                # 警告期間終了
                                is_sleep_pending = False
                                
                                if canceled:
                                    if cancel_reason == "telegram":
                                        # Telegramによる延長：画面は暗いまま、待機時間だけを10分(600秒)延長する！
                                        print(f"\n{get_timestamp()} [延長] Telegramからの割り込みを受信したため、スリープを10分間延長します。モニター消灯状態は維持されます。")
                                        state = 2 # 消灯を維持
                                        low_net_standby_start_time = time.time() # タイマーのリセット
                                        extended_standby_limit = 600 # 延長時間（10分）を次のスリープ判定に強制適用
                                        is_retrying = False
                                        retry_start_time = None
                                        has_sent_10min_warning = False
                                        # 割り込み要求フラグのクリア
                                        telegram_extend_request = False
                                        continue
                                    else:
                                        # 物理デバイス操作によるキャンセル：通常画面に復帰
                                        print(f"\n{get_timestamp()} [キャンセル] 猶予時間中に操作を検知したため、スリープを中止しました。モニターをONに戻します。")
                                        turn_on_monitor()
                                        state = 0
                                        last_wakeup_time = time.time()
                                        net_monitor.get_speed()
                                        send_notifications(
                                            config,
                                            f"🟢 **[{pc_name}]** 操作を検知したため、スリープ移行をキャンセルしました。通常稼働に戻ります。"
                                        )
                                        is_retrying = False
                                        retry_start_time = None
                                        has_sent_10min_warning = False
                                        force_power_mode = None # 一時予約を解除
                                        extended_standby_limit = 0
                                        continue
                            
                            print(f"{get_timestamp()} [実行] システムを {mode_name} にします。")
                            
                            # 復帰直後は「消灯状態（State 2）」から開始するように設定
                            state = 2 
                            low_net_standby_start_time = None
                            extended_standby_limit = 0
                            
                            # スリープに入る直前の物理時刻と現在時刻を記録
                            sleep_call_time = time.time()
                            sleep_start_dt = datetime.datetime.now()
                             
                            go_to_sleep(hibernate=use_hibernate)
                             
                            # ===== ここからスリープ復帰後の処理 =====
                            # 復帰した直後, ネットワークモニターをリセット
                            time.sleep(2)
                            net_monitor.get_speed()
                             
                            # 復帰時の入力状態とマウス位置を上書き記録
                            monitor_off_input_time = get_last_input_time_raw()
                            last_mouse_x, last_mouse_y = get_mouse_position()
                            
                            # 実際にどのくらいスリープしていたか（経過時間）を計算
                            sleep_duration = time.time() - sleep_call_time
                            
                            if sleep_duration < 15.0:
                                # 15秒未満で戻ってきた ➔ スリープ失敗、またはノイズによる即時誤復帰！
                                print(f"\n{get_timestamp()} [警告] スリープの移行に失敗した（または即時誤復帰した）ため、30秒後に再試行します。")
                                
                                # 初回のリトライ移行時のみ、スマホへ警告通知を送信
                                if not is_retrying:
                                    send_notifications(
                                        config,
                                        f"⚠️ **[{pc_name}]** スリープの移行に失敗したため、成功するまで30秒おきにリトライ処理に入ります。"
                                    )
                                    # リトライ開始時刻をセット
                                    retry_start_time = time.time()
                                    has_sent_10min_warning = False
                                    
                                is_retrying = True # リretryフラグをON
                                # スリープタイマーを「残り30秒」の状態にセットする
                                low_net_standby_start_time = time.time() - (standby_limit - 30)
                            else:
                                # 15秒以上経って戻ってきた ➔ 本物のスリープ成功＆正常復帰！
                                # ※復帰直後は「通信監視状態（State 1）」から開始し、指定秒数監視後に分岐させる
                                print(f"\n{get_timestamp()} [情報] スリープから復帰しました。通信監視状態（State 1）から再開します。")
                                turn_on_monitor() # プログラムの意思で点灯させるため維持
                                
                                # スリープの開始、終了時刻、および睡眠実績時間を計算して通知
                                sleep_end_dt = datetime.datetime.now()
                                duration_seconds = int(sleep_duration)
                                hours = duration_seconds // 3600
                                minutes = (duration_seconds % 3600) // 60
                                
                                duration_str = ""
                                if hours > 0:
                                    duration_str += f"{hours}時間"
                                duration_str += f"{minutes}分"
                                if hours == 0 and minutes == 0:
                                    duration_str = f"{duration_seconds}秒"
                                    
                                send_notifications(
                                    config,
                                    f"🟢 **[{pc_name}]** スリープから正常に復帰しました。\n"
                                    f"·スリープ開始: {sleep_start_dt.strftime('%m/%d %H:%M:%S')}\n"
                                    f"·スリープ解除: {sleep_end_dt.strftime('%m/%d %H:%M:%S')}\n"
                                    f"·スリープ時間: {duration_str}"
                                )
                                
                                state = 1
                                # 復帰猶予ガード時間の設定
                                grace_sec = config.get("wakeup_mouse_grace_seconds", 20)
                                wakeup_grace_until = time.time() + grace_sec
                                user_active_during_grace = False
                                wakeup_mouse_x, wakeup_mouse_y = get_mouse_position()
                                
                                last_wakeup_time = time.time()
                                is_retrying = False # リretryフラグをOFF
                                retry_start_time = None
                                has_sent_10min_warning = False
                                # 通常通りタイマーをリセット
                                low_net_standby_start_time = None
                                
                                # 復帰成功時に手動予約を自動クリア
                                force_power_mode = None
                    else:
                        # 通信量上昇、GPU高負荷、ダウンロード中、またはスリープ禁止時間帯によるリセット
                        if low_net_standby_start_time is not None:
                            if is_no_sleep:
                                print(f"\n{get_timestamp()} [情報] スリープ禁止時間帯（{config.get('no_sleep_start_hour')}時〜{config.get('no_sleep_end_hour')}時）のためスリープタイマーをリセットします。")
                            elif is_gpu_busy_with_python:
                                print(f"\n{get_timestamp()} [情報] LoRA学習中(python高負荷)を検知したためスリープタイマーをリセットします。GPU: {gpu_util}%")
                            elif is_downloading:
                                print(f"\n{get_timestamp()} [情報] ファイルダウンロード中を検知したためスリープタイマーをリセットします。")
                            elif speed >= high_net_limit:
                                print(f"\n{get_timestamp()} [情報] 高トラフィック(配信または高速DL: {speed:.1f} KB/s)を検知したためスリープタイマーをリセットします。")
                        low_net_standby_start_time = None
                        
                        if is_no_sleep:
                            print(f"\r{get_timestamp()} [モニターOFF] スリープ禁止時間帯(モニター消灯のみ維持)... | 通信: {speed:.1f} KB/s  ", end="", flush=True)
                        elif is_gpu_busy_with_python:
                            print(f"\r{get_timestamp()} [モニターOFF] LoRA学習保護中... | 通信: {speed:.1f} KB/s | GPU: {gpu_util}% (python)  ", end="", flush=True)
                        elif is_downloading:
                            print(f"\r{get_timestamp()} [モニターOFF] ファイルダウンロード中... | 通信: {speed:.1f} KB/s  ", end="", flush=True)
                        elif speed >= high_net_limit:
                            print(f"\r{get_timestamp()} [モニターOFF] 配信/高速DL保護中... | 通信: {speed:.1f} KB/s (高トラフィック)  ", end="", flush=True)
                        else:
                            print(f"\r{get_timestamp()} [モニターOFF] 通信待機中... | 通信: {speed:.1f} KB/s | GPU: {gpu_util}%  ", end="", flush=True)
                else:
                    # スリープ無効時の静か待機
                    pass

            time.sleep(config['check_interval_seconds'])

    except KeyboardInterrupt:
        print("\n監視プログラムを終了しました。")
        # 終了時に念のためモニターをオンにする命令を送る
        turn_off_monitor()

if __name__ == "__main__":
    main()
