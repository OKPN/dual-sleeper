import ctypes
import ctypes.wintypes
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
import math

# Windows API 定義
class LASTINPUTINFO(ctypes.Structure):
    _fields_ = [("cbSize", ctypes.c_uint), ("dwTime", ctypes.c_uint)]

class POINT(ctypes.Structure):
    _fields_ = [("x", ctypes.c_long), ("y", ctypes.c_long)]

# XInput (コントローラー入力) 構造体定義
class XINPUT_GAMEPAD(ctypes.Structure):
    _fields_ = [
        ("wButtons", ctypes.c_ushort),
        ("bLeftTrigger", ctypes.c_ubyte),
        ("bRightTrigger", ctypes.c_ubyte),
        ("sThumbLX", ctypes.c_short),
        ("sThumbLY", ctypes.c_short),
        ("sThumbRX", ctypes.c_short),
        ("sThumbRY", ctypes.c_short),
    ]

class XINPUT_STATE(ctypes.Structure):
    _fields_ = [
        ("dwPacketNumber", ctypes.c_ulong),
        ("Gamepad", XINPUT_GAMEPAD),
    ]

# XInput DLLの安全な読み込み
xinput_dll = None
for dll_name in ["xinput1_4.dll", "xinput9_1_0.dll", "xinput1_3.dll"]:
    try:
        xinput_dll = ctypes.windll.LoadLibrary(dll_name)
        break
    except Exception:
        pass

# GUIDの定義 (Downloadsフォルダの自動取得およびWASAPI用)
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
current_net_median_speed = 0.0
current_net_max_speed = 0.0
current_low_net_sec = 0.0
current_gpu_util = 0
current_media_force_until = 0.0
current_status_reason = "通常"
telegram_offset = 0

# Telegram割り込みスリープ延長用グローバル変数
is_sleep_pending = False
telegram_extend_request = False

# HyperKey (Win + Ctrl + Shift + Alt + M) 即時消灯トリガー用グローバル変数
hotkey_state2_triggered = False
last_hotkey_time = 0.0

# コントローラー前回のパケット番号記憶用辞書 (プレイヤー0〜3)
last_xinput_packets = {}

def is_audio_session_active():
    """
    Windows Core Audio API (WASAPI) を ctypes 経由で呼び出し、
    現在スピーカーまたはマイクでアクティブな音声セッション（Discord/LINE等の通話・音声再生）が存在するか判定します。
    """
    try:
        # COM初期化
        ctypes.windll.ole32.CoInitialize(None)

        CLSID_MMDeviceEnumerator = GUID(0xBCDE0380, 0x1DED, 0x467C, (ctypes.c_ubyte * 8)(0x96, 0xC7, 0x4D, 0x61, 0x16, 0x09, 0x71, 0x35))
        IID_IMMDeviceEnumerator = GUID(0xA95664D2, 0x9614, 0x4F35, (ctypes.c_ubyte * 8)(0xA7, 0x46, 0xDE, 0x8D, 0xB6, 0x36, 0x17, 0xE6))
        IID_IAudioSessionManager2 = GUID(0x77AA99A0, 0x1BD6, 0x484F, (ctypes.c_ubyte * 8)(0x8B, 0xC7, 0x2C, 0x65, 0x4C, 0x9A, 0x9B, 0x6F))

        # COM クラスインスタンス作成
        enumerator = ctypes.c_void_p()
        hr = ctypes.windll.ole32.CoCreateInstance(
            ctypes.byref(CLSID_MMDeviceEnumerator),
            None,
            1, # CLSCTX_INPROC_SERVER
            ctypes.byref(IID_IMMDeviceEnumerator),
            ctypes.byref(enumerator)
        )
        if hr != 0 or not enumerator:
            return False

        # VTBL 経由で IMMDeviceEnumerator::GetDefaultAudioEndpoint を呼び出し
        # eRender = 0 (スピーカー/出力), eCapture = 1 (マイク/入力), eConsole = 0
        device = ctypes.c_void_p()
        # GetDefaultAudioEndpoint is at index 4 in IMMDeviceEnumerator vtable
        enum_vtable = ctypes.cast(enumerator, ctypes.POINTER(ctypes.c_void_p))[0]
        get_default_endpoint = ctypes.WINFUNCTYPE(ctypes.c_long, ctypes.c_void_p, ctypes.c_int, ctypes.c_int, ctypes.POINTER(ctypes.c_void_p))(
            ctypes.cast(enum_vtable, ctypes.POINTER(ctypes.c_void_p))[4]
        )
        
        # スピーカー(0) と マイク(1) の両方をチェック
        is_active = False
        for flow_type in (0, 1):
            hr = get_default_endpoint(enumerator, flow_type, 0, ctypes.byref(device))
            if hr == 0 and device:
                # IMMDevice::Activate -> IAudioSessionManager2 (index 3 in IMMDevice vtable)
                dev_vtable = ctypes.cast(device, ctypes.POINTER(ctypes.c_void_p))[0]
                activate = ctypes.WINFUNCTYPE(ctypes.c_long, ctypes.c_void_p, ctypes.POINTER(GUID), ctypes.c_ulong, ctypes.c_void_p, ctypes.POINTER(ctypes.c_void_p))(
                    ctypes.cast(dev_vtable, ctypes.POINTER(ctypes.c_void_p))[3]
                )
                session_mgr = ctypes.c_void_p()
                hr_act = activate(device, ctypes.byref(IID_IAudioSessionManager2), 1, None, ctypes.byref(session_mgr))
                
                if hr_act == 0 and session_mgr:
                    # IAudioSessionManager2::GetSessionEnumerator (index 5 in IAudioSessionManager2 vtable)
                    mgr_vtable = ctypes.cast(session_mgr, ctypes.POINTER(ctypes.c_void_p))[0]
                    get_session_enum = ctypes.WINFUNCTYPE(ctypes.c_long, ctypes.c_void_p, ctypes.POINTER(ctypes.c_void_p))(
                        ctypes.cast(mgr_vtable, ctypes.POINTER(ctypes.c_void_p))[5]
                    )
                    session_enum = ctypes.c_void_p()
                    hr_enum = get_session_enum(session_mgr, ctypes.byref(session_enum))
                    
                    if hr_enum == 0 and session_enum:
                        # IAudioSessionEnumerator::GetCount (index 3)
                        enum_vt = ctypes.cast(session_enum, ctypes.POINTER(ctypes.c_void_p))[0]
                        get_count = ctypes.WINFUNCTYPE(ctypes.c_long, ctypes.c_void_p, ctypes.POINTER(ctypes.c_int))(
                            ctypes.cast(enum_vt, ctypes.POINTER(ctypes.c_void_p))[3]
                        )
                        get_session = ctypes.WINFUNCTYPE(ctypes.c_long, ctypes.c_void_p, ctypes.c_int, ctypes.POINTER(ctypes.c_void_p))(
                            ctypes.cast(enum_vt, ctypes.POINTER(ctypes.c_void_p))[4]
                        )
                        
                        count = ctypes.c_int(0)
                        if get_count(session_enum, ctypes.byref(count)) == 0:
                            for idx in range(count.value):
                                session_ctrl = ctypes.c_void_p()
                                if get_session(session_enum, idx, ctypes.byref(session_ctrl)) == 0 and session_ctrl:
                                    # IAudioSessionControl::GetState (index 3 in IAudioSessionControl vtable)
                                    ctrl_vt = ctypes.cast(session_ctrl, ctypes.POINTER(ctypes.c_void_p))[0]
                                    get_state = ctypes.WINFUNCTYPE(ctypes.c_long, ctypes.c_void_p, ctypes.POINTER(ctypes.c_int))(
                                        ctypes.cast(ctrl_vt, ctypes.POINTER(ctypes.c_void_p))[3]
                                    )
                                    state_val = ctypes.c_int(0)
                                    if get_state(session_ctrl, ctypes.byref(state_val)) == 0:
                                        # AudioSessionStateActive = 1
                                        if state_val.value == 1:
                                            is_active = True
                                            ctypes.windll.ole32.CoTaskMemFree(session_ctrl)
                                            break
                                    ctypes.windll.ole32.CoTaskMemFree(session_ctrl)
                        ctypes.windll.ole32.CoTaskMemFree(session_enum)
                    ctypes.windll.ole32.CoTaskMemFree(session_mgr)
                ctypes.windll.ole32.CoTaskMemFree(device)
            if is_active:
                break
        ctypes.windll.ole32.CoTaskMemFree(enumerator)
        return is_active
    except Exception:
        return False

