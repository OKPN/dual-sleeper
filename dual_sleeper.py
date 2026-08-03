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
import html
import base64
import atexit
import signal
import re

# ==============================================================================
# 電源プロファイル (Power Scheme) 管理用グローバル変数 ＆ 排他制御ロック
# ==============================================================================
state_lock = threading.Lock()
original_power_plan_guid = None
original_power_plan_name = None
is_power_saver_applied = False
is_ultimate_plan_applied = False
is_ai_plan_applied = False
is_cpu_plan_applied = False

def get_active_power_scheme():
    """現在アクティブな電源プランの (GUID, 名前) を取得します。"""
    if os.name != 'nt':
        return None, None
    try:
        res = subprocess.run(["powercfg", "/getactivescheme"], capture_output=True, check=True)
        try:
            out = res.stdout.decode('cp932').strip()
        except Exception:
            out = res.stdout.decode('utf-8', errors='replace').strip()
        match = re.search(r"GUID:\s+([a-fA-F0-9\-]+)\s+\((.+)\)", out)
        if match:
            return match.group(1).lower(), match.group(2)
    except Exception:
        pass
    return None, None

def get_power_scheme_by_keyword(keyword="省電力"):
    """指定されたキーワード (例: 省電力, 究極, 高パフォーマンス) に該当する電源プランの GUID を検索して返します。"""
    if os.name != 'nt':
        return None
    try:
        res = subprocess.run(["powercfg", "/list"], capture_output=True, check=True)
        try:
            out = res.stdout.decode('cp932').strip()
        except Exception:
            out = res.stdout.decode('utf-8', errors='replace').strip()
            
        default_saver_guid = "a1841308-3541-4fab-bc81-f71556f20b4a"
        default_high_guid = "8c5e7fda-e8bf-4a96-9a85-a6e23a8c635c"
        default_ultimate_guid = "e9a42b02-d5df-448d-aa00-03f14749eb61"
        
        # 1. 優先ワード検索
        for line in out.splitlines():
            match = re.search(r"GUID:\s+([a-fA-F0-9\-]+)\s+\((.+)\)", line)
            if match:
                g = match.group(1).lower()
                name = match.group(2).lower()
                kw = keyword.lower()
                if kw in name:
                    return g
                if kw in ("究極", "ultimate") and ("究極" in name or "ultimate" in name or g == default_ultimate_guid):
                    return g
                if kw in ("高パフォーマンス", "high") and ("高パフォーマンス" in name or "high" in name or g == default_high_guid):
                    return g
                    
        # 2. フォールバック検索 (究極が見つからなければ高パフォーマンス)
        if keyword in ("究極", "ultimate"):
            for line in out.splitlines():
                match = re.search(r"GUID:\s+([a-fA-F0-9\-]+)\s+\((.+)\)", line)
                if match:
                    g = match.group(1).lower()
                    name = match.group(2).lower()
                    if "高パフォーマンス" in name or "high" in name or g == default_high_guid:
                        return g
            return default_high_guid
        elif keyword in ("省電力", "saver"):
            return default_saver_guid
    except Exception:
        pass
    return None

def set_power_scheme(guid):
    """指定された GUID の電源プランへ一瞬で切り替えます。（※サインイン等のセキュリティ設定には一切介入しません）"""
    if os.name != 'nt' or not guid:
        return False
    try:
        flags = subprocess.CREATE_NO_WINDOW if os.name == 'nt' else 0
        subprocess.run(["powercfg", "/setactive", guid], creationflags=flags, check=True)
        return True
    except Exception as e:
        return False

def restore_original_power_scheme():
    """起動時に保存した元の電源プロファイルへ 100% 確実に復元します。"""
    global original_power_plan_guid, original_power_plan_name, is_power_saver_applied, is_ultimate_plan_applied, is_ai_plan_applied
    if (is_power_saver_applied or is_ultimate_plan_applied or is_ai_plan_applied) and original_power_plan_guid:
        if set_power_scheme(original_power_plan_guid):
            name_str = f" ({original_power_plan_name})" if original_power_plan_name else ""
            print(f"\n{get_timestamp()} [電源プロファイル復元] 元の電源プラン{name_str} へ安全に自動復元しました。")
            is_power_saver_applied = False
            is_ultimate_plan_applied = False
            is_ai_plan_applied = False
            is_cpu_plan_applied = False

# atexit に登録してアプリ終了時に確実に元に戻す
atexit.register(restore_original_power_scheme)

def _signal_handler(signum, frame):
    restore_original_power_scheme()
    sys.exit(0)

try:
    signal.signal(signal.SIGINT, _signal_handler)
    signal.signal(signal.SIGTERM, _signal_handler)
except Exception:
    pass

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
current_net_baseline_speed = 0.0
current_net_dynamic_limit = 20.0
current_low_net_sec = 0.0
current_gpu_util = 0
current_media_force_until = 0.0
current_status_reason = "通常"
telegram_offset = 0

# Telegram割り込みスリープ延長用グローバル変数
is_sleep_pending = False
telegram_extend_request = False

# 落雷保護警戒グローバル変数
lightning_alert_active = False
is_lightning_forecast_risk = False

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
                res = ctypes.windll.powrprof.SetSuspendState(0, 0, 0)
            return bool(res)
        else:
            res = ctypes.windll.powrprof.SetSuspendState(0, 0, 0)
            return bool(res)
    except Exception as e:
        print(f"{get_timestamp()} [警告] 電源状態の変更に失敗しました: {e}")
        return False

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

_LOCATION_CACHE = {}

def parse_location_info(lightning_cfg):
    """
    lightning_protection 設定辞書から location を解析します。
    数字カンマ区切り (例: "35.6812, 139.7671") または 日本語テキスト住所 (例: "東京都千代田区") の両方に対応。
    戻り値: (lat, lon, is_address_mode, display_name)
    """
    if not isinstance(lightning_cfg, dict):
        return None, None, False, ""
        
    raw_loc = str(lightning_cfg.get("location", "")).strip()
    if not raw_loc:
        lat = lightning_cfg.get("latitude")
        lon = lightning_cfg.get("longitude")
        if lat is not None and lon is not None:
            try:
                return float(lat), float(lon), False, ""
            except (ValueError, TypeError):
                pass
        return None, None, False, ""
        
    if raw_loc in _LOCATION_CACHE:
        return _LOCATION_CACHE[raw_loc]
        
    # カンマまたはスペース区切りの数値判定
    parts = [p.strip() for p in raw_loc.replace(",", " ").split() if p.strip()]
    if len(parts) >= 2:
        try:
            lat = float(parts[0])
            lon = float(parts[1])
            res = (lat, lon, False, "")
            _LOCATION_CACHE[raw_loc] = res
            return res
        except ValueError:
            pass
            
    # テキスト住所の正ジオコーディング (Nominatim API)
    try:
        q = urllib.parse.quote(raw_loc)
        url = f"https://nominatim.openstreetmap.org/search?q={q}&format=json&accept-language=ja&limit=1"
        req = urllib.request.Request(url, headers={"User-Agent": "DualSleeper/1.0"})
        with urllib.request.urlopen(req, timeout=5) as response:
            if response.status == 200:
                data = json.loads(response.read().decode("utf-8"))
                if data and isinstance(data, list) and len(data) > 0:
                    lat = float(data[0]["lat"])
                    lon = float(data[0]["lon"])
                    display_name = raw_loc
                    res = (lat, lon, True, display_name)
                    _LOCATION_CACHE[raw_loc] = res
                    return res
    except Exception:
        pass
        
    return None, None, False, ""

def parse_location(lightning_cfg):
    """
    lightning_protection から (lat, lon) を抽出します (従来互換)。
    """
    lat, lon, _, _ = parse_location_info(lightning_cfg)
    return lat, lon

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

def send_windows_desktop_notification(title: str, message: str):
    """Windows 10 / 11 のデスクトップ画面右下にトースト通知（ポップアップ）を表示します。"""
    if os.name != 'nt':
        return
    try:
        t_xml = html.escape(title if title else "Dual Sleeper")
        # マークダウン装飾などの除去と改行整理
        clean_msg = message.replace("**", "").replace("`", "").replace("📍 ", "").replace("⚡ ", "")
        m_xml = html.escape(clean_msg)
        
        xml_str = f'<toast><visual><binding template="ToastGeneric"><text>{t_xml}</text><text>{m_xml}</text></binding></visual></toast>'
        
        # Windows標準登録済みの PowerShell AppID を指定してサイレント破棄を防止
        app_id = r"{1AC14E77-02E7-4E5D-B744-2EB1AE5198B7}\WindowsPowerShell\v1.0\powershell.exe"
        
        ps_script = (
            '[Windows.UI.Notifications.ToastNotificationManager, Windows.UI.Notifications, ContentType = WindowsRuntime] | Out-Null\n'
            '[Windows.Data.Xml.Dom.XmlDocument, Windows.Data.Xml.Dom.XmlDocument, ContentType = WindowsRuntime] | Out-Null\n'
            '$x = New-Object Windows.Data.Xml.Dom.XmlDocument\n'
            f"$x.LoadXml('{xml_str}')\n"
            '$t = [Windows.UI.Notifications.ToastNotification]::new($x)\n'
            f'[Windows.UI.Notifications.ToastNotificationManager]::CreateToastNotifier("{app_id}").Show($t)\n'
        )
        encoded_ps = base64.b64encode(ps_script.encode('utf-16le')).decode('utf-8')
        
        subprocess.run(
            ['powershell', '-NoProfile', '-ExecutionPolicy', 'Bypass', '-EncodedCommand', encoded_ps],
            creationflags=subprocess.CREATE_NO_WINDOW if os.name == 'nt' else 0,
            timeout=5
        )
    except Exception as e:
        print(f"\n{get_timestamp()} [警告] Windowsデスクトップ通知の送信に失敗しました: {e}")

def send_notifications(config, message, is_weather_alert=False, weather_title=None):
    """設定されているすべての通知サービス（Discord, Telegram, Windowsデスクトップ通知）にメッセージを送信します。"""
    # Discord
    webhook_url = config.get("discord_webhook_url", "")
    if webhook_url:
        send_discord_notification(webhook_url, message)
        
    # Telegram
    bot_token = config.get("telegram_bot_token", "")
    chat_id = config.get("telegram_chat_id", "")
    if bot_token and chat_id:
        send_telegram_notification(bot_token, chat_id, message)

    # Windows デスクトップトースト通知 (desktop_notification: "weather_only", "all", "off")
    desktop_setting = config.get("desktop_notification", "weather_only")
    if desktop_setting == "all" or (desktop_setting == "weather_only" and is_weather_alert):
        title = weather_title if weather_title else ("⚡️ 気象・落雷アラート" if is_weather_alert else "💤 Dual Sleeper 通知")
        send_windows_desktop_notification(title, message)

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