def calculate_median(data_list):
    """通信速度データリストの中央値(Median)を計算して返します。"""
    if not data_list:
        return 0.0
    sorted_list = sorted(data_list)
    n = len(sorted_list)
    mid = n // 2
    if n % 2 == 1:
        return sorted_list[mid]
    else:
        return (sorted_list[mid - 1] + sorted_list[mid]) / 2.0

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

def check_controller_activity():
    """
    接続されているゲームコントローラー(XInput)の入力をチェックし、
    操作が行われた場合は True を返します。
    """
    global xinput_dll, last_xinput_packets
    if not xinput_dll:
        return False

    activity_detected = False
    state = XINPUT_STATE()

    # 最大4台のコントローラーをチェック
    for i in range(4):
        try:
            res = xinput_dll.XInputGetState(i, ctypes.byref(state))
            if res == 0:  # ERROR_SUCCESS (接続中)
                pkt = state.dwPacketNumber
                prev_pkt = last_xinput_packets.get(i, None)
                if prev_pkt is not None and pkt != prev_pkt:
                    activity_detected = True
                last_xinput_packets[i] = pkt
        except Exception:
            pass

    return activity_detected

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
    """モニターの電源をオンにし、マウス入力をシミュレートして確実な点灯を促します。"""
    # 1. モニター電源オンのSysCommand送信
    ctypes.windll.user32.PostMessageW(HWND_BROADCAST, WM_SYSCOMMAND, SC_MONITORPOWER, -1)
    
    # 2. Windows OSに物理的なマウス移動イベント(mouse_event)を発射してバックライトを点灯させる
    # MOUSEEVENTF_MOVE = 0x0001
    ctypes.windll.user32.mouse_event(0x0001, 1, 0, 0, 0)
    time.sleep(0.05)
    ctypes.windll.user32.mouse_event(0x0001, -1, 0, 0, 0)

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

def parse_location(lightning_cfg):
    """
    lightning_protection 設定辞書から (latitude, longitude) を解析して返します。
    "location": "35.6812, 139.7671" のような Google マップからの全コピー文字列をパースします。
    """
    if not isinstance(lightning_cfg, dict):
        return None, None
        
    loc = lightning_cfg.get("location", "")
    if loc and isinstance(loc, str):
        parts = [p.strip() for p in loc.replace(",", " ").split() if p.strip()]
        if len(parts) >= 2:
            try:
                return float(parts[0]), float(parts[1])
            except ValueError:
                pass
                
    lat = lightning_cfg.get("latitude")
    lon = lightning_cfg.get("longitude")
    if lat is not None and lon is not None:
        try:
            return float(lat), float(lon)
        except (ValueError, TypeError):
            pass
            
    return None, None

def get_auto_hibernate_mode(lightning_cfg):
    """
    lightning_protection 設定辞書から auto_hibernate モードを取得します。
    戻り値: "off", "state2_only", "always"
    """
    if not isinstance(lightning_cfg, dict):
        return "off"
        
    val = lightning_cfg.get("auto_hibernate", "off")
    if isinstance(val, bool):
        return "always" if val else "off"
        
    val_str = str(val).strip().lower()
    if val_str in ("always", "true", "all"):
        return "always"
    elif val_str in ("state2_only", "state2", "standby"):
        return "state2_only"
    else:
        return "off"

def calculate_distance_km(lat1, lon1, lat2, lon2):
    """
    2点間の緯度・経度から大円距離(km)を算出します (Haversine formula)。
    """
    try:
        R = 6371.0  # 地球の平均半径 (km)
        phi1, phi2 = math.radians(lat1), math.radians(lat2)
        d_phi = math.radians(lat2 - lat1)
        d_lam = math.radians(lon2 - lon1)
        
        a = math.sin(d_phi / 2.0)**2 + math.cos(phi1) * math.cos(phi2) * math.sin(d_lam / 2.0)**2
        c = 2.0 * math.atan2(math.sqrt(a), math.sqrt(1.0 - a))
        return R * c
    except Exception:
        return 0.0

def calculate_bearing_deg(lat1, lon1, lat2, lon2):
    """
    地点1から見た地点2の方位角(0-360度)を計算します。
    """
    try:
        phi1, phi2 = math.radians(lat1), math.radians(lat2)
        d_lam = math.radians(lon2 - lon1)
        y = math.sin(d_lam) * math.cos(phi2)
        x = math.cos(phi1) * math.sin(phi2) - math.sin(phi1) * math.cos(phi2) * math.cos(d_lam)
        return (math.degrees(math.atan2(y, x)) + 360.0) % 360.0
    except Exception:
        return None

def calculate_bearing_16(lat1, lon1, lat2, lon2):
    """
    地点1(端末)から見た地点2(気象観測ポイント)の方角を16方位("北西", "南南西"など)で返します。
    """
    try:
        bearing = calculate_bearing_deg(lat1, lon1, lat2, lon2)
        if bearing is None:
            return ""
        
        directions = [
            "北", "北北東", "北東", "東北東",
            "東", "東南東", "南東", "南南東",
            "南", "南南西", "南西", "西南西",
            "西", "西北西", "北西", "北北西"
        ]
        index = int((bearing + 11.25) / 22.5) % 16
        return directions[index]
    except Exception:
        return ""

def calculate_wind_approach(lat1, lon1, lat2, lon2, wind_deg):
    """
    観測地点(lat2, lon2)から端末(lat1, lon1)への方向ベクトルと、風向(wind_deg)から
    雷雲が端末へ「接近中」「遠ざかり中」「並行移動」かを判定します。
    wind_deg: 風が吹いてくる方位角 (0-360度)
    """
    if lat1 is None or lon1 is None or lat2 is None or lon2 is None or wind_deg is None:
        return "風向データなし", 0
        
    # 観測地点 -> 端末 への方位角
    bearing_to_target = calculate_bearing_deg(lat2, lon2, lat1, lon1)
    if bearing_to_target is None:
        return "解析不能", 0
        
    # 風が吹き去る方向 (雷雲の移動ベクトル方向) = 風向 + 180度
    move_deg = (float(wind_deg) + 180.0) % 360.0
    
    # 移動方向と端末方向の角度差 (0-180度)
    diff = abs(move_deg - bearing_to_target) % 360.0
    if diff > 180.0:
        diff = 360.0 - diff
        
    if diff <= 45.0:
        return "🏃💨 端末へ接近中！", 15
    elif diff >= 135.0:
        return "🍃 端末から遠ざかり中", -15
    else:
        return "➡️ 端末横を通過・並行移動中", 0

def check_lightning_alert(lat, lon, lookahead_hours=3):
    """
    Open-Meteo API を叩いてマルチグリッド一括取得、風向ベクトル判定、サージリスクスコア(%)を総合計算します。
    戻り値: (is_thunder_now, weather_code_desc, location_info_str, is_thunder_forecast, forecast_desc, clear_time_info, risk_score, approach_desc)
    """
    if lat is None or lon is None:
        return False, "位置情報未設定", "", False, "位置情報未設定", "", 0, "位置未設定"
        
    # マルチグリッド座標 (端末中心 + 東西南北 Offset 約 4km)
    delta = 0.04
    lats = [lat, lat + delta, lat - delta, lat, lat]
    lons = [lon, lon, lon, lon + delta, lon - delta]
    
    lat_str = ",".join(f"{x:.4f}" for x in lats)
    lon_str = ",".join(f"{x:.4f}" for x in lons)
    
    url = f"https://api.open-meteo.com/v1/forecast?latitude={lat_str}&longitude={lon_str}&current=weather_code,temperature_2m,wind_speed_10m,wind_direction_10m&hourly=weather_code"
    req = urllib.request.Request(
        url,
        headers={"User-Agent": "DualSleeper/1.0"}
    )
    try:
        with urllib.request.urlopen(req, timeout=10) as response:
            if response.status == 200:
                raw_json = json.loads(response.read().decode("utf-8"))
                # 複数地点のリクエストの場合、リストで返ってくる
                data_list = raw_json if isinstance(raw_json, list) else [raw_json]
                
                # 端末中心のデータ (インデックス 0)
                center_data = data_list[0]
                current_center = center_data.get("current", {})
                center_code = current_center.get("weather_code", -1)
                wind_speed = current_center.get("wind_speed_10m", 0.0)
                wind_deg = current_center.get("wind_direction_10m", 0.0)
                
                # マルチグリッドの各地点を解析
                grid_info = []
                is_thunder_now = False
                min_dist_km = 999.0
                closest_bearing = ""
                closest_lat, closest_lon = lat, lon
                
                for idx, d in enumerate(data_list):
                    res_lat = d.get("latitude", lats[idx])
                    res_lon = d.get("longitude", lons[idx])
                    curr = d.get("current", {})
                    code = curr.get("weather_code", -1)
                    
                    dist_km = calculate_distance_km(lat, lon, res_lat, res_lon)
                    bearing = calculate_bearing_16(lat, lon, res_lat, res_lon)
                    
                    if code in (95, 96, 99):
                        is_thunder_now = True
                        
                    if dist_km < min_dist_km:
                        min_dist_km = dist_km
                        closest_bearing = bearing
                        closest_lat, closest_lon = res_lat, res_lon
                        
                if min_dist_km < 0.1 or not closest_bearing:
                    loc_desc = "端末直近エリア"
                else:
                    loc_desc = f"端末から【{closest_bearing} 約 {min_dist_km:.1f} km】地点"
                
                # 予報解析 (センターグリッド)
                current_time_str = current_center.get("time", "")
                hourly_times = center_data.get("hourly", {}).get("time", [])
                hourly_codes = center_data.get("hourly", {}).get("weather_code", [])
                
                start_idx = 0
                if current_time_str and len(current_time_str) >= 13:
                    match_prefix = current_time_str[:13]
                    for i, t in enumerate(hourly_times):
                        if t.startswith(match_prefix):
                            start_idx = i
                            break
                            
                forecast_codes = hourly_codes[start_idx : start_idx + max(1, lookahead_hours) + 1]
                is_thunder_forecast = any(c in (95, 96, 99) for c in forecast_codes)
                forecast_desc = f"直近{lookahead_hours}時間以内に雷予報あり" if is_thunder_forecast else f"直近{lookahead_hours}時間内は雷予報なし"
                
                # 雷解除予想時刻のスキャン
                clear_time_info = ""
                if is_thunder_now or is_thunder_forecast:
                    scan_end = min(start_idx + 7, len(hourly_codes))
                    found_clear = False
                    for i in range(start_idx, scan_end):
                        if hourly_codes[i] not in (95, 96, 99):
                            t_raw = hourly_times[i] if i < len(hourly_times) else ""
                            t_str = t_raw.split("T")[1][:5] if "T" in t_raw else t_raw
                            clear_time_info = f"【 {t_str} 頃 】に雷が通過・解除される見込みです。"
                            found_clear = True
                            break
                    if not found_clear:
                        clear_time_info = "今後6時間以上雷が継続する見込みです。"
                        
                # 風向・移動ベクトル接近判定
                approach_desc, wind_risk_mod = calculate_wind_approach(lat, lon, closest_lat, closest_lon, wind_deg)
                
                # サージ影響度リスクスコア (0% - 100%) の統合算出
                # 基礎距離リスク
                if min_dist_km <= 1.0:
                    base_risk = 85.0
                elif min_dist_km <= 15.0:
                    base_risk = 85.0 * (1.0 - (min_dist_km - 1.0) / 14.0)
                else:
                    base_risk = 0.0
                    
                if is_thunder_now:
                    base_risk += 20.0
                elif is_thunder_forecast:
                    base_risk += 10.0
                    
                if is_thunder_now or is_thunder_forecast:
                    base_risk += wind_risk_mod
                    
                risk_score = max(0, min(100, int(round(base_risk))))
                if not is_thunder_now and not is_thunder_forecast and risk_score < 10:
                    risk_score = 0
                    approach_desc = "☀️ 平穏（雷の影響なし）"
                    
                weather_desc = f"⚡ 雷雨/落雷検知 (コード {center_code})" if is_thunder_now else f"正常 (コード {center_code})"
                
                return is_thunder_now, weather_desc, loc_desc, is_thunder_forecast, forecast_desc, clear_time_info, risk_score, approach_desc
    except Exception as e:
        return False, f"取得エラー: {e}", "", False, f"取得エラー: {e}", "", 0, f"取得エラー: {e}"
        
    return False, "データなし", "", False, "データなし", "", 0, "データなし"

def get_weather_report(config):
    """
    Open-Meteo API を叩いて、現在の天気、気温、風速・風向、落雷リスク、接近情報、落雷予報をフォーマットした Telegram 用 Markdown 文字列を返します。
    """
    lightning_cfg = config.get("lightning_protection", {})
    if not isinstance(lightning_cfg, dict):
        return "❌ 位置情報が未設定です。config.json の lightning_protection を確認してください。"
        
    lat, lon = parse_location(lightning_cfg)
    if lat is None or lon is None:
        return "❌ 位置情報が未設定です。config.json の location を確認してください。"
        
    fc_cfg = lightning_cfg.get("forecast_protection", {})
    fc_hours = fc_cfg.get("lookahead_hours", 3) if isinstance(fc_cfg, dict) else 3
    
    is_now, weather_desc, loc_desc, is_fc, forecast_desc, clear_info_str, risk_score, approach_desc = check_lightning_alert(lat, lon, lookahead_hours=fc_hours)
    
    # センターデータの詳細取得 (気温・風向・風速)
    url = f"https://api.open-meteo.com/v1/forecast?latitude={lat}&longitude={lon}&current=weather_code,temperature_2m,wind_speed_10m,wind_direction_10m"
    temp_str = "不明"
    wind_info_str = "不明"
    weather_str = weather_desc
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "DualSleeper/1.0"})
        with urllib.request.urlopen(req, timeout=10) as response:
            if response.status == 200:
                data = json.loads(response.read().decode("utf-8"))
                curr = data.get("current", {})
                code = curr.get("weather_code", -1)
                temp = curr.get("temperature_2m", None)
                w_speed = curr.get("wind_speed_10m", None)
                w_deg = curr.get("wind_direction_10m", None)
                
                weather_map = {
                    0: "☀️ 快晴", 1: "🌤️ 晴れ", 2: "⛅ 一部曇り", 3: "☁️ 曇り",
                    45: "🌫️ 霧", 48: "🌫️ 着氷性の霧",
                    51: "🚿 弱い小雨", 53: "🚿 小雨", 55: "🚿 強い小雨",
                    61: "☔ 弱い雨", 63: "☔ 雨", 65: "☔ 強い雨",
                    71: "❄️ 弱い雪", 73: "❄️ 雪", 75: "❄️ 強い雪",
                    80: "🌧️ にわか雨", 81: "🌧️ 強いにわか雨", 82: "🌧️ 激しいにわか雨",
                    95: "⚡ 雷雨", 96: "⚡ 雹を伴う雷雨", 99: "⚡ 激しい雷雨"
                }
                weather_str = weather_map.get(code, f"コード {code}")
                temp_str = f"{temp} °C" if temp is not None else "不明"
                
                if w_deg is not None and w_speed is not None:
                    w_bearing = calculate_bearing_16(0, 0, 0, 0) # ダミーではなく風向を文字化
                    dir_idx = int((float(w_deg) + 11.25) / 22.5) % 16
                    dirs = ["北", "北北東", "北東", "東北東", "東", "東南東", "南東", "南南東", "南", "南南西", "南西", "西南西", "西", "西北西", "北西", "北北西"]
                    w_dir_name = dirs[dir_idx]
                    wind_info_str = f"{w_speed} m/s (風向: {w_dir_name} {w_deg:.0f}°)"
    except Exception:
        pass
        
    thunder_status_str = "⚡️ **雷雨発生中！ (DANGER)**" if is_now else "☀️ **雷なし (NORMAL)**"
    forecast_status_str = f"⚡️ **雷予報あり (WARNING)**" if is_fc else "🌤️ **雷予報なし (CLEAR)**"
    
    clear_msg_part = f"\n🌤️ **解除見込み:** {clear_info_str}" if clear_info_str else ""
    
    pc_name = get_computer_name()
    return (
        f"🌩️ **[{pc_name}] 現在の天気・防災レポート**\n"
        f"📍 **観測地点:** {loc_desc}\n"
        f"🌤️ **天候:** {weather_str}\n"
        f"🌡️ **気温:** `{temp_str}` | **風速:** `{wind_info_str}`\n"
        f"⚡ **実況雷:** {thunder_status_str}\n"
        f"📊 **サージリスク度:** `{risk_score}%` ({approach_desc})\n"
        f"🔮 **直近{fc_hours}時間予報:** {forecast_status_str}{clear_msg_part}"
    )

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
        "idle_limit_seconds": 300,
        "network_limit_kbs": 20.0,
        "network_check_duration_seconds": 30,
        "check_interval_seconds": 5,
        "standby_after_monitor_off_seconds": 300,
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
        "gpu_protect_processes": ["python.exe", "python", "llama-server.exe", "llama-server"],
        "gpu_limit_percent": 10,
        "game_gpu_threshold_percent": 30,
        "high_network_limit_kbs": 625.0,
        "keep_awake_window_titles": ["youtube:20", "twitch", "zoom:60", "obs:360"],
        "server_mode": "off",
        "server_mode_standby_delay_seconds": 600,
        "wol_url": "",
        "lightning_protection": {
            "enabled": False,
            "location": "35.6812, 139.7671",
            "latitude": 35.6812,
            "longitude": 139.7671,
            "check_interval_seconds": 300,
            "auto_hibernate": "off",
            "forecast_protection": {
                "enabled": False,
                "lookahead_hours": 3
            }
        }
    }
    config_path = os.path.join(os.path.dirname(__file__), "config.json")
    if os.path.exists(config_path):
        try:
            with open(config_path, "r", encoding="utf-8") as f:
                lines = f.readlines()
            
            clean_lines = []
            for line in lines:
                stripped = line.strip()
                if stripped.startswith("//") or stripped.startswith("#"):
                    continue
                
                # 文字列リテラル内の // や # をコメントとして誤誤認しない堅牢な解析
                in_string = False
                clean_chars = []
                i = 0
                while i < len(line):
                    ch = line[i]
                    if ch == '"' and (i == 0 or line[i-1] != '\\'):
                        in_string = not in_string
                    elif not in_string and line[i:i+2] == "//":
                        break
                    elif not in_string and ch == '#':
                        break
                    clean_chars.append(ch)
                    i += 1
                clean_lines.append("".join(clean_chars))
                
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