_ADDRESS_CACHE = {}

def get_address_from_coords(lat, lon):
    """
    緯度経度から大まかな日本語住所(都道府県・市区町村)を取得・キャッシュします。
    """
    if lat is None or lon is None:
        return ""
        
    try:
        cache_key = (round(float(lat), 3), round(float(lon), 3))
        if cache_key in _ADDRESS_CACHE:
            return _ADDRESS_CACHE[cache_key]
    except Exception:
        cache_key = None
        
    address_str = ""
    # 1. BigDataCloud API (高速・高精度)
    url = f"https://api.bigdatacloud.net/data/reverse-geocode-client?latitude={lat}&longitude={lon}&localityLanguage=ja"
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "DualSleeper/1.0"})
        with urllib.request.urlopen(req, timeout=5) as response:
            if response.status == 200:
                data = json.loads(response.read().decode("utf-8"))
                sub = data.get("principalSubdivision", "")
                city = data.get("city", "") or data.get("locality", "")
                if sub and city:
                    address_str = f"{sub} {city}"
                elif sub:
                    address_str = sub
                elif city:
                    address_str = city
    except Exception:
        pass
        
    # 2. フォールバック (Nominatim OpenStreetMap)
    if not address_str:
        url_nom = f"https://nominatim.openstreetmap.org/reverse?format=json&lat={lat}&lon={lon}&zoom=10&accept-language=ja"
        try:
            req = urllib.request.Request(url_nom, headers={"User-Agent": "DualSleeper/1.0"})
            with urllib.request.urlopen(req, timeout=5) as response:
                if response.status == 200:
                    data = json.loads(response.read().decode("utf-8"))
                    addr_info = data.get("address", {})
                    sub = addr_info.get("province", "") or addr_info.get("state", "")
                    city = addr_info.get("city", "") or addr_info.get("town", "") or addr_info.get("suburb", "")
                    if sub and city:
                        address_str = f"{sub} {city}"
                    elif sub:
                        address_str = sub
                    elif city:
                        address_str = city
        except Exception:
            pass
            
WMO_WEATHER_MAP = {
    0: "☀️ 快晴", 1: "🌤️ 晴れ", 2: "⛅ 一部曇り", 3: "☁️ 曇り",
    45: "🌫️ 霧", 48: "🌫️ 着氷性の霧",
    51: "🚿 弱い小雨", 53: "🚿 小雨", 55: "🚿 強い小雨",
    56: "❄️ 着氷性の弱い小雨", 57: "❄️ 着氷性の強い小雨",
    61: "☔ 弱い雨", 63: "☔ 雨", 65: "☔ 強い雨 (豪雨)",
    66: "❄️ 着氷性の弱い雨", 67: "❄️ 着氷性の強い雨",
    71: "❄️ 弱い雪", 73: "❄️ 雪", 75: "❄️ 強い雪 (大雪)", 77: "❄️ 霧雪",
    80: "🌧️ にわか雨", 81: "🌧️ 強いにわか雨", 82: "🌧️ 激しいにわか雨",
    85: "🌨️ 弱いにわか雪", 86: "🌨️ 強いにわか雪",
    95: "⚡ 雷雨", 96: "⚡ 雹(ひょう)を伴う雷雨", 99: "⚡ 激しい雷雨"
}

def check_lightning_alert(lat_or_cfg, lon=None, lookahead_hours=3):
    """
    Open-Meteo API を叩いて指定された緯度・経度(または config)の現在の天気、今後の雷予報、および雷解除予想時刻をチェックします。
    戻り値: (is_thunder_now, weather_code_desc, location_info_str, is_thunder_forecast, forecast_desc, clear_time_info)
    """
    is_addr_mode = False
    addr_display = ""
    
    if isinstance(lat_or_cfg, dict):
        lat, lon, is_addr_mode, addr_display = parse_location_info(lat_or_cfg)
    elif isinstance(lat_or_cfg, str):
        lat, lon, is_addr_mode, addr_display = parse_location_info({"location": lat_or_cfg})
    else:
        lat = lat_or_cfg
        # lon は引数の lon をそのまま使用
        
    if lat is None or lon is None:
        return False, "位置情報未設定", "", False, "位置情報未設定", ""
        
    # 自地点 (lat, lon) を中心とする基本 3x3 (9マス) を完全キープ
    # 外輪から【外西16k, 外西南西18k, 深西27k, 外南17k】へ最適配置した全13マス
    lat_offsets = [
        0.0,                                       # 0: 中心(自地点)
        0.05, -0.05,  0.00,  0.00,  0.05,  0.05, -0.05, -0.05, # 1-8: 基本3x3周囲8マス (北, 南, 東, 西, 北東, 北西, 南東, 南西)
        0.00, -0.08,  0.00, -0.15                   # 9-12: 外西(16k), 外西南西(18k), 深西(27k), 外南(17k)
    ]
    lon_offsets = [
        0.0,                                       # 0: 中心(自地点)
        0.00,  0.00,  0.06, -0.06,  0.06, -0.06,  0.06, -0.06, # 1-8: 基本3x3周囲8マス
       -0.18, -0.18, -0.30,  0.00                   # 9-12: 外西, 外西南西, 深西, 外南
    ]
    
    lats_str = ",".join([f"{(lat + d):.5f}" for d in lat_offsets])
    lons_str = ",".join([f"{(lon + d):.5f}" for d in lon_offsets])
    
    url = f"https://api.open-meteo.com/v1/forecast?latitude={lats_str}&longitude={lons_str}&current=weather_code,precipitation&hourly=weather_code,precipitation,cape&forecast_hours=12&timezone=Asia%2FTokyo"
    req = urllib.request.Request(
        url,
        headers={"User-Agent": "DualSleeper/1.0"}
    )
    try:
        with urllib.request.urlopen(req, timeout=10) as response:
            if response.status == 200:
                raw_data = json.loads(response.read().decode("utf-8"))
                data_list = raw_data if isinstance(raw_data, list) else [raw_data]
                
                center_data = data_list[0]
                current = center_data.get("current", {})
                code = current.get("weather_code", -1)
                
                # 時系列インデックスを取得
                hourly_times = center_data.get("hourly", {}).get("time", [])
                current_time_str = current.get("time", "")
                start_idx = 0
                if current_time_str and len(current_time_str) >= 13:
                    match_prefix = current_time_str[:13]
                    for i, t in enumerate(hourly_times):
                        if t.startswith(match_prefix):
                            start_idx = i
                            break
                            
                # 自地点観測グリッドの情報
                res_lat = center_data.get("latitude", lat)
                res_lon = center_data.get("longitude", lon)
                
                dist_km = calculate_distance_km(lat, lon, res_lat, res_lon)
                bearing = calculate_bearing_16(lat, lon, res_lat, res_lon)
                
                if is_addr_mode:
                    display_title = addr_display or "指定地域"
                    if dist_km < 0.1 or not bearing:
                        loc_desc = f"【{display_title}】 (気象観測点: 地区中心付近)"
                    else:
                        loc_desc = f"【{display_title}】 (気象観測点: 地区中心より【{bearing} 約 {dist_km:.1f} km】地点)"
                else:
                    addr_str = get_address_from_coords(lat, lon)
                    addr_prefix = f"【{addr_str}】" if addr_str else ""
                    if dist_km < 0.1 or not bearing:
                        loc_desc = f"{addr_prefix} (気象観測点: 端末直近エリア)".strip()
                    else:
                        loc_desc = f"{addr_prefix} (気象観測点: 端末から【{bearing} 約 {dist_km:.1f} km】地点)".strip()

                # -------------------------------------------------------------
                # ⚡️ 【CAPE (雷エネルギー) ✕ 降水量 ✕ 傾き急上昇の精密計算】
                # -------------------------------------------------------------
                max_now_cape = 0.0
                max_now_prec = 0.0
                
                fc_end_idx = min(start_idx + max(1, lookahead_hours) + 1, len(hourly_times))
                max_fc_cape = 0.0
                max_fc_prec = 0.0
                max_cape_slope = 0.0
                
                for item in data_list:
                    h_capes = item.get("hourly", {}).get("cape", [])
                    h_precs = item.get("hourly", {}).get("precipitation", [])
                    h_wcodes = item.get("hourly", {}).get("weather_code", [])
                    
                    if start_idx < len(h_capes):
                        now_c = h_capes[start_idx] or 0.0
                        now_p = h_precs[start_idx] or 0.0
                        if now_c > max_now_cape: max_now_cape = now_c
                        if now_p > max_now_prec: max_now_prec = now_p
                        
                    fc_capes = h_capes[start_idx : fc_end_idx]
                    fc_precs = h_precs[start_idx : fc_end_idx]
                    
                    for c_val in fc_capes:
                        if c_val and c_val > max_fc_cape:
                            max_fc_cape = c_val
                            
                    for p_val in fc_precs:
                        if p_val and p_val > max_fc_prec:
                            max_fc_prec = p_val
                            
                    if len(fc_capes) >= 2:
                        slope = (fc_capes[-1] or 0.0) - (fc_capes[0] or 0.0)
                        if slope > max_cape_slope:
                            max_cape_slope = slope

                cape_thresh = 2500.0
                if isinstance(lat_or_cfg, dict):
                    try:
                        cape_thresh = float(lat_or_cfg.get("cape_threshold", 2500))
                    except (ValueError, TypeError):
                        cape_thresh = 2500.0

                # 自地点 (中心 center_data) の CAPE と 降水量を個別抽出
                center_h_capes = center_data.get("hourly", {}).get("cape", [])
                center_h_precs = center_data.get("hourly", {}).get("precipitation", [])
                center_now_cape = (center_h_capes[start_idx] or 0.0) if start_idx < len(center_h_capes) else 0.0
                center_now_prec = (center_h_precs[start_idx] or 0.0) if start_idx < len(center_h_precs) else 0.0
                center_now_wcode = center_data.get("current", {}).get("weather_code", -1)

                # 1. 実況雷判定 (is_thunder_now: 自地点ピンポイントのみを参照！)
                is_wmo_thunder_now_center = (center_now_wcode in (95, 96, 99))
                is_thunder_now = (
                    is_wmo_thunder_now_center or 
                    (center_now_cape >= cape_thresh and center_now_prec >= 5.0)
                )

                # 2. 予兆・予報警戒判定 (is_thunder_forecast: 大気が超不安定で、かつ「雨」を伴う場合のみ厳密に発動)
                is_wmo_thunder_fc = any(
                    any(c in (95, 96, 99) for c in item.get("hourly", {}).get("weather_code", [])[start_idx:fc_end_idx])
                    for item in data_list
                )
                
                # 雨系WMOコード (小雨, 雨, 豪雨, にわか雨, 雷雨) ※単なる曇り(2,3)や霧(45,48)は完全除外！
                rain_wmo_codes = {51, 53, 55, 56, 57, 61, 63, 65, 66, 67, 80, 81, 82, 95, 96, 99}
                
                # 予報対象時間内に「雨が降る(降水量 >= 0.1mm/h)」または「雨系の天気」が含まれているか厳密チェック
                has_rain_forecast = (max_fc_prec >= 0.1) or any(
                    any(c in rain_wmo_codes for c in item.get("hourly", {}).get("weather_code", [])[start_idx:fc_end_idx])
                    for item in data_list
                )

                is_thunder_forecast = (
                    is_wmo_thunder_fc or
                    (max_fc_cape >= cape_thresh and has_rain_forecast) or
                    (max_now_cape >= cape_thresh and has_rain_forecast) or
                    (max_fc_cape >= 1000.0 and max_cape_slope >= 800.0 and has_rain_forecast)
                )

                # 3. メッセージ生成
                w_name = WMO_WEATHER_MAP.get(code, f"コード {code}")
                
                if is_thunder_now:
                    weather_desc = f"{w_name} ⚡️(現地で雷雨発生中 DANGER)"
                    forecast_desc = f"⚡️ 現地で雷雨発生中 (降水量 {max_now_prec:.1f}mm / CAPE {max_now_cape:.0f} J/kg)"
                elif is_thunder_forecast:
                    weather_desc = f"{w_name} (大気不安定: CAPE {max_now_cape:.0f} J/kg)"
                    if max_cape_slope >= 800.0:
                        slope_str = f" / 3時間で+{max_cape_slope:.0f}急上昇"
                    else:
                        slope_str = ""
                    forecast_desc = f"⚡️ 今後{lookahead_hours}時間以内に大気が極めて不安定化・落雷警戒 (最大CAPE {max_fc_cape:.0f} J/kg{slope_str})"
                else:
                    weather_desc = w_name
                    forecast_desc = f"🌤️ 周辺エリアすべて雷危険なし (CAPE: {max_fc_cape:.0f} J/kg 平穏)"
                
                # 解除時刻の算出 (自地点 center_data の CAPE が警戒しきい値 cape_thresh を下回る予定時刻)
                clear_time_info = ""
                if is_thunder_now or is_thunder_forecast:
                    last_danger_idx = -1
                    center_h_capes_all = center_data.get("hourly", {}).get("cape", [])
                    scan_end = min(start_idx + 12, len(center_h_capes_all))
                    for i in range(start_idx, scan_end):
                        if (center_h_capes_all[i] or 0) >= cape_thresh:
                            if i > last_danger_idx:
                                last_danger_idx = i
                                    
                    if last_danger_idx != -1:
                        clear_idx = last_danger_idx + 1
                        if clear_idx < len(hourly_times):
                            t_raw = hourly_times[clear_idx]
                            t_str = t_raw.split("T")[1][:5] if "T" in t_raw else t_raw
                            diff_hours = clear_idx - start_idx
                            hours_desc = "まもなく" if diff_hours <= 0 else f"約 {diff_hours} 時間後"
                            clear_time_info = f"`{t_str}` 頃（{hours_desc}）に警戒数値 (`{cape_thresh:.0f} J/kg`) を下回り解除となる見込みです。"
                        else:
                            clear_time_info = f"今後12時間以上、警戒数値 (`{cape_thresh:.0f} J/kg`) を超える大気不安定が継続する見込みです。"

                # 今後12時間の CAPE 予報リストの抽出
                hourly_capes_12h = []
                for i in range(start_idx, min(start_idx + 12, len(hourly_times))):
                    t_raw = hourly_times[i]
                    t_str = t_raw.split("T")[1][:5] if "T" in t_raw else t_raw
                    c_val = center_data.get("hourly", {}).get("cape", [])[i] or 0.0
                    hourly_capes_12h.append((t_str, c_val))

                return is_thunder_now, weather_desc, loc_desc, is_thunder_forecast, forecast_desc, clear_time_info, max_now_cape, max_fc_cape, hourly_capes_12h
    except Exception as e:
        return False, f"取得エラー: {e}", "", False, f"取得エラー: {e}", "", 0.0, 0.0, []
        
    return False, "データなし", "", False, "データなし", "", 0.0, 0.0, []