def hotkey_worker():
    """HyperKey (Win + Ctrl + Shift + Alt + M) を監視するバックグラウンドスレッド"""
    global hotkey_state2_triggered
    try:
        user32 = ctypes.windll.user32
        HOTKEY_ID = 1001
        # MOD_ALT(1) | MOD_CONTROL(2) | MOD_SHIFT(4) | MOD_WIN(8) | MOD_NOREPEAT(0x4000) = 0x400F (16400)
        # 左右の修飾キー(L-Win/L-Ctrl/L-Alt/L-Shift 等)のどちらが送信されてもWindowsが確実に認識するように設定
        registered = user32.RegisterHotKey(None, HOTKEY_ID, 0x400F, 0x4D)
        if not registered:
            # バックアップ：MOD_NOREPEAT なしの 15 (0x0F) で再試行
            registered = user32.RegisterHotKey(None, HOTKEY_ID, 15, 0x4D)
            
        if registered:
            print(f"{get_timestamp()} [システム] グローバルホットキー登録完了: [Win + Ctrl + Shift + Alt + M] (即時トグル切替)")
            msg = ctypes.wintypes.MSG()
            while user32.GetMessageW(ctypes.byref(msg), None, 0, 0) != 0:
                if msg.message == 0x0312: # WM_HOTKEY
                    if msg.wParam == HOTKEY_ID:
                        hotkey_state2_triggered = True
                user32.TranslateMessage(ctypes.byref(msg))
                user32.DispatchMessageW(ctypes.byref(msg))
            user32.UnregisterHotKey(None, HOTKEY_ID)
        else:
            print(f"{get_timestamp()} [警告] グローバルホットキーの登録に失敗しました。他のアプリと競合している可能性があります。")
    except Exception as e:
        print(f"[警告] ホットキー監視スレッドでエラーが発生しました: {e}")

def telegram_worker(bot_token, chat_id, pc_name):
    """Telegramのロングポーリング受信を専門に行う非同期ワーカースレッドです。"""
    global force_power_mode, telegram_offset
    global current_state_num, current_idle_sec, current_net_speed, current_net_median_speed, current_net_max_speed, current_low_net_sec, current_gpu_util, current_media_force_until, current_status_reason
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
                    
                    # 1. sleep コマンドのハンドリング (トグル化)
                    if cmd in ("/sleep", "sleep"):
                        # 電源予約のトグルマップ (None -> sleep -> hibernate -> None)
                        next_power_modes = {
                            None: "sleep",
                            "sleep": "hibernate",
                            "hibernate": None
                        }
                        
                        # 引数が直接指定されている場合は優先適用
                        if len(text_parts) > 1:
                            sub_cmd = text_parts[1]
                            if sub_cmd in ("sleep", "s"):
                                force_power_mode = "sleep"
                            elif sub_cmd in ("hibernate", "h"):
                                force_power_mode = "hibernate"
                            elif sub_cmd in ("cancel", "c", "off", "none"):
                                force_power_mode = None
                            else:
                                force_power_mode = "invalid"
                        else:
                            # 引数なしはトグル
                            force_power_mode = next_power_modes.get(force_power_mode, None)
                            
                        if force_power_mode == "invalid":
                            reply_text = f"❌ **[{pc_name}]** 無効な予約モードです。`sleep` とだけ送信して切り替えてください。"
                        else:
                            mode_labels = {
                                "sleep": "強制スタンバイ (スリープ)",
                                "hibernate": "強制休止状態 (ハイバネート)",
                                None: "予約なし (時間帯制御)"
                            }
                            next_labels = {
                                "sleep": "強制休止状態 (ハイバネート)",
                                "hibernate": "予約なし (解除)",
                                None: "強制スタンバイ (スリープ)"
                            }
                            reply_text = (
                                f"🟢 **[{pc_name}] 電源予約設定**\n"
                                f"電源予約を `{mode_labels[force_power_mode]}` に変更しました。\n\n"
                                f"※次回 `sleep` と送信すると、次のモード (`{next_labels[force_power_mode]}`) に切り替わります。"
                            )
                            print(f"\n{get_timestamp()} [リモート予約] Telegramから電源予約変更を受信: {str(force_power_mode).upper()}")
                    
                    # 2. status コマンドのハンドリング（中央値・最高通信速度の表示拡張）
                    elif cmd in ("/status", "status"):
                        state_names = {0: "通常状態 (State 0)", 1: "通信監視中 (State 1)", 2: "消灯中 (State 2)"}
                        state_str = state_names.get(current_state_num, "不明")
                        
                        mode_labels = {
                            "sleep": "強制スタンバイ (スリープ)",
                            "hibernate": "強制休止状態 (ハイバネート)",
                            None: "なし (通常時間帯制御)"
                        }
                        mode_str = mode_labels.get(force_power_mode, "なし")
                        
                        config_tmp = load_config()
                        server_mode_val = get_server_mode_type(config_tmp)
                        server_labels = {
                            "off": "オフ (通常運用)",
                            "desktop": "デスクトップ時のみ有効",
                            "always": "常時適用"
                        }
                        server_str = server_labels.get(server_mode_val, "オフ")
                        
                        # 強制点灯状態の文字列生成
                        now_t = time.time()
                        if now_t < current_media_force_until and current_media_force_until > 0:
                            rem_sec = int(current_media_force_until - now_t)
                            media_str = f"有効 (残り {rem_sec} 秒)"
                        else:
                            media_str = "なし"
                        
                        # 通信速度テキスト（中央値・最高表示）
                        if current_state_num in (1, 2) and current_low_net_sec > 0:
                            net_str = f"中央値 {current_net_median_speed:.1f} KB/s (最高: {current_net_max_speed:.1f} KB/s)"
                        else:
                            net_str = f"{current_net_speed:.1f} KB/s (瞬間値)"
                        
                        reply_text = (
                            f"📊 **[{pc_name}] 現在のステータス**\n"
                            f"·状態: {state_str}\n"
                            f"·判定: `{current_status_reason}`\n"
                            f"·無操作時間: {current_idle_sec:.1f} 秒\n"
                            f"·通信速度: {net_str}\n"
                            f"·低通信継続: {current_low_net_sec:.1f} 秒\n"
                            f"·GPU使用率: {current_gpu_util} %\n"
                            f"·強制点灯: `{media_str}`\n"
                            f"·電源予約: `{mode_str}`\n"
                            f"·サーバモード: `{server_str}`"
                        )
                    
                    # 3. server コマンドのハンドリング
                    elif cmd in ("/server", "server"):
                        config = load_config()
                        current_mode = get_server_mode_type(config)
                        
                        # トグル遷移の定義 (off -> desktop -> always -> off)
                        next_modes = {
                            "off": "desktop",
                            "desktop": "always",
                            "always": "off"
                        }
                        
                        if len(text_parts) > 1:
                            sub_cmd = text_parts[1]
                            if sub_cmd in ("off", "desktop", "always"):
                                next_mode = sub_cmd
                            else:
                                next_mode = None
                        else:
                            next_mode = next_modes.get(current_mode, "off")
                            
                        if next_mode:
                            config["server_mode"] = next_mode
                            save_config(config)
                            
                            mode_labels = {
                                "off": "オフ (通常運用)",
                                "desktop": "デスクトップ時のみ有効",
                                "always": "常時適用"
                            }
                            
                            reply_text = (
                                f"⚙️ **[{pc_name}] サーバモード設定**\n"
                                f"サーバモードを `{mode_labels[next_mode]}` に変更・保存しました。\n\n"
                                f"※次回 `server` と単体で送信すると、次のモード (`{mode_labels[next_modes[next_mode]]}`) に切り替わります。"
                            )
                            print(f"\n{get_timestamp()} [リモート設定] Telegramからサーバモード変更を受信: {next_mode.upper()}")
                        else:
                            reply_text = f"❌ **[{pc_name}]** 無効なモードです。`off`, `desktop`, `always` から選択するか、`server` とだけ送信して切り替えてください。"
                    
                    # 4. weather コマンドのハンドリング
                    elif cmd in ("/weather", "weather", "tenki", "/tenki"):
                        config_tmp = load_config()
                        reply_text = get_weather_report(config_tmp)
                        print(f"\n{get_timestamp()} [リモート情報] Telegramから天気レポート要求を受信")
                    
                    # 5. 無効な入力（その他のメッセージ）に対するヘルプ自動応答 (古いコマンドは削除)
                    else:
                        reply_text = (
                            f"💡 **[{pc_name}] コマンドヘルプ**\n"
                            f"送信するたびに状態が切り替わるトグル式コマンドが便利です。\n\n"
                            f"📌 **トグルコマンド (送信するたびに順次切替)**\n"
                            f"· `sleep` : 電源予約の切替\n"
                            f"  (予約なし ➔ 強制スリープ ➔ 強制休止状態)\n"
                            f"· `server`: サーバモードの切替\n"
                            f"  (オフ ➔ デスクトップのみ ➔ 常時適用)\n\n"
                            f"📌 **通常コマンド**\n"
                            f"· `status` : 現在の稼働状況を確認する\n"
                            f"· `weather`: 現在の天気・気温・落雷情報を確認する"
                        )
                        
                    if reply_text:
                        send_telegram_notification(bot_token, chat_id, reply_text)
                        
        except Exception as e:
            # ネット切断等の一時的な例外は、ログを汚さないためスルーして5秒後に再試行
            time.sleep(5)