def get_weather_report(config):
    """
    Open-Meteo API を叩いて、現在の天気、気温、雷エネルギー(CAPE)、落雷実況、今後12時間のCAPE予報リストをフォーマットした Telegram 用 Markdown 文字列を返します。
    """
    lightning_cfg = config.get("lightning_protection", {})
    if not isinstance(lightning_cfg, dict):
        return "❌ 位置情報が未設定です。config.json の lightning_protection を確認してください。"
        
    lat, lon = parse_location(lightning_cfg)
    if lat is None or lon is None:
        return "❌ 位置情報が未設定です。config.json の location を確認してください。"
        
    fc_cfg = lightning_cfg.get("forecast_protection", {})
    fc_hours = fc_cfg.get("lookahead_hours", 3) if isinstance(fc_cfg, dict) else 3
    
    # check_lightning_alert の判定を100%そのまま使用
    res = check_lightning_alert(lightning_cfg, lookahead_hours=fc_hours)
    is_now, weather_desc, loc_desc, is_fc, forecast_desc, clear_info_str = res[0], res[1], res[2], res[3], res[4], res[5]
    now_cape = res[6] if len(res) >= 7 else 0.0
    fc_cape = res[7] if len(res) >= 8 else 0.0
    hourly_capes_12h = res[8] if len(res) >= 9 else []
    
    # 気温・降水量の取得
    url = f"https://api.open-meteo.com/v1/forecast?latitude={lat}&longitude={lon}&current=temperature_2m,precipitation&timezone=Asia%2FTokyo"
    temp_str = "不明"
    prec_str = ""
    weather_str = weather_desc
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "DualSleeper/1.0"})
        with urllib.request.urlopen(req, timeout=10) as response:
            if response.status == 200:
                data = json.loads(response.read().decode("utf-8"))
                current = data.get("current", {})
                temp = current.get("temperature_2m", None)
                temp_str = f"{temp} °C" if temp is not None else "不明"
                prec = current.get("precipitation", 0.0)
                if prec is not None and prec > 0.0:
                    prec_str = f" 🌧️ (降水量: `{prec:.1f} mm/h`)"
    except Exception:
        pass
        
    thunder_status_str = "⚡️ **現地で雷雨発生中！ (DANGER)**" if is_now else "☀️ **雷なし (NORMAL)**"
    
    cape_thresh = float(lightning_cfg.get("cape_threshold", 2500))
    
    # CAPE雷エネルギーの評価テキスト
    if now_cape >= cape_thresh:
        cape_status_str = f"`{now_cape:.0f} J/kg` 🚨 **(極度に不安定: 警戒{cape_thresh:.0f}以上)**"
    elif now_cape >= 1000:
        cape_status_str = f"`{now_cape:.0f} J/kg` 🟡 **(やや不安定: 注意)**"
    else:
        cape_status_str = f"`{now_cape:.0f} J/kg` 🟢 **(平穏・安全)**"
        
    # 今後12時間の CAPE 予報リスト作成
    cape_list_lines = []
    for t_str, c_val in hourly_capes_12h:
        if c_val >= cape_thresh:
            badge = f"🚨 (警戒{cape_thresh:.0f}超)"
        elif c_val >= 1000:
            badge = "🟡 (注意1000超)"
        else:
            badge = "🟢 (平穏)"
        cape_list_lines.append(f"・`{t_str}` ➔ `{c_val:.0f} J/kg` {badge}")
        
    cape_12h_block = "\n".join(cape_list_lines) if cape_list_lines else "・データ取得なし"
    
    pc_name = get_computer_name()
    return (
        f"🌩️ **[{pc_name}] 現在の天気・防災レポート**\n"
        f"📍 **観測地点:** {loc_desc}\n"
        f"🌤️ **天候:** {weather_str}{prec_str}\n"
        f"🌡️ **気温:** `{temp_str}`\n"
        f"⚡ **雷エネルギー (CAPE):** {cape_status_str}\n\n"
        f"📊 **今後12時間の雷エネルギー (CAPE) 予報:**\n"
        f"{cape_12h_block}"
    )