def main():
    global force_power_mode
    global current_state_num, current_idle_sec, current_net_speed, current_net_median_speed, current_net_max_speed, current_low_net_sec, current_gpu_util, current_media_force_until, current_status_reason
    global is_sleep_pending, telegram_extend_request, hotkey_state2_triggered, last_hotkey_time

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
            send_telegram_notification(telegram_token, test_message)
            
        print("テストメッセージの送信を試みました。スマホや各アプリを確認してください。")
        sys.exit(0)

    print("=" * 60)
    print("""
Dual Sleeper v1.0.0
====================
AI学習サーバー・リモートPC向け インテリジェント電源＆モニター管理システム
""")
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
    
    # コントローラー入力監視状態の出力
    if xinput_dll:
        print("  ・コントローラー監視  : 有効 (XInput非同期チェック機能付き)")
    else:
        print("  ・コントローラー監視  : 非対応 (XInput DLL未検出)")
        
    # WASAPI オーディオセッション監視の出力
    print("  ・オーディオセッション監視: 有効 (WASAPI 通話/音声ストリーム保護)")
        
    # WoL URLの設定出力
    wol_link_url = config.get("wol_url", "").strip()
    if wol_link_url:
        print(f"  ・WoL遠隔起動リンク   : 設定済み ({wol_link_url[:40]}...)")
    else:
        print("  ・WoL遠隔起動リンク   : 未設定")
        
    # 落雷保護アラートの設定出力
    lightning_cfg = config.get("lightning_protection", {})
    if isinstance(lightning_cfg, dict) and lightning_cfg.get("enabled", False):
        lat, lon = parse_location(lightning_cfg)
        interval = lightning_cfg.get("check_interval_seconds", 300)
        hib_mode = get_auto_hibernate_mode(lightning_cfg)
        mode_labels = {
            "off": "スマホ通知＆選択",
            "state2_only": "消灯/放置中(State 2)のみ問答無用自動休止",
            "always": "常時問答無用自動休止"
        }
        hib_label = mode_labels.get(hib_mode, "スマホ通知＆選択")
        
        fc_cfg = lightning_cfg.get("forecast_protection", {})
        fc_enabled = isinstance(fc_cfg, dict) and fc_cfg.get("enabled", False)
        fc_hours = fc_cfg.get("lookahead_hours", 3) if isinstance(fc_cfg, dict) else 3
        fc_label = f"有効 (直近{fc_hours}時間内の雷予報で離席スリープを自動休止化)" if fc_enabled else "無効 (初期無効)"
        
        print(f"  ・落雷保護アラート    : 有効 (位置: {lat}, {lon} | 周期: {interval}秒 | モード: {hib_mode} -> {hib_label})")
        print(f"  ・落雷予報連動休止    : {fc_label}")
        print("    💡 [ワンポイント] 関東等の落雷ピークは「7月〜8月の14:00〜18:00」です。この時期の常用を強く推奨します。")
    else:
        print("  ・落雷保護アラート    : 無効 (初期無効)")
        print("    💡 [ワンポイント] 関東等の落雷ピークは「7月〜8月の14:00〜18:00」です。夏季は config.json で有効化を推奨します。")
    
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
    
    # 点灯延長対象タイトルの出力
    keep_awake_kw = config.get("keep_awake_window_titles", [])
    if keep_awake_kw:
        print(f"  ・点灯延長対象タイトル: {', '.join(keep_awake_kw)}")
    else:
        print("  ・点灯延長対象タイトル: なし")
        
    # ダウンロードフォルダの自動取得
    downloads_dir = get_downloads_folder()
    print(f"  ・ダウンロードフォルダ: {downloads_dir}")
    print("=" * 60)
    print("【キーボード操作】 h:電源予約切替 | s:サーバモード切替 | [Win+Ctrl+Shift+Alt+M]:即時トグル切替")
    print("【リモート操作】   Telegram Bot から /sleep, /status, /server が利用可能")
    print("=" * 60)
    print("監視を開始します。終了するには Ctrl+C を押してください。\n")

    # グローバルホットキー監視スレッドの起動
    hk_thread = threading.Thread(target=hotkey_worker, daemon=True)
    hk_thread.start()

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
    last_state = -1 # 状態遷移検知用
    
    low_net_start_time = None
    low_net_standby_start_time = None
    monitor_off_input_time = None
    last_wakeup_time = time.time()
    last_controller_input_time = 0.0
    
    # 通信監視区間・消灯区間の速度統計（中央値・最高計算用）
    interval_speeds = []
    
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

    # 落雷保護監視用変数
    last_lightning_check_time = 0
    lightning_alert_active = False
    is_lightning_forecast_risk = False
    current_clear_time_info = ""

    try:
        while True:
            # 5秒の監視ループを 0.1秒単位の超爆速ループに分割し、キーレスポンスを向上
            check_interval = config.get('check_interval_seconds', 5)
            sub_loops = int(check_interval / 0.1)
            
            for _ in range(max(1, sub_loops)):
                # コントローラー(XInput)の操作検知をリアルタイムチェック
                if check_controller_activity():
                    last_controller_input_time = time.time()

                # ===== グローバルホットキー (Win + Ctrl + Shift + Alt + M) トグル判定 =====
                if hotkey_state2_triggered:
                    hotkey_state2_triggered = False
                    now_t = time.time()
                    
                    # 1.5秒以内の連続・リピート入力を完全にガード（連打による「点灯➔即消灯」の防止）
                    if now_t - last_hotkey_time >= 1.5:
                        last_hotkey_time = now_t
                        # 手動消灯の時はメディア強制点灯モードも完全に打ち切る
                        media_force_on_until = 0
                        current_media_force_until = 0.0
                        last_detected_media_title = ""
                        interval_speeds.clear()
                        
                        if state == 2:
                            # 消灯中 (State 2) の場合は、モニターを確実に点灯し State 0 (通常状態) へ復帰
                            print(f"\n{get_timestamp()} [ホットキー] HyperKey 検知: モニターを点灯し「通常状態 (State 0)」へ復帰します。")
                            turn_on_monitor()
                            state = 0
                            last_wakeup_time = time.time()
                            net_monitor.get_speed()
                            extended_standby_limit = 0
                            force_power_mode = None
                        else:
                            # 点灯中 (State 0 または 1) の場合は、即座にモニターを消灯して State 2 へ遷移
                            print(f"\n{get_timestamp()} [ホットキー] HyperKey 検知: 即座にモニターを消灯し「消灯状態 (State 2)」へ遷移します。")
                            turn_off_monitor()
                            time.sleep(1.0)
                            state = 2
                            monitor_off_input_time = get_last_input_time_raw()
                            last_mouse_x, last_mouse_y = get_mouse_position()
                            low_net_standby_start_time = None
                    break # インナーループを出てメイン処理へ

                # 常に非同期でローカルのキーボード入力をチェック (即時反映)
                while msvcrt.kbhit():
                    try:
                        char_code = msvcrt.getch()
                        if char_code in (b'\x00', b'\xe0'):
                            msvcrt.getch()
                            continue
                        ch = char_code.decode("utf-8").lower()
                        
                        if ch == "h":
                            # 電源手動予約のトグル切り替え (None -> sleep -> hibernate -> None)
                            next_power_modes = {
                                None: "sleep",
                                "sleep": "hibernate",
                                "hibernate": None
                            }
                            force_power_mode = next_power_modes.get(force_power_mode, None)
                            
                            if force_power_mode == "sleep":
                                print(f"\n{get_timestamp()} [手動予約] 次回スリープ移行時、強制的に「スタンバイ (スリープ)」を実行します。(復帰時にリセット)")
                            elif force_power_mode == "hibernate":
                                print(f"\n{get_timestamp()} [手動予約] 次回スリープ移行時、強制的に「休止状態 (ハイバネート)」を実行します。(復帰時にリセット)")
                            else:
                                print(f"\n{get_timestamp()} [手動予約] 予約された電源モードを解除しました。(通常設定の時間帯制御に戻ります)")
                                
                        elif ch == "s":
                            # サーバモード設定のトグル切り替え (off -> desktop -> always -> off)
                            config = load_config()
                            current_mode = get_server_mode_type(config)
                            next_server_modes = {
                                "off": "desktop",
                                "desktop": "always",
                                "always": "off"
                            }
                            next_mode = next_server_modes.get(current_mode, "off")
                            
                            config["server_mode"] = next_mode
                            save_config(config)
                            
                            mode_labels = {
                                "off": "オフ (通常運用)",
                                "desktop": "デスクトップ時のみ有効",
                                "always": "常時適用"
                            }
                            print(f"\n{get_timestamp()} [設定変更] サーバモードを「{mode_labels[next_mode]}」に変更しました。")
                    except Exception:
                        pass
                
                time.sleep(0.1)

            # ===== 【落雷保護アラートチェック】 =====
            lightning_cfg = config.get("lightning_protection", {})
            if isinstance(lightning_cfg, dict) and lightning_cfg.get("enabled", False):
                lat, lon = parse_location(lightning_cfg)
                interval = lightning_cfg.get("check_interval_seconds", 300)
                
                fc_cfg = lightning_cfg.get("forecast_protection", {})
                fc_enabled = isinstance(fc_cfg, dict) and fc_cfg.get("enabled", False)
                fc_hours = fc_cfg.get("lookahead_hours", 3) if isinstance(fc_cfg, dict) else 3
                
                if time.time() - last_lightning_check_time >= interval:
                    last_lightning_check_time = time.time()
                    is_thunder, thunder_msg, loc_desc, is_fc_thunder, fc_msg, clear_time_info, risk_score, approach_desc = check_lightning_alert(lat, lon, lookahead_hours=fc_hours)
                    current_clear_time_info = clear_time_info
                    
                    if fc_enabled:
                        if is_fc_thunder and not is_lightning_forecast_risk:
                            is_lightning_forecast_risk = True
                            print(f"\n{get_timestamp()} [雷予報検知] ⚡ {fc_msg} が検出されたため、離席スリープの動作を「休止状態（ハイバネート）」へ一時昇格します。({clear_time_info} | サージリスク: {risk_score}%)")
                        elif not is_fc_thunder and is_lightning_forecast_risk:
                            is_lightning_forecast_risk = False
                            print(f"\n{get_timestamp()} [雷予報解除] 直近{fc_hours}時間内の雷予報がなくなったため、スリープ動作の昇格を解除しました。")
                    else:
                        is_lightning_forecast_risk = False
                    
                    if is_thunder:
                        if not lightning_alert_active:
                            lightning_alert_active = True
                            hib_mode = get_auto_hibernate_mode(lightning_cfg)
                            should_auto_hibernate = (
                                hib_mode == "always" or 
                                (hib_mode == "state2_only" and state == 2)
                            )
                            
                            loc_info = f"\n📍 検知位置: {loc_desc}" if loc_desc else ""
                            clear_info = f"\n🌤️ **解除見込み:** {clear_time_info}" if clear_time_info else ""
                            risk_info = f"\n📊 **サージリスク度:** `{risk_score}%` ({approach_desc})"
                            
                            if should_auto_hibernate:
                                mode_reason = "always (常時自動)" if hib_mode == "always" else "state2_only (消灯/放置中自動)"
                                print(f"\n{get_timestamp()} [落雷自動退避] ⚡ {loc_desc or '端末周辺'}で雷雨/落雷が検知されたため、auto_hibernate設定 ({mode_reason}) に従い「休止状態（ハイバネート）」へ問答無用で移行します！({thunder_msg} | リスク:{risk_score}% | {clear_time_info})")
                                send_notifications(
                                    config,
                                    f"⚡ **[{pc_name}] 【落雷自動退避通知】**\n"
                                    f"登録地点の周辺で雷雨・落雷が検知されました！{loc_info}{risk_info}{clear_info}\n\n"
                                    f"⚡ `auto_hibernate: \"{hib_mode}\"` 設定に従い、PCおよびデータを雷サージから保護するため直ちに「休止状態（ハイバネート）」へ自動移行します。"
                                )
                                time.sleep(3.0) # 通知送信完了待ち
                                execute_power_command(use_hibernate=True)
                            else:
                                print(f"\n{get_timestamp()} [落雷警報] ⚡ {loc_desc or '端末周辺'}で雷雨/落雷が検知されました！({thunder_msg} | リスク:{risk_score}% | {clear_time_info})")
                                send_notifications(
                                    config,
                                    f"⚡ **[{pc_name}] 【落雷警報アラート】**\n"
                                    f"登録地点の周辺で雷雨・落雷が検知されました！{loc_info}{risk_info}{clear_info}\n\n"
                                    f"雷サージからPCおよびデータを保護するため、休止状態（ハイバネート）に移行しますか？\n"
                                    f"スマホから「1」または「h」と返信すると、直ちに休止状態（ハイバネート）を予約・実行します。（または /sleep hibernate）"
                                )
                    else:
                        if lightning_alert_active:
                            lightning_alert_active = False
                            print(f"\n{get_timestamp()} [落雷警報解除] 端末周辺の雷雨/落雷警報が解除されました。")

            # 状態遷移 (State 変更) が発生した時だけ、通信統計データを初期化する
            if state != last_state:
                interval_speeds.clear()
                last_state = state

            # 常にネットワーク速度を更新しておく（正確な差分計測のため）
            speed = net_monitor.get_speed()
            
            # 物理的な無操作時間（キーボード・マウス）を取得
            physical_idle = get_idle_duration()
            current_time = time.time()
            physical_active_time = current_time - physical_idle
            
            # 設定を毎ループ再読み込み（稼働中に設定変更できるようにする）
            config = load_config()

            # ===== 【新機能】アクティブウィンドウのメディアファイルおよび登録タイトル検知 =====
            current_title = get_active_window_title()
            has_media = any(ext in current_title for ext in media_extensions)
            
            # config.json に登録された点灯延長対象タイトルのキーワード判定
            keep_awake_kw = config.get("keep_awake_window_titles", [])
            has_custom_kw = False
            custom_duration = 600.0 # デフォルト10分 (600秒)
            
            for item in keep_awake_kw:
                if not item:
                    continue
                item_str = str(item).strip()
                if ":" in item_str:
                    parts = item_str.split(":", 1)
                    kw = parts[0].strip().lower()
                    try:
                        # 整数または小数(分)を秒に変換
                        duration = float(parts[1].strip()) * 60.0
                    except ValueError:
                        duration = 600.0
                else:
                    kw = item_str.lower()
                    duration = 600.0
                
                if kw and kw in current_title:
                    has_custom_kw = True
                    custom_duration = duration
                    break # 最初に一致したものの設定を適用

            if has_media or has_custom_kw:
                # 前回の検知ファイル/キーワードからタイトル名が変わった（＝新しく開いた・別動画にした）瞬間にのみタイマーを設定する
                if current_title != last_detected_media_title:
                    last_detected_media_title = current_title
                    # 指定された延長時間（秒）をセット（デフォルトは10分）
                    target_duration = 600.0 if has_media else custom_duration
                    media_force_on_until = time.time() + target_duration
                    current_media_force_until = media_force_on_until
                    print(f"\n{get_timestamp()} [メディア/登録タイトル検知] 点灯延長対象（...{current_title[-40:]}）のオープンを検知しました。{int(target_duration // 60)}分間 ({int(target_duration)}秒) の強制点灯モードに入ります。")
            else:
                # 対象ウィンドウが非アクティブ（閉じられた・別のウィンドウへ移動）の時はクリア
                if media_force_on_until > 0 and (last_detected_media_title and last_detected_media_title not in current_title):
                    print(f"\n{get_timestamp()} [状態遷移] 対象ウィンドウが閉じられたか非アクティブになったため、強制点灯モードを終了します。")
                    media_force_on_until = 0
                    current_media_force_until = 0.0
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
                raw_standby_limit = config.get("server_mode_standby_delay_seconds", 600) # 設定値から動的取得
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
                if not (has_media or has_custom_kw) or (last_detected_media_title and last_detected_media_title not in current_title):
                    print(f"\n{get_timestamp()} [状態遷移] メディアウィンドウのクローズまたは非アクティブ化を検知したため、強制点灯を解除して通常監視（State 0）へ移行します。")
                    media_force_on_until = 0
                    current_media_force_until = 0.0
                    last_detected_media_title = ""
                    state = 0
                    last_wakeup_time = time.time()
                    net_monitor.get_speed()
                    continue

                # 10分間はすべての操作チェックや省エネ状態への遷移を完全に無視する
                last_wakeup_time = time.time() # 監視タイマーの基点を現在にし続ける
                current_state_num = 0
                current_idle_sec = 0.0
                current_net_speed = speed
                current_net_median_speed = speed
                current_net_max_speed = speed
                current_low_net_sec = 0.0
                current_media_force_until = media_force_on_until
                current_status_reason = "🎬 メディア/登録タイトル再生中"
                
                # GPUステータスの更新
                gpu_limit = config.get("gpu_limit_percent", 0)
                gpu_procs = config.get("gpu_protect_processes", [])
                gpu_util, gpu_protect_active = get_gpu_status(gpu_procs)
                current_gpu_util = gpu_util
                
                mode_status = f" | 予約: {force_power_mode.upper() if force_power_mode else 'なし'}"
                print(f"\r{get_timestamp()} [メディア強制点灯中] 残り時間: {int(media_force_on_until - current_time)}秒 | 通信: {speed:.1f} KB/s{mode_status}  ", end="", flush=True)
                continue
            elif media_force_on_until > 0:
                # ちょうど指定時間が満了した瞬間
                media_force_on_until = 0 # タイマーをクリア
                current_media_force_until = 0.0
                last_detected_media_title = ""
                state = 1 # 直接「通信監視状態 (State 1)」へ遷移！
                low_net_start_time = time.time() # 通信量の監視を開始
                # 無操作時間はすでに満了しているものとして偽装（ダミー時刻セット）
                last_wakeup_time = time.time() - config['idle_limit_seconds']
                print(f"\n{get_timestamp()} [状態遷移] メディア強制点灯時間が終了しました。放置の可能性があるため、通信監視状態（State 1）へダイレクト移行します。")
                continue
            else:
                current_media_force_until = 0.0

            # 物理入力(KB/マウス/コントローラー)の時刻と、モニター復帰時刻の最も新しいものを最終アクティブ時間とする
            effective_active_time = max(physical_active_time, last_wakeup_time, last_controller_input_time)
            idle_sec = current_time - effective_active_time
            
            # 現在の低通信継続時間の計算および State 内での継続的な通信速度統計（中央値・最高）の集計
            if state in (1, 2) and ((state == 1 and low_net_start_time is not None) or (state == 2 and low_net_standby_start_time is not None)):
                interval_speeds.append(speed)
                median_sp = calculate_median(interval_speeds)
                max_sp = max(interval_speeds) if interval_speeds else speed
                
                if state == 1:
                    current_low_net_sec = time.time() - low_net_start_time
                else:
                    current_low_net_sec = time.time() - low_net_standby_start_time
            else:
                median_sp = speed
                max_sp = speed
                current_low_net_sec = 0.0

            current_net_median_speed = median_sp
            current_net_max_speed = max_sp

            # GPUステータスおよびWASAPIオーディオセッション測定
            gpu_limit = config.get("gpu_limit_percent", 0)
            gpu_procs = config.get("gpu_protect_processes", [])
            gpu_util, gpu_protect_active = get_gpu_status(gpu_procs)
            current_gpu_util = gpu_util

            # WASAPI オーディオセッション（Discord/LINE等の通話・音声ストリーム）のチェック
            is_audio_active = is_audio_session_active()

            # 判定状態(current_status_reason)の動的算出（Telegram/コンソール共通）
            is_gpu_busy_with_python = (gpu_limit > 0 and gpu_util >= gpu_limit and gpu_protect_active)
            high_net_limit = config.get("high_network_limit_kbs", 625.0)
            normal_net_limit = config.get("network_limit_kbs", 20.0)

            # ゲームGPU判定の閾値（GPU使用率30%以上を「ゲーム等のGPU使用放置」とみなす）
            game_gpu_threshold = config.get("game_gpu_threshold_percent", 30)

            if is_gpu_busy_with_python:
                current_status_reason = f"🤖 AI利用中 (Python GPU: {gpu_util}%)"
            elif is_audio_active and state == 2:
                current_status_reason = f"🎙️ 通話/音声ストリーム検知中 (スリープ保護)"
            elif speed >= high_net_limit:
                current_status_reason = f"📡 ゲーム配信中 (高トラフィック: {speed:.1f} KB/s)"
            elif state == 2 and speed > normal_net_limit:
                current_status_reason = f"🔄 パルス通信検知 ({speed:.1f} KB/s)"
            elif state == 2:
                if gpu_util >= game_gpu_threshold:
                    current_status_reason = f"🎮 ゲーム放置中 (スリープ待機)"
                else:
                    current_status_reason = f"💤 放置中 (スリープ待機)"
            elif state == 1:
                current_status_reason = f"🔍 通信監視中 (低通信待機)"
            else:
                current_status_reason = f"💻 通常稼働中"

            # グローバルステータスの更新
            current_state_num = state
            current_idle_sec = idle_sec
            current_net_speed = speed
            
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
                    print(f"\r{get_timestamp()} [通信監視中] 低通信継続: {elapsed_low_net:.1f}/{net_check_duration}秒 | 中央通信: {median_sp:.1f} KB/s (最高: {max_sp:.1f}){dl_status}  ", end="", flush=True)
                    
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

                # 2. スタンバイ判定のためのネットワーク監視、GPU監視、およびオーディオセッション監視
                if standby_limit > 0:
                    # 消灯中にパルス通信（通常通信しきい値 20 KB/s 超え）または WASAPI オーディオストリーム（通話等）を検知した場合
                    # スリープ待機タイマーを即座にリセット（0秒に戻し、再び5分間の猶予を確保する）
                    if speed > normal_net_limit or is_audio_active:
                        if low_net_standby_start_time is not None:
                            reason_str = "🎙️ 通話/音声ストリーム" if is_audio_active else f"🔄 パルス通信 ({speed:.1f} KB/s)"
                            print(f"\n{get_timestamp()} [タイマーリセット] {reason_str} を検知したためスリープタイマーをリセットしました。")
                        low_net_standby_start_time = time.time()

                    # ファイルダウンロード中であるかチェック
                    is_downloading = is_downloading_active(downloads_dir)
                    
                    # スリープ禁止時間帯（モニター消灯のみ）かチェック
                    is_no_sleep = is_no_sleep_time(config.get("no_sleep_start_hour"), config.get("no_sleep_end_hour"))
                    
                    # 【スリープを許可する条件】
                    # ※オーディオセッションアクティブ中(is_audio_active) もスリープを阻害保護する
                    allow_sleep = (not is_gpu_busy_with_python) and (speed < high_net_limit) and (not is_downloading) and (not is_no_sleep) and (not is_audio_active)
                    
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
                        state_label = "🎮 ゲーム放置中" if gpu_util >= game_gpu_threshold else "💤 放置中"
                        print(f"\r{get_timestamp()} [モニターOFF] {state_label}(スリープ待機: {elapsed_low_net_standby:.1f}/{standby_limit}秒) | 中央通信: {median_sp:.1f} KB/s (最高: {max_sp:.1f}) | GPU: {gpu_util}%  ", end="", flush=True)
                        
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
                            elif is_lightning_forecast_risk:
                                use_hibernate = True
                                fc_hours = config.get("lightning_protection", {}).get("forecast_protection", {}).get("lookahead_hours", 3)
                                mode_desc = f"雷予報連動 (直近{fc_hours}時間内) により、「休止状態（自動昇格）」"
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
                                
                                wol_link_url = config.get("wol_url", "").strip()
                                wol_msg_part = ""
                                if wol_link_url:
                                    wol_msg_part = f"\n\n🔗 **[Wake on LAN 遠隔起動リンク]**\nPCを起こしたくなった場合は以下をタップ:\n{wol_link_url}"
                                    
                                # スリープ決定時点のState 2詳細ステータス文字列を作成
                                status_details_msg = (
                                    f"📊 **[決定時のステータス]**\n"
                                    f"·判定: `{current_status_reason}`\n"
                                    f"·通信速度: 中央値 {median_sp:.1f} KB/s (最高: {max_sp:.1f} KB/s)\n"
                                    f"·低通信継続: {elapsed_low_net_standby:.1f} 秒 (待機完了)\n"
                                    f"·GPU使用率: {gpu_util} %\n"
                                    f"·電源予約: `{force_power_mode.upper() if force_power_mode else 'なし'}`"
                                )

                                weather_clear_msg = f"\n\n🌤️ **落雷/雷予報 解除見込み:**\n{current_clear_time_info}" if current_clear_time_info else ""

                                send_notifications(
                                    config,
                                    f"🔔 **[{pc_name}] まもなく {mode_name} に移行します。**\n"
                                    f"({mode_desc})\n\n"
                                    f"{status_details_msg}{weather_clear_msg}\n\n"
                                    f"スマホから何か文字・数字を送信すると、移行を一時的に10分間延長（モニター消灯状態維持）します。{wol_msg_part}"
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
                                print(f"\n{get_timestamp()} [情報] スリープ禁止時間帯のためスリープタイマーをリセットします。")
                            elif is_audio_active:
                                print(f"\n{get_timestamp()} [情報] 🎙️ 通話/音声ストリーム検知中のためスリープタイマーをリセットします。")
                            elif is_gpu_busy_with_python:
                                print(f"\n{get_timestamp()} [情報] 🤖 AI利用中 (Python GPU: {gpu_util}%) を検知したためスリープタイマーをリセットします。")
                            elif is_downloading:
                                print(f"\n{get_timestamp()} [情報] ファイルダウンロード中を検知したためスリープタイマーをリセットします。")
                            elif speed >= high_net_limit:
                                print(f"\n{get_timestamp()} [情報] 📡 ゲーム配信中 (高トラフィック: {speed:.1f} KB/s) を検知したためスリープタイマーをリセットします。")
                        low_net_standby_start_time = None
                        
                        if is_no_sleep:
                            print(f"\r{get_timestamp()} [モニターOFF] スリープ禁止時間帯(モニター消灯のみ維持)... | 通信: {speed:.1f} KB/s  ", end="", flush=True)
                        elif is_audio_active:
                            print(f"\r{get_timestamp()} [モニターOFF] 🎙️ 通話/音声ストリーム検知中 (スリープ保護) | 通信: {speed:.1f} KB/s  ", end="", flush=True)
                        elif is_gpu_busy_with_python:
                            print(f"\r{get_timestamp()} [モニターOFF] 🤖 AI利用中 (Python GPU: {gpu_util}%) | 通信: {speed:.1f} KB/s  ", end="", flush=True)
                        elif is_downloading:
                            print(f"\r{get_timestamp()} [モニターOFF] ファイルダウンロード中... | 通信: {speed:.1f} KB/s  ", end="", flush=True)
                        elif speed >= high_net_limit:
                            print(f"\r{get_timestamp()} [モニターOFF] 📡 ゲーム配信中 (高トラフィック: {speed:.1f} KB/s) | 通信待機  ", end="", flush=True)
                        else:
                            print(f"\r{get_timestamp()} [モニターOFF] 通信待機中... | 通信: {speed:.1f} KB/s | GPU: {gpu_util}%  ", end="", flush=True)

    except KeyboardInterrupt:
        print("\n監視プログラムを終了しました。")
        # 終了時に念のためモニターをオンにする命令を送る
        turn_off_monitor()

if __name__ == "__main__":
    main()