def get_gpu_status(protect_processes, min_vram_mb=500):
    """
    NVIDIA GPUの使用率(%) と、現在GPUを使用している保護対象プロセスの有無を判定します。
    保護対象プロセス(python等)が実際に指定VRAM(初期値: 500MB)以上を消費している場合のみ保護を有効化します。
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
        
        # 2. 現在GPUを使用しているプロセス名およびVRAM使用量(used_memory)を取得
        proc_output = subprocess.check_output(
            ["nvidia-smi", "--query-compute-apps=process_name,used_memory", "--format=csv,noheader,nounits"],
            shell=True,
            stderr=subprocess.DEVNULL
        ).decode("utf-8").strip()
        
        if proc_output:
            for line in proc_output.splitlines():
                line_str = line.strip()
                if not line_str:
                    continue
                parts = [p.strip() for p in line_str.split(",")]
                if len(parts) >= 2:
                    active_p = parts[0]
                    try:
                        used_mem_mb = int(parts[1])
                    except ValueError:
                        used_mem_mb = 0
                    
                    active_filename = os.path.basename(active_p.replace("\\", "/")).lower()
                    a_target = active_filename[:-4] if active_filename.endswith(".exe") else active_filename
                    a_normalized = a_target.replace("-", "_")
                    
                    # 1. 設定リストとの完全一致・表記揺れ一致・相互部分一致
                    for protect_p in protect_processes:
                        p_name_lower = protect_p.lower()
                        p_target = p_name_lower[:-4] if p_name_lower.endswith(".exe") else p_name_lower
                        p_normalized = p_target.replace("-", "_")
                        
                        if a_target == p_target or a_normalized == p_normalized or p_target in a_target or a_target in p_target:
                            # N/A(0MB)で返ってくるWDDM環境でも、AIプロセス名に合致すれば確実にAI認定！
                            if used_mem_mb >= min_vram_mb or used_mem_mb == 0:
                                protect_active = True
                                break
                    
                    # 2. LM Studio / OpenClaw / llama.cpp などのローカルAI計算エンジンのスマート自動認識
                    if not protect_active:
                        ai_kw_list = ["language_server", "lmstudio", "lm-studio", "lms", "llama-server", "llama_server"]
                        if any(kw in a_target for kw in ai_kw_list):
                            if used_mem_mb >= min_vram_mb or used_mem_mb == 0:
                                protect_active = True

                    if protect_active:
                        break
    except Exception:
        # nvidia-smiが実行できない環境では0%とみなし、保護も無効とする
        pass
        
    return gpu_util, protect_active

def check_game_server_port(ports_input):
    """指定された1つまたは複数のポートに外部からのアクティブな接続が存在するか判定します（Tailscale/LAN完全対応）。"""
    if ports_input is None:
        return False, 0, ""

    # 数値、文字列、またはリストをリスト構造に統一
    if isinstance(ports_input, (int, str)):
        raw_list = [ports_input]
    elif isinstance(ports_input, list):
        raw_list = ports_input
    else:
        return False, 0, ""

    target_ports = set()
    for p in raw_list:
        try:
            target_ports.add(int(p))
        except (ValueError, TypeError):
            pass

    if not target_ports:
        return False, 0, ""

    ports_str = ", ".join(str(p) for p in sorted(target_ports))
    active_count = 0

    # 1. psutil によるソケット検査 (TCP/UDP)
    try:
        conns = psutil.net_connections(kind='all')
        for c in conns:
            if c.laddr and c.laddr.port in target_ports:
                r_ip = None
                if c.raddr and hasattr(c.raddr, 'ip'):
                    r_ip = str(c.raddr.ip)
                elif c.raddr and isinstance(c.raddr, tuple) and len(c.raddr) >= 1:
                    r_ip = str(c.raddr[0])

                if r_ip and r_ip not in ("127.0.0.1", "::1", "0.0.0.0", "::"):
                    active_count += 1
        if active_count > 0:
            return True, active_count, ports_str
    except Exception:
        pass

    # 2. netstat -ano の頑丈なCP932/Shift-JISエンコーディング解析 (Tailscale 100.x.x.x 及び LAN IP対応)
    try:
        output = subprocess.check_output("netstat -ano", shell=True, text=True, encoding='cp932', errors='ignore')
        for line in output.splitlines():
            parts = line.split()
            if len(parts) >= 3:
                local_addr = parts[1]
                foreign_addr = parts[2]
                for p_num in target_ports:
                    # ローカルポート判定 (:2283 や :8211 など)
                    if local_addr.endswith(f":{p_num}") or f":{p_num} " in f"{local_addr} ":
                        # 外部アドレスの除外判定 (127.0.0.1, 0.0.0.0, [::], *:*, 0.0.0.0:0 を除外 ➔ Tailscale 100.x 及び LAN IPを全捕捉)
                        if foreign_addr and not (
                            foreign_addr.startswith("127.0.0.1") or
                            foreign_addr.startswith("0.0.0.0") or
                            foreign_addr.startswith("[::]") or
                            foreign_addr.startswith("*:*") or
                            foreign_addr == "0.0.0.0:0"
                        ):
                            active_count += 1
        if active_count > 0:
            return True, active_count, ports_str
    except Exception:
        pass

    return False, 0, ports_str

class NetworkMonitor:
    def __init__(self):
        self.last_io_by_nic = self._get_filtered_io()
        self.last_time = time.monotonic()
        self.speed_history = [] # 直近の通信速度サンプルの履歴
        self.baseline_speed = 5.0 # 自動計測された平常時バックグラウンド通信量 (KB/s)

    def _get_filtered_io(self):
        """Tailscaleなどの特定アダプターを除外した、全体の送受信バイト数の合計を返します。"""
        try:
            io_dict = psutil.net_io_counters(pernic=True)
            total_sent = 0
            total_recv = 0
            for nic_name, io in io_dict.items():
                if "tailscale" in nic_name.lower():
                    continue
                total_sent += io.bytes_sent
                total_recv += io.bytes_recv
            return {"bytes_sent": total_sent, "bytes_recv": total_recv}
        except Exception:
            try:
                io = psutil.net_io_counters()
                return {"bytes_sent": io.bytes_sent, "bytes_recv": io.bytes_recv}
            except Exception:
                return {"bytes_sent": 0, "bytes_recv": 0}

    def get_speed(self):
        """前回の呼び出しからの平均通信速度（KB/s）を計算し、動的ベースラインと履歴を更新します。"""
        current_io = self._get_filtered_io()
        current_time = time.monotonic()
        elapsed = current_time - self.last_time
        
        if elapsed <= 0:
            return 0.0
        
        sent = current_io["bytes_sent"] - self.last_io_by_nic["bytes_sent"]
        recv = current_io["bytes_recv"] - self.last_io_by_nic["bytes_recv"]
        total_kb = (sent + recv) / 1024.0
        speed = total_kb / elapsed
        
        self.last_io_by_nic = current_io
        self.last_time = current_time

        # 直近7サンプル（約35秒分）の通信速度を保持
        self.speed_history.append(speed)
        if len(self.speed_history) > 7:
            self.speed_history.pop(0)

        # 動的ベースラインの自動学習: 下位サンプル（最小2つ）の平均を「平常時バックグラウンド通信量」とする
        sorted_h = sorted(self.speed_history)
        if sorted_h:
            num_low = max(1, len(sorted_h) // 3)
            low_samples = sorted_h[:num_low]
            self.baseline_speed = sum(low_samples) / float(len(low_samples))
        else:
            self.baseline_speed = speed

        return speed

    def get_median_speed(self):
        """直近サンプルの中央値（Median）を算出。1〜2秒の単発パルス通信を完全消去します。"""
        if not self.speed_history:
            return 0.0
        sorted_h = sorted(self.speed_history)
        return sorted_h[len(sorted_h) // 2]

    def get_baseline_speed(self):
        """自動測定された現在の平常時バックグラウンド通信量（ベースライン速度）を返します。"""
        return self.baseline_speed

    def get_dynamic_threshold(self, margin_kbs=20.0):
        """自動測定された平常時ベースライン + マージン(初期値: 20.0 KB/s)の動的しきい値を返します。マージン自身が最低下限となります。"""
        m = float(margin_kbs)
        return max(m, self.baseline_speed + m)

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
        "power_plan_control": {
            "enabled": False,
            "restore_on_exit": True,
            "power_saver_on_idle_monitor_off": True,
            "ultimate_on_game": False,
            "high_performance_on_ai": False,
            "high_performance_on_cpu": False,
            "cpu_heavy_threshold_percent": 80,
            "cpu_heavy_duration_seconds": 5,
            "auto_background_saver_seconds": 60
        },
        "gpu_protect_processes": [
            "python.exe", "python",
            "llama-server.exe", "llama-server",
            "language_server.exe", "language_server",
            "lmstudio.exe", "lm-studio.exe", "lms.exe",
            "vmmemwsl", "wsl.exe", "wslhost.exe"
        ],
        "gpu_protect_min_vram_mb": 4000,
        "gpu_limit_percent": 40,
        "game_gpu_threshold_percent": 30,
        "network_limit_kbs": 30.0,
        "high_network_limit_kbs": 625.0,
        "dynamic_network_margin_kbs": 30.0,
        "keep_awake_window_titles": ["youtube:20", "twitch", "zoom:60", "obs:360"],
        "server_mode": "off",
        "server_mode_standby_delay_seconds": 600,
        "game_server_protection": {
            "enabled": False,
            "port": 8211
        },
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
            },
            "extended_standby_on_alert": {
                "enabled": True,
                "standby_seconds": 1200
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
            # 末尾カンマ (Trailing Comma) や余分な改行コメントを自動除去してパースエラーを物理防止
            config_content = re.sub(r',(\s*[}\]])', r'\1', config_content)
            config = json.loads(config_content)
            
            # 旧キー "network_limit_kbs" が config.json に残っている場合、マージン値として自動読み替え
            if "network_limit_kbs" in config and "dynamic_network_margin_kbs" not in config:
                config["dynamic_network_margin_kbs"] = config["network_limit_kbs"]

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
    global current_state_num, current_idle_sec, current_net_speed, current_net_median_speed, current_net_max_speed, current_net_baseline_speed, current_net_dynamic_limit, current_low_net_sec, current_gpu_util, current_media_force_until, current_status_reason
    global is_sleep_pending, telegram_extend_request, is_lightning_forecast_risk, lightning_alert_active
    
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
                        with state_lock:
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
                        with state_lock:
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
                        
                        # 落雷警戒ステータス
                        if globals().get("lightning_alert_active", False):
                            lightning_status_str = "⚡️ 落雷発生中！ (DANGER)"
                        elif globals().get("is_lightning_forecast_risk", False):
                            lightning_status_str = "⚡️ autoハイバネート待機中 (WARNING)"
                        else:
                            lightning_status_str = "☀️ 通常 (NORMAL)"
                        
                        # 電源プロファイル（プラン）ステータスの動的生成
                        p_cfg_status = config_tmp.get("power_plan_control", {})
                        active_guid_now, active_name_now = get_active_power_scheme()
                        disp_plan_name = active_name_now or original_power_plan_name or "通常プラン"
                        
                        if p_cfg_status.get("enabled", False):
                            if is_power_saver_applied:
                                power_plan_str = f"🍃 {disp_plan_name} (省電力切替中)"
                            elif is_ultimate_plan_applied:
                                power_plan_str = f"🎮 {disp_plan_name} (ゲーム昇格中)"
                            elif is_ai_plan_applied:
                                power_plan_str = f"🤖 {disp_plan_name} (AI昇格中)"
                            else:
                                power_plan_str = f"⚡ {disp_plan_name} (通常運用中)"
                        else:
                            power_plan_str = f"⚡ {disp_plan_name} (自動切替: オフ)"

                        # 残りカウントダウン時間のフォーマット生成
                        rem_sec = 0
                        if current_state_num == 2:
                            # State 2 (消灯中): スリープ/休止までの残り時間
                            st_limit = globals().get("standby_limit", 300)
                            rem_sec = max(0, int(st_limit - current_low_net_sec))
                            rem_label = "スリープまで残り"
                        elif current_state_num == 1:
                            # State 1 (通信監視中): 低通信確認が完了して消灯するまでの残り時間
                            chk_dur = config_tmp.get("network_check_duration_seconds", 30)
                            rem_sec = max(0, int(chk_dur - current_low_net_sec))
                            rem_label = "消灯まで残り"
                        else:
                            # State 0 (通常状態): 無操作判定時間までの残り ＋ 通信監視時間 (30秒) の総残り時間
                            idle_lim = config_tmp.get("idle_limit_seconds", 120)
                            chk_dur = config_tmp.get("network_check_duration_seconds", 30)
                            rem_sec = max(0, int(max(0, idle_lim - current_idle_sec) + chk_dur))
                            rem_label = "消灯まで残り"
                            
                        if rem_sec >= 60:
                            rem_time_str = f"`{rem_sec // 60}分{rem_sec % 60}秒`"
                        else:
                            rem_time_str = f"`{rem_sec}秒`"

                        margin_kbs = float(config_tmp.get("dynamic_network_margin_kbs", config_tmp.get("network_limit_kbs", 30.0)))
                        base_sp = float(globals().get("current_net_baseline_speed", 0.0))
                        calc_limit = max(margin_kbs, base_sp + margin_kbs)
                        dyn_limit_str = f"{calc_limit:.1f} KB/s (ベース {base_sp:.1f} + マージン {margin_kbs:.1f} KB/s)"

                        gs_cfg = config_tmp.get("game_server_protection", {})
                        gs_enabled = isinstance(gs_cfg, dict) and gs_cfg.get("enabled", False)
                        gs_port_input = gs_cfg.get("ports", gs_cfg.get("port", 8211)) if isinstance(gs_cfg, dict) else 8211
                        if gs_enabled:
                            has_player, p_count, p_str = check_game_server_port(gs_port_input)
                            if has_player:
                                game_srv_str = f"🎮 接続あり ({p_count}名 / ポート: {p_str})"
                            else:
                                game_srv_str = f"💤 接続なし (ポート: {p_str})"
                        else:
                            game_srv_str = "オフ"

                        reply_text = (
                            f"📊 **[{pc_name}] 現在のステータス**\n"
                            f"·状態: {state_str}\n"
                            f"·判定: `{current_status_reason}`\n"
                            f"·電源プラン: `{power_plan_str}`\n"
                            f"·無操作時間: {current_idle_sec:.1f} 秒\n"
                            f"·通信速度: {net_str}\n"
                            f"·動的通信上限: `{dyn_limit_str}`\n"
                            f"·{rem_label}: {rem_time_str}\n"
                            f"·GPU使用率: {current_gpu_util} %\n"
                            f"·ゲームサーバ保護: `{game_srv_str}`\n"
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
    global force_power_mode, standby_limit
    global original_power_plan_guid, original_power_plan_name, is_power_saver_applied, is_ultimate_plan_applied, is_ai_plan_applied, is_cpu_plan_applied
    global current_state_num, current_idle_sec, current_net_speed, current_net_median_speed, current_net_max_speed, current_net_baseline_speed, current_net_dynamic_limit, current_low_net_sec, current_gpu_util, current_media_force_until, current_status_reason
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
    print(f"  ・動的通信マージン    : {config.get('dynamic_network_margin_kbs', 20.0)} KB/s (平常時+マージン全自動適応)")
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
    
    gs_cfg = config.get("game_server_protection", {})
    gs_enabled = isinstance(gs_cfg, dict) and gs_cfg.get("enabled", False)
    gs_port_input = gs_cfg.get("ports", gs_cfg.get("port", 8211)) if isinstance(gs_cfg, dict) else 8211
    _, _, gs_ports_str = check_game_server_port(gs_port_input)
    gs_desc = f"有効 (対象ポート: {gs_ports_str} | 接続中はスリープ絶対無効)" if gs_enabled else "無効 (初期無効)"
    print(f"  ・ゲームサーバ保護    : {gs_desc}")
    
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
    
    # 電源プロファイル制御の有効化時、現在アクティブな元プランを保存
    p_cfg = config.get("power_plan_control", {})
    if p_cfg.get("enabled", False):
        original_power_plan_guid, original_power_plan_name = get_active_power_scheme()
        if original_power_plan_guid:
            name_str = f" ({original_power_plan_name})" if original_power_plan_name else ""
            print(f"{get_timestamp()} [電源プロファイル制御] 元の電源プラン{name_str} を安全保存しました。")
    
    # 状態定義:
    # 0: 通常状態（無操作時間を見守る）
    # 1: 通信監視状態（無操作状態になり、ネットワークの低通信が継続するのを待つ）
    # 2: 消灯状態（モニターがオフ。操作があるのを待つ）
    state = 0 
    last_state = -1 # 状態遷移検知用
    
    low_net_start_time = None
    low_net_standby_start_time = None
    high_net_continue_start_time = None
    background_net_continue_start_time = None
    game_gpu_idle_start_time = None
    ai_gpu_idle_start_time = None
    cpu_heavy_start_time = None
    monitor_off_input_time = None
    last_wakeup_time = time.monotonic()
    last_controller_input_time = 0.0
    last_game_server_active_time = 0.0
    
    # 大容量ダウンロード / 高通信完了通知用変数
    high_net_session_start_time = None
    high_net_session_max_speed = 0.0
    high_net_session_drop_start_time = None
    
    standby_limit = config.get("standby_after_monitor_off_seconds", 300)
    
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
    last_detected_media_key = ""
    media_title_absent_start_time = None # クローズ判定用タイマー
    media_expired_titles = set() # 消化済みタイトル/キーの連続再点灯防止ガード
    media_extensions = (".mp4", ".mkv", ".avi", ".mov", ".wmv", ".flv", ".webm", ".jpg", ".jpeg", ".png", ".gif", ".bmp", ".webp")

    # 一時的な延長時間記憶用
    extended_standby_limit = 0

    # 落雷保護監視用変数
    global lightning_alert_active, is_lightning_forecast_risk
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
                    res_alert = check_lightning_alert(lightning_cfg, lookahead_hours=fc_hours)
                    is_thunder, thunder_msg, loc_desc, is_fc_thunder, fc_msg, clear_time_info = res_alert[0], res_alert[1], res_alert[2], res_alert[3], res_alert[4], res_alert[5]
                    h_capes_12_alert = res_alert[8] if len(res_alert) >= 9 else []
                    current_clear_time_info = clear_time_info
                    
                    if fc_enabled:
                        if is_fc_thunder and not is_lightning_forecast_risk:
                            is_lightning_forecast_risk = True
                            print(f"\n{get_timestamp()} [雷予報検知] ⚡ {fc_msg} が検出されたため、離席スリープの動作を「休止状態（ハイバネート）」へ一時昇格します。({clear_time_info})")
                            
                            c_thresh_n = float(lightning_cfg.get("cape_threshold", 2500))
                            cape_12h_block_str = ""
                            if h_capes_12_alert:
                                c_lines = []
                                for t_str_n, c_val_n in h_capes_12_alert:
                                    badge_n = f"🚨 (警戒{c_thresh_n:.0f}超)" if c_val_n >= c_thresh_n else ("🟡 (注意1000超)" if c_val_n >= 1000 else "🟢 (平穏)")
                                    c_lines.append(f"・`{t_str_n}` ➔ `{c_val_n:.0f} J/kg` {badge_n}")
                                cape_12h_block_str = "\n\n📊 **今後12時間の雷エネルギー (CAPE) 予報:**\n" + "\n".join(c_lines)
                                
                            clear_part = f"\n\n⏰ **警戒解除予想時刻:**\n{clear_time_info}" if clear_time_info else ""
                            
                            send_notifications(
                                config,
                                f"⚡️ **[{pc_name}] 【落雷予兆警戒モード発動】**\n"
                                f"今後{fc_hours}時間以内に大気が極めて不安定化（積乱雲急発達・落雷警戒）する予兆を検出したため、離席放置時のスリープ動作を「休止状態（ハイバネート）」へ事前に自動昇格・警戒セットしました。{clear_part}{cape_12h_block_str}",
                                is_weather_alert=True,
                                weather_title="⚡️ 落雷予兆警戒モード発動 (WARNING)"
                            )
                        elif not is_fc_thunder and is_lightning_forecast_risk:
                            is_lightning_forecast_risk = False
                            print(f"\n{get_timestamp()} [雷予報解除] 大気の不安定度が下がり警戒ラインを通過したため、スリープ動作の昇格を解除しました。")
                            send_notifications(
                                config,
                                f"🌤️ **[{pc_name}] 【落雷予兆警戒モード解除】**\n"
                                f"大気の不安定度（雷エネルギー）が警戒ラインを下回ったため、スリープ動作の自動昇格（ハイバネート化）を解除し、通常運用へ復帰しました。\n\n"
                                f"⚠️ ※CAPE数値はまだ完全には下がり切っていません。引き続き最新の雷情報や天候の急変にご注意ください。",
                                is_weather_alert=True,
                                weather_title="🌤️ 落雷予兆警戒モード解除 (CLEAR)"
                            )
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
                            
                            if should_auto_hibernate:
                                mode_reason = "always (常時自動)" if hib_mode == "always" else "state2_only (消灯/放置中自動)"
                                print(f"\n{get_timestamp()} [落雷自動退避] ⚡ {loc_desc or '端末周辺'}で大気超不安定（落雷警戒）が検出されたため、auto_hibernate設定 ({mode_reason}) に従い「休止状態（ハイバネート）」へ問答無用で移行します！({thunder_msg} | {clear_time_info})")
                                send_notifications(
                                    config,
                                    f"⚡ **[{pc_name}] 【落雷自動退避通知】**\n"
                                    f"登録地点の周辺で大気の状態が極めて不安定（落雷警戒）です！{loc_info}{clear_info}\n\n"
                                    f"⚡ `auto_hibernate: \"{hib_mode}\"` 設定に従い、PCおよびデータを雷サージから保護するため直ちに「休止状態（ハイバネート）」へ自動移行します。",
                                    is_weather_alert=True,
                                    weather_title="⚡️ 落雷警報アラート (大気超不安定 DANGER)"
                                )
                                time.sleep(3.0) # 通知送信完了待ち
                                execute_power_command(use_hibernate=True)
                            else:
                                print(f"\n{get_timestamp()} [落雷警報] ⚡ {loc_desc or '端末周辺'}で大気超不安定（落雷警戒）が検出されました！({thunder_msg} | {clear_time_info})")
                                send_notifications(
                                    config,
                                    f"⚡ **[{pc_name}] 【落雷警報アラート】**\n"
                                    f"登録地点の周辺で大気の状態が極めて不安定（落雷が警戒されます）！{loc_info}{clear_info}\n\n"
                                    f"雷サージからPCおよびデータを保護するため、休止状態（ハイバネート）に移行しますか？\n"
                                    f"「1」または「h」と返信すると、直ちに休止状態（ハイバネート）を予約・実行します。（または /sleep hibernate）",
                                    is_weather_alert=True,
                                    weather_title="⚡️ 大気超不安定・落雷警戒アラート (WARNING)"
                                )
                    else:
                        if lightning_alert_active:
                            lightning_alert_active = False
                            print(f"\n{get_timestamp()} [落雷警報解除] 端末周辺の大気不安定・落雷警報が解除されました。")
                            send_notifications(
                                config,
                                f"🌤️ **[{pc_name}] 【落雷警報解除通知】**\n"
                                f"端末周辺の大気不安定・落雷警戒が解除されました。通常スリープ運用へ全自動復帰しました。",
                                is_weather_alert=True,
                                weather_title="🌤️ 落雷警報アラート解除 (CLEAR)"
                            )

            # 状態遷移 (State 変更) が発生した時だけ、通信統計データを初期化する
            if state != last_state:
                interval_speeds.clear()
                last_state = state

            # 常にネットワーク速度を更新しておく（正確な差分計測のため）
            speed = net_monitor.get_speed()
            
            # 設定を毎ループ再読み込み（稼働中に設定変更できるようにする）
            config = load_config()

            # グローバル通信ステータスの毎ループリアルタイム同期
            margin_kbs = config.get("dynamic_network_margin_kbs", 20.0)
            current_net_baseline_speed = net_monitor.get_baseline_speed()
            current_net_dynamic_limit = net_monitor.get_dynamic_threshold(margin_kbs)
            
            # ===== 大容量ダウンロード / 高通信完了通知判定 (download_completion_notification) =====
            dl_notify_cfg = config.get("download_completion_notification", {})
            if isinstance(dl_notify_cfg, dict) and dl_notify_cfg.get("enabled", False):
                high_limit = float(dl_notify_cfg.get("threshold_kbs", config.get("high_network_limit_kbs", 625.0)))
                min_dur_sec = float(dl_notify_cfg.get("min_duration_seconds", 600))
                trig_cond = str(dl_notify_cfg.get("trigger_condition", "state2_only")).lower()

                if speed >= high_limit:
                    if high_net_session_start_time is None:
                        high_net_session_start_time = time.monotonic()
                        high_net_session_max_speed = speed
                    else:
                        high_net_session_max_speed = max(high_net_session_max_speed, speed)
                    high_net_session_drop_start_time = None
                else:
                    if high_net_session_start_time is not None:
                        if high_net_session_drop_start_time is None:
                            high_net_session_drop_start_time = time.monotonic()
                        elif time.monotonic() - high_net_session_drop_start_time >= 10.0: # 10秒間の連続収束確認で完了判定
                            session_duration = high_net_session_drop_start_time - high_net_session_start_time
                            if session_duration >= min_dur_sec:
                                should_notify = (trig_cond != "state2_only") or (state == 2)
                                if should_notify:
                                    dur_min = int(session_duration // 60)
                                    dur_sec = int(session_duration % 60)
                                    max_mbps = (high_net_session_max_speed * 8.0) / 1024.0
                                    dl_msg = (
                                        f"📥 **[{pc_name}] 大容量通信 / ダウンロード完了**\n"
                                        f"·継続時間: {dur_min} 分 {dur_sec} 秒\n"
                                        f"·最高速度: {high_net_session_max_speed / 1024.0:.1f} MB/s ({max_mbps:.1f} Mbps)\n"
                                        f"·現在の状態: 高トラフィックが収束したため、放置スリープ監視へ復帰します。"
                                    )
                                    send_discord_notification(config.get("discord_webhook_url"), dl_msg)
                                    send_telegram_notification(config.get("telegram_bot_token"), config.get("telegram_chat_id"), dl_msg)
                                    print(f"\n{get_timestamp()} [通知] 大容量ダウンロード/高トラフィック完了通知を送信しました (継続: {dur_min}分{dur_sec}秒)")
                            high_net_session_start_time = None
                            high_net_session_max_speed = 0.0
                            high_net_session_drop_start_time = None
            
            # 物理的な無操作時間（キーボード・マウス）を取得
            physical_idle = get_idle_duration()
            current_time = time.time()
            physical_active_time = current_time - physical_idle

            # ===== 【機能】アクティブウィンドウのメディアファイルおよび登録タイトル検知 =====
            current_title = get_active_window_title()
            has_media = any(ext in current_title for ext in media_extensions)
            
            # config.json に登録された点灯延長対象タイトルのキーワード判定
            keep_awake_kw = config.get("keep_awake_window_titles", [])
            has_custom_kw = False
            custom_duration = 600.0 # デフォルト10分 (600秒)
            matched_key = ""
            
            for item in keep_awake_kw:
                if not item:
                    continue
                item_str = str(item).strip()
                if ":" in item_str:
                    parts = item_str.split(":", 1)
                    kw = parts[0].strip().lower()
                    try:
                        duration = float(parts[1].strip()) * 60.0
                    except ValueError:
                        duration = 600.0
                else:
                    kw = item_str.lower()
                    duration = 600.0
                
                if kw and kw in current_title:
                    has_custom_kw = True
                    custom_duration = duration
                    matched_key = kw
                    break # 最初に一致したものの設定を適用

            if has_media and not matched_key:
                # メディア拡張子でマッチした場合
                matched_key = "media_extension"

            if has_media or has_custom_kw:
                # ユーザーが一度消化した登録条件（キーワード/タイトル）に含まれておらず、かつ前回の検知から変わった瞬間にのみタイマーを設定
                is_expired = (matched_key in media_expired_titles) or (current_title in media_expired_titles)
                if current_title != last_detected_media_title and not is_expired:
                    last_detected_media_title = current_title
                    last_detected_media_key = matched_key
                    # 指定された延長時間（秒）をセット
                    target_duration = 600.0 if has_media else custom_duration
                    media_force_on_until = time.time() + target_duration
                    current_media_force_until = media_force_on_until
                    key_label = f" (キー: {matched_key})" if matched_key else ""
                    print(f"\n{get_timestamp()} [登録条件検知] 強制点灯対象{key_label}（...{current_title[-40:]}）のオープンを検知しました。{int(target_duration // 60)}分間 ({int(target_duration)}秒) の強制点灯モードに入ります。")
            elif media_force_on_until == 0:
                # 強制点灯モード中でない時のみ前回のタイトル記憶をクリア
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

            # 雷警戒・雷予報モード中の State 2 スリープ遅延個別自動適用 (extended_standby_on_alert)
            ext_alert_cfg = config.get("lightning_protection", {}).get("extended_standby_on_alert", {})
            if isinstance(ext_alert_cfg, dict) and ext_alert_cfg.get("enabled", True):
                if is_lightning_forecast_risk or lightning_alert_active:
                    raw_standby_limit = ext_alert_cfg.get("standby_seconds", 1200)

            # 一時的な延長がセットされている場合は、それを最優先する
            if extended_standby_limit > 0:
                standby_limit = extended_standby_limit
            else:
                standby_limit = raw_standby_limit

            # ===== 【メディア強制点灯モード処理】 =====
            is_media_forced = (time.time() < media_force_on_until and media_force_on_until > 0)
            if is_media_forced:
                # 対象ウィンドウが閉じられた（非アクティブ状態が連続 5 秒継続）かを判定
                if not (has_media or has_custom_kw):
                    if media_title_absent_start_time is None:
                        media_title_absent_start_time = time.time()
                    elif time.time() - media_title_absent_start_time >= 5.0: # 一時的切り替えではない本物のクローズ
                        print(f"\n{get_timestamp()} [状態遷移] 対象ウィンドウが閉じられた（5秒間不在）ため、強制点灯モードを即座にキャンセルして通常監視（State 0）へ復帰します。")
                        media_force_on_until = 0
                        current_media_force_until = 0.0
                        last_detected_media_title = ""
                        last_detected_media_key = ""
                        media_title_absent_start_time = None
                        state = 0
                        last_wakeup_time = time.time()
                        net_monitor.get_speed()
                        continue
                else:
                    media_title_absent_start_time = None # 対象タイトルが存在している間はクローズタイマーをリセット

                # 強制点灯中は監視タイマーの基点を現在にし続ける
                last_wakeup_time = time.time()
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
                if last_detected_media_title:
                    media_expired_titles.add(last_detected_media_title)
                if last_detected_media_key:
                    media_expired_titles.add(last_detected_media_key)
                tag_label = last_detected_media_key if last_detected_media_key else (last_detected_media_title[-30:] if last_detected_media_title else "")
                print(f"\n{get_timestamp()} [ガード記録] 登録条件（{tag_label}）の強制点灯を消化したため、操作復帰まで連続再反応を防止ガードします。")
                media_force_on_until = 0 # タイマーをクリア
                current_media_force_until = 0.0
                last_detected_media_title = ""
                last_detected_media_key = ""
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
            gpu_min_vram_mb = config.get("gpu_protect_min_vram_mb", 500)
            gpu_util, gpu_protect_active = get_gpu_status(gpu_procs, min_vram_mb=gpu_min_vram_mb)
            current_gpu_util = gpu_util
            
            # CPU使用率測定 (psutil)
            try:
                current_cpu_util = psutil.cpu_percent(interval=None)
            except Exception:
                current_cpu_util = 0.0

            # WASAPI オーディオセッション（Discord/LINE等の通話・音声ストリーム）のチェック
            is_audio_active = is_audio_session_active()

            # 判定状態(current_status_reason)の動的算出（Telegram/コンソール共通）
            is_gpu_busy_with_python = (gpu_limit > 0 and gpu_util >= gpu_limit and gpu_protect_active)
            high_net_limit = config.get("high_network_limit_kbs", 625.0)
            normal_net_limit = net_monitor.get_dynamic_threshold(config.get("dynamic_network_margin_kbs", 20.0))

            # ゲームGPU判定の閾値（GPU使用率30%以上を「ゲーム等のGPU使用放置」とみなす）
            game_gpu_threshold = config.get("game_gpu_threshold_percent", 30)

            # ===== 【電源プロファイル昇格制御: 優先判定ピラミッド (CPU高負荷 / AI利用 > ゲーム中)】 =====
            p_cfg = config.get("power_plan_control", {})
            is_cpu_heavy = False
            
            if p_cfg.get("enabled", False):
                # 1. CPU高負荷判定 (アプローチ A)
                cpu_thresh = p_cfg.get("cpu_heavy_threshold_percent", 80)
                cpu_dur_limit = p_cfg.get("cpu_heavy_duration_seconds", 5)
                
                if p_cfg.get("high_performance_on_cpu", False):
                    if current_cpu_util >= cpu_thresh:
                        if cpu_heavy_start_time is None:
                            cpu_heavy_start_time = time.time()
                        elif time.time() - cpu_heavy_start_time >= cpu_dur_limit:
                            is_cpu_heavy = True
                    else:
                        cpu_heavy_start_time = None
                else:
                    cpu_heavy_start_time = None

                is_ai_active = p_cfg.get("high_performance_on_ai", False) and is_gpu_busy_with_python
                is_game_active = (state == 0) and p_cfg.get("ultimate_on_game", False) and (gpu_util >= game_gpu_threshold and not gpu_protect_active)

                # 🥇 第 1 優先: CPU高負荷 (アプローチ A) または AI利用中
                if is_cpu_heavy or is_ai_active:
                    game_gpu_idle_start_time = None
                    if not is_ai_plan_applied and not is_cpu_plan_applied:
                        high_guid = get_power_scheme_by_keyword("高パフォーマンス")
                        if high_guid and set_power_scheme(high_guid):
                            if is_cpu_heavy:
                                is_cpu_plan_applied = True
                                is_ai_plan_applied = False
                                tag = f"CPU高負荷 (CPU: {current_cpu_util:.1f}% {cpu_dur_limit}秒継続)"
                            else:
                                is_ai_plan_applied = True
                                is_cpu_plan_applied = False
                                tag = f"AI利用 (Python GPU: {gpu_util}%)"
                            is_power_saver_applied = False
                            is_ultimate_plan_applied = False
                            print(f"\n{get_timestamp()} [電源プロファイル昇格] {tag} を検知したため、「高パフォーマンス」へ自動切り替えました。")

                # 🥈 第 2 優先: ゲーム中判定 (GPU 30%以上)
                elif is_game_active:
                    ai_gpu_idle_start_time = None
                    cpu_heavy_start_time = None
                    if not is_ultimate_plan_applied:
                        ult_guid = get_power_scheme_by_keyword("究極") or get_power_scheme_by_keyword("高パフォーマンス")
                        if ult_guid and set_power_scheme(ult_guid):
                            is_ultimate_plan_applied = True
                            is_power_saver_applied = False
                            is_ai_plan_applied = False
                            is_cpu_plan_applied = False
                            print(f"\n{get_timestamp()} [電源プロファイル昇格] ゲーム稼働 (GPU: {gpu_util}%) を検知したため、「究極のパフォーマンス」へ自動切り替えました。")

                # 🥉 復帰判定 (ヒステリシス考慮)
                else:
                    if is_cpu_plan_applied:
                        if cpu_heavy_start_time is None:
                            cpu_heavy_start_time = time.time()
                        elif time.time() - cpu_heavy_start_time >= 5.0:
                            restore_original_power_scheme()
                            cpu_heavy_start_time = None
                    elif is_ai_plan_applied:
                        if ai_gpu_idle_start_time is None:
                            ai_gpu_idle_start_time = time.time()
                        elif time.time() - ai_gpu_idle_start_time >= 30.0:
                            restore_original_power_scheme()
                            ai_gpu_idle_start_time = None
                    elif is_ultimate_plan_applied:
                        if game_gpu_idle_start_time is None:
                            game_gpu_idle_start_time = time.time()
                        elif time.time() - game_gpu_idle_start_time >= 5.0:
                            restore_original_power_scheme()
                            game_gpu_idle_start_time = None

            if is_cpu_heavy:
                current_status_reason = f"🏗️ CPU高負荷処理中 (CPU: {current_cpu_util:.1f}%)"
            elif is_gpu_busy_with_python:
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
                
                # ===== 【電源プロファイル昇格制御: 優先判定ピラミッドにより一元管理】 =====
                pass

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
                            media_expired_titles.clear()
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
                        media_expired_titles.clear()
                        low_net_start_time = None
                        print(f"\n{get_timestamp()} [状態遷移] 操作を検知したため、通常監視に戻ります。")
                        extended_standby_limit = 0 # 復帰時は一時延長を解除
                        continue
                
                # ファイルダウンロード中であるかチェック
                is_downloading = is_downloading_active(downloads_dir)
                
                # ===== 【State 1 通信判定: 2段構え設計】 =====
                # 消灯前(State 1)は未登録動画の視聴を暗くさせないため、固定しきい値(network_limit_kbs) ＋ 移動中央値(Median) で通信を捕捉
                state1_net_limit = float(config.get("network_limit_kbs", 30.0))
                margin_kbs = float(config.get("dynamic_network_margin_kbs", 30.0))
                base_sp = net_monitor.get_baseline_speed()
                dynamic_net_limit = net_monitor.get_dynamic_threshold(margin_kbs)
                median_sp = net_monitor.get_median_speed()
                current_net_baseline_speed = base_sp
                current_net_dynamic_limit = dynamic_net_limit
                
                # 移動中央値(Median)が固定しきい値以下、または「ブラウザがファイルダウンロード中」の場合
                if median_sp <= state1_net_limit or is_downloading:
                    if low_net_start_time is None:
                        low_net_start_time = time.time()
                    
                    elapsed_low_net = time.time() - low_net_start_time
                    dl_status = " (ダウンロード検出中)" if is_downloading else ""
                    rem_sec_off = max(0, int(net_check_duration - elapsed_low_net))
                    rem_off_str = f"{rem_sec_off // 60}分{rem_sec_off % 60}秒" if rem_sec_off >= 60 else f"{rem_sec_off}秒"
                    print(f"\r{get_timestamp()} [通信監視中] 🌙 消灯まで残り {rem_off_str} | 中央通信: {median_sp:.1f} KB/s (判定上限: {state1_net_limit:.1f} KB/s){dl_status}  ", end="", flush=True)
                    
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
                    # 通信量がしきい値を超えたら計測タイマーをリセット（動画バッファ通信等による点灯維持）
                    if low_net_start_time is not None:
                        print(f"\n{get_timestamp()} [情報] 通信量上昇（中央値 {median_sp:.1f} > 上限 {state1_net_limit:.1f} KB/s）を検知したため消灯タイマーをリセットし点灯維持します。")
                    low_net_start_time = None
                    print(f"\r{get_timestamp()} [通信監視中] 動画/通信検知中... | 中央通信: {median_sp:.1f} KB/s (判定上限: {state1_net_limit:.1f} KB/s)  ", end="", flush=True)

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
                    media_expired_titles.clear() # 操作復帰によりメディア消化ガードを解除
                    restore_original_power_scheme() # 操作復帰時に元の電源プランへ安全に自動復元
                    last_wakeup_time = time.time() # 復帰した瞬間を基準時として記録
                    net_monitor.get_speed() # 復帰待ちの間の通信量をリセット
                    is_retrying = False # 操作復帰時にリトライフラグをクリア
                    retry_start_time = None
                    has_sent_10min_warning = False
                    force_power_mode = None # 操作復帰時は手動予約をクリアする
                    extended_standby_limit = 0 # 復帰時は一時延長を解除
                    continue

                # 2. スタンバイ判定のためのネットワーク監視、GPU監視、ゲームサーバー保護およびオーディオセッション監視
                if standby_limit > 0:
                    gs_cfg = config.get("game_server_protection", {})
                    gs_enabled = isinstance(gs_cfg, dict) and gs_cfg.get("enabled", False)
                    gs_port_input = gs_cfg.get("ports", gs_cfg.get("port", 8211)) if isinstance(gs_cfg, dict) else 8211
                    has_game_player, p_count, p_ports_str = check_game_server_port(gs_port_input) if gs_enabled else (False, 0, "")

                    if gs_enabled and has_game_player:
                        last_game_server_active_time = time.monotonic()

                    is_gs_latched = gs_enabled and (has_game_player or (time.monotonic() - last_game_server_active_time < 60.0))

                    margin_kbs = config.get("dynamic_network_margin_kbs", 20.0)
                    base_sp = net_monitor.get_baseline_speed()
                    dynamic_net_limit = net_monitor.get_dynamic_threshold(margin_kbs)
                    median_sp = net_monitor.get_median_speed()
                    
                    # 生速度 (speed) または 移動中央値 (median_sp) が動的上限を超えているか判定
                    is_net_busy = (speed > dynamic_net_limit or median_sp > dynamic_net_limit)
                    
                    # 消灯中に「ゲームサーバープレイヤー接続」「持続通信（3秒以上）」または「WASAPI オーディオストリーム（通話等）」を検知した場合
                    if is_gs_latched or is_net_busy or is_audio_active:
                        if is_gs_latched:
                            # ゲームサーバーへのプレイヤー接続中：スリープ絶対無効化・タイマー即時リセット
                            if low_net_standby_start_time is not None:
                                p_disp_count = max(1, p_count)
                                print(f"\n{get_timestamp()} [タイマーリセット] 🎮 ゲームサーバー接続中 (ポート: {p_ports_str} / 接続数 {p_disp_count}) を検知したためスリープを絶対無効化・タイマーリセットしました。")
                            low_net_standby_start_time = time.monotonic()
                            restore_original_power_scheme()
                            high_net_continue_start_time = None
                        elif is_audio_active:
                            # 通話/音声発生時は即時スリープタイマーリセット
                            if low_net_standby_start_time is not None:
                                print(f"\n{get_timestamp()} [タイマーリセット] 🎙️ 通話/音声ストリームを検知したためスリープタイマーをリセットしました。")
                            low_net_standby_start_time = time.monotonic()
                            restore_original_power_scheme()
                            high_net_continue_start_time = None
                        else:
                            # 3秒持続確認タイマー（0.1秒の一瞬のノイズは無視し、3秒間連続通信でしっかり捕捉）
                            if high_net_continue_start_time is None:
                                high_net_continue_start_time = time.monotonic()
                            elif time.monotonic() - high_net_continue_start_time >= 3.0:
                                if low_net_standby_start_time is not None:
                                    print(f"\n{get_timestamp()} [タイマーリセット] 🔄 持続通信 ({speed:.1f} > 上限 {dynamic_net_limit:.1f} KB/s [ベース{base_sp:.1f}+マージン{margin_kbs:.1f}]) を3秒間継続検知したためスリープタイマーをリセットしました。")
                                low_net_standby_start_time = time.monotonic()
                                restore_original_power_scheme()
                    else:
                        high_net_continue_start_time = None

                    # ファイルダウンロード中であるかチェック
                    is_downloading = is_downloading_active(downloads_dir)
                    
                    # スリープ禁止時間帯（モニター消灯のみ）かチェック
                    is_no_sleep = is_no_sleep_time(config.get("no_sleep_start_hour"), config.get("no_sleep_end_hour"))
                    
                    # 【スリープを許可する条件】
                    # ※オーディオセッションアクティブ中(is_audio_active) もスリープを阻害保護する
                    allow_sleep = (not is_gpu_busy_with_python) and (speed < high_net_limit) and (not is_downloading) and (not is_no_sleep) and (not is_audio_active)
                    
                    # ===== 【電源プロファイル制御: 非対称ヒステリシス完全放置消灯 ＆ 自動化タスク省電力固定】 =====
                    p_cfg = config.get("power_plan_control", {})
                    if p_cfg.get("enabled", False) and p_cfg.get("power_saver_on_idle_monitor_off", True):
                        bg_saver_limit = p_cfg.get("auto_background_saver_seconds", 60)
                        
                        # 1. 低通信が5秒以上継続した場合に「省電力」へ切り替え
                        if allow_sleep and speed <= normal_net_limit:
                            low_dur = (time.time() - low_net_standby_start_time) if low_net_standby_start_time else 0.0
                            if low_dur >= 5.0 and not is_power_saver_applied:
                                saver_guid = get_power_scheme_by_keyword("省電力")
                                if saver_guid and set_power_scheme(saver_guid):
                                    is_power_saver_applied = True
                                    background_net_continue_start_time = None
                                    print(f"\n{get_timestamp()} [電源プロファイル切替] 完全放置消灯 (5秒低通信継続) を検知したため、「省電力」プロファイルへ一時切り替えました。")
                        
                        # 2. 消灯放置中に通信が発生しバランスに戻った後、無操作のまま通信が N 秒(初期値60秒)継続した場合
                        # ➔ 「Windows Update/バックグラウンド通信等の自動化タスク」と判定し、再び「省電力」プロファイルへ自動移行！
                        elif bg_saver_limit > 0 and (not is_audio_active) and (not is_gpu_busy_with_python) and speed > normal_net_limit:
                            if background_net_continue_start_time is None:
                                background_net_continue_start_time = time.time()
                            elif time.time() - background_net_continue_start_time >= bg_saver_limit:
                                if not is_power_saver_applied:
                                    saver_guid = get_power_scheme_by_keyword("省電力")
                                    if saver_guid and set_power_scheme(saver_guid):
                                        is_power_saver_applied = True
                                        print(f"\n{get_timestamp()} [電源プロファイル最適化] 自動化タスク (バックグラウンド通信 {int(bg_saver_limit)}秒継続) を検知したため、「省電力」プロファイルへ自動移行しました。")
                        else:
                            background_net_continue_start_time = None

                        if not allow_sleep:
                            restore_original_power_scheme()
                    
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
                        rem_sec_st = max(0, int(standby_limit - elapsed_low_net_standby))
                        rem_st_str = f"{rem_sec_st // 60}分{rem_sec_st % 60}秒" if rem_sec_st >= 60 else f"{rem_sec_st}秒"
                        print(f"\r{get_timestamp()} [消灯中] {state_label} (💤 スリープまで残り {rem_st_str}) | 通信: {median_sp:.1f} KB/s | GPU: {gpu_util}%  ", end="", flush=True)
                        
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
                                    
                                # スリープ決定時点の電源プロファイル（プラン）表記
                                p_cfg_det = config.get("power_plan_control", {})
                                if p_cfg_det.get("enabled", False):
                                    if is_power_saver_applied:
                                        plan_det_str = "🍃 省電力 (一時自動切替中)"
                                    else:
                                        orig_name = original_power_plan_name or "通常プラン"
                                        plan_det_str = f"⚡ {orig_name} (通常運用中)"
                                else:
                                    _, det_act_name = get_active_power_scheme()
                                    plan_det_str = f"⚡ {det_act_name or '通常プラン'} (自動切替: オフ)"

                                # オートハイバネート・雷警戒有効時のみ、今後の CAPE 予報リストをメッセージに付与する
                                cape_12h_part = ""
                                if is_lightning_forecast_risk or globals().get("lightning_alert_active", False):
                                    try:
                                        res_w = check_lightning_alert(lightning_cfg, lookahead_hours=fc_hours)
                                        h_capes_12 = res_w[8] if len(res_w) >= 9 else []
                                        if h_capes_12:
                                            cape_lines = []
                                            c_thresh = float(lightning_cfg.get("cape_threshold", 2500))
                                            for t_str, c_val in h_capes_12:
                                                badge = f"🚨 (警戒{c_thresh:.0f}超)" if c_val >= c_thresh else ("🟡 (注意1000超)" if c_val >= 1000 else "🟢 (平穏)")
                                                cape_lines.append(f"・`{t_str}` ➔ `{c_val:.0f} J/kg` {badge}")
                                            cape_12h_part = "\n\n📊 **今後12時間の雷エネルギー (CAPE) 予報:**\n" + "\n".join(cape_lines)
                                    except Exception:
                                        pass

                                # スリープ決定時点のState 2詳細ステータス文字列を作成
                                margin_kbs = float(config.get("dynamic_network_margin_kbs", config.get("network_limit_kbs", 30.0)))
                                base_sp = net_monitor.get_baseline_speed()
                                dyn_limit = net_monitor.get_dynamic_threshold(margin_kbs)
                                dyn_limit_str = f"{dyn_limit:.1f} KB/s (ベース {base_sp:.1f} + マージン {margin_kbs:.1f} KB/s)"

                                gs_cfg_det = config.get("game_server_protection", {})
                                gs_en_det = isinstance(gs_cfg_det, dict) and gs_cfg_det.get("enabled", False)
                                gs_port_det_in = gs_cfg_det.get("ports", gs_cfg_det.get("port", 8211)) if isinstance(gs_cfg_det, dict) else 8211
                                if gs_en_det:
                                    has_p_det, p_c_det, p_s_det = check_game_server_port(gs_port_det_in)
                                    gs_det_str = f"🎮 接続あり ({p_c_det}名 / ポート: {p_s_det})" if has_p_det else f"💤 接続なし (ポート: {p_s_det})"
                                else:
                                    gs_det_str = "オフ"

                                status_details_msg = (
                                    f"📊 **[決定時のステータス]**\n"
                                    f"·判定: `{current_status_reason}`\n"
                                    f"·電源プラン: `{plan_det_str}`\n"
                                    f"·通信速度: 中央値 {median_sp:.1f} KB/s (最高: {max_sp:.1f} KB/s)\n"
                                    f"·動的通信上限: `{dyn_limit_str}`\n"
                                    f"·ゲームサーバ保護: `{gs_det_str}`\n"
                                    f"·GPU使用率: {gpu_util} %\n"
                                    f"·電源予約: `{force_power_mode.upper() if force_power_mode else 'なし'}`"
                                )

                                weather_clear_msg = cape_12h_part

                                send_notifications(
                                    config,
                                    f"🔔 **[{pc_name}] まもなく {mode_name} に移行します。**\n"
                                    f"({mode_desc})\n\n"
                                    f"{status_details_msg}{weather_clear_msg}\n\n"
                                    f"{pending_sec}秒以内に何か文字、数字を送信すると、移行を一時的に10分間延長（モニター消灯状態維持）します。{wol_msg_part}"
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
                             
                            # スリープに入る直前にユーザーの元の電源プランへ完全復元
                            restore_original_power_scheme()

                            sleep_success = go_to_sleep(hibernate=use_hibernate)
                             
                            # ===== ここからスリープ復帰後の処理 =====
                            # 復帰した直後, ネットワークモニターをリセット
                            time.sleep(2)
                            net_monitor.get_speed()
                             
                            # 復帰時の入力状態とマウス位置を上書き記録
                            monitor_off_input_time = get_last_input_time_raw()
                            last_mouse_x, last_mouse_y = get_mouse_position()
                            
                            # 実際にどのくらいスリープしていたか（経過時間）を計算
                            sleep_duration = time.time() - sleep_call_time
                            
                            if (not sleep_success) or sleep_duration < 15.0:
                                # 15秒未満で戻ってきた ➔ スリープ失敗（保存確認ダイアログ等のブロック）、または即時誤復帰！
                                print(f"\n{get_timestamp()} [警告] スリープの移行に失敗した（またはダイアログ等でブロックされた）ため、30秒後に再試行します。")
                                
                                if not is_retrying:
                                    retry_start_time = time.time()
                                    has_sent_10min_warning = False
                                    
                                is_retrying = True # リトライフラグをON
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
        turn_on_monitor()
    except Exception as e:
        print(f"\n{get_timestamp()} [重大エラー] 予期せぬ例外が発生したため、メインループを保護・再開します: {e}")
        time.sleep(3.0)
        # 念のため元プランの復元とモニター点灯
        restore_original_power_scheme()
        turn_on_monitor()

if __name__ == "__main__":
    main()
