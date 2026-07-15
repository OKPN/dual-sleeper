import ctypes
import json
import os
import sys
import time
import datetime
import socket
import urllib.request
import urllib.error
import psutil

# Windows API 定義
class LASTINPUTINFO(ctypes.Structure):
    _fields_ = [("cbSize", ctypes.c_uint), ("dwTime", ctypes.c_uint)]

class POINT(ctypes.Structure):
    _fields_ = [("x", ctypes.c_long), ("y", ctypes.c_long)]

HWND_BROADCAST = 0xFFFF
WM_SYSCOMMAND = 0x0112
SC_MONITORPOWER = 0xF170

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

def turn_off_monitor():
    """モニターの電源をオフにします。"""
    ctypes.windll.user32.SendMessageW(HWND_BROADCAST, WM_SYSCOMMAND, SC_MONITORPOWER, 2)

def turn_on_monitor():
    """モニターの電源をオンにし、マウス入力をシミュレートして復帰を促します。"""
    ctypes.windll.user32.SendMessageW(HWND_BROADCAST, WM_SYSCOMMAND, SC_MONITORPOWER, -1)
    
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
                print("[警告] 休止状態の実行に失敗しました。通常のスタンバイ（スリープ）を実行します。")
                ctypes.windll.powrprof.SetSuspendState(0, 0, 0)
        else:
            ctypes.windll.powrprof.SetSuspendState(0, 0, 0)
    except Exception as e:
        print(f"電源状態の変更に失敗しました: {e}")

def is_hibernate_time(start_hour, end_hour):
    """現在時刻が休止状態（ハイバネート）を適用する時間帯にあるか判定します。"""
    if start_hour is None or end_hour is None:
        return False
    
    now = datetime.datetime.now()
    current_hour = now.hour
    
    if start_hour <= end_hour:
        # 同一日の範囲 (例: 0:00 - 7:00)
        return start_hour <= current_hour < end_hour
    else:
        # 日をまたぐ範囲 (例: 23:00 - 6:00)
        return current_hour >= start_hour or current_hour < end_hour

def get_computer_name():
    """PC名を取得します。"""
    return socket.gethostname()

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
        # タイムアウトを5秒に設定して送信
        with urllib.request.urlopen(req, timeout=5) as response:
            pass
    except Exception as e:
        print(f"\n[警告] Discord通知の送信に失敗しました: {e}")

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

def load_config():
    """設定ファイルを読み込みます。存在しない場合はデフォルト値を返します。"""
    default_config = {
        "idle_limit_seconds": 180,
        "network_limit_kbs": 5.0,
        "network_check_duration_seconds": 60,
        "check_interval_seconds": 5,
        "standby_after_monitor_off_seconds": 300,
        "hibernate_start_hour": 0,
        "hibernate_end_hour": 7,
        "force_monitor_off_idle_seconds": 900,
        "discord_webhook_url": "",
        "sleep_pending_seconds": 10
    }
    config_path = os.path.join(os.path.dirname(__file__), "config.json")
    if os.path.exists(config_path):
        try:
            with open(config_path, "r", encoding="utf-8") as f:
                config = json.load(f)
                # デフォルト値のキーが欠落している場合に補完
                for key, val in default_config.items():
                    if key not in config:
                        config[key] = val
                return config
        except Exception as e:
            print(f"設定ファイルの読み込みに失敗しました。デフォルト値を使用します。エラー: {e}")
    return default_config

def main():
    # Discord Webhook テスト送信のコマンドライン引数判定
    if len(sys.argv) > 1 and sys.argv[1] == "--test-webhook":
        config = load_config()
        url = config.get("discord_webhook_url", "")
        if not url:
            print("[エラー] config.json に discord_webhook_url が設定されていません。")
            sys.exit(1)
        print(f"Discord Webhookのテスト送信を行っています... (URL: {url[:30]}...)")
        pc_name = get_computer_name()
        send_discord_notification(url, f"🔔 **[{pc_name}]** Webhookテスト通知です。このメッセージが見えていれば連携は成功しています！")
        print("テストメッセージを送信しました。Discordのチャンネルを確認してください。")
        sys.exit(0)

    print("=" * 60)
    print(" Dual Sleeper - 段階的電源管理システム")
    print("=" * 60)
    
    config = load_config()
    print("現在の設定:")
    print(f"  ・無操作しきい値      : {config['idle_limit_seconds']} 秒")
    print(f"  ・通信量しきい値      : {config['network_limit_kbs']} KB/s")
    print(f"  ・通信監視時間        : {config['network_check_duration_seconds']} 秒")
    print(f"  ・監視ポーリング間隔  : {config['check_interval_seconds']} 秒")
    
    standby_limit = config.get("standby_after_monitor_off_seconds", 0)
    if standby_limit > 0:
        print(f"  ・システムスリープ遅延: {standby_limit} 秒 (モニター消灯後)")
        start_h = config.get("hibernate_start_hour")
        end_h = config.get("hibernate_end_hour")
        if start_h is not None and end_h is not None:
            print(f"  ・夜間休止状態の時間帯: {start_h}:00 〜 {end_h}:00 (それ以外はスタンバイ)")
    else:
        print("  ・システムスリープ遅延: 無効 (モニター消灯のみ)")
        
    force_off_limit = config.get("force_monitor_off_idle_seconds", 0)
    if force_off_limit > 0:
        print(f"  ・強制モニター消灯    : {force_off_limit} 秒 (無操作継続時、通信の有無を問わず)")
    else:
        print("  ・強制モニター消灯    : 無効")
        
    webhook_url = config.get("discord_webhook_url", "")
    if webhook_url:
        print(f"  ・Discord通知         : 有効 (猶予: {config.get('sleep_pending_seconds', 10)} 秒)")
    else:
        print("  ・Discord通知         : 無効 (Webhook URL未設定)")
        
    print("=" * 60)
    print("監視を開始します。終了するには Ctrl+C を押してください。\n")

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

    try:
        while True:
            # 常にネットワーク速度を更新しておく（正確な差分計測のため）
            speed = net_monitor.get_speed()
            
            # 物理的な無操作時間を取得し、最後のアクティブ時刻を計算
            physical_idle = get_idle_duration()
            current_time = time.time()
            physical_active_time = current_time - physical_idle
            
            # 物理入力の時刻と、モニター復帰時刻のいずれか新しい方を最終アクティブ時刻とする
            effective_active_time = max(physical_active_time, last_wakeup_time)
            idle_sec = current_time - effective_active_time
            
            # 設定を毎ループ再読み込み（稼働中に設定変更できるようにする）
            config = load_config()
            
            # 【共通の割り込み処理】長時間の無操作で強制モニターオフにする判定
            force_off_limit = config.get("force_monitor_off_idle_seconds", 0)
            if state != 2 and force_off_limit > 0 and idle_sec >= force_off_limit:
                print(f"\n[実行] 長時間の無操作 ({idle_sec:.1f} 秒) を検知したため、通信状態を問わずモニターをオフにします。")
                turn_off_monitor()
                time.sleep(1.0) # 消灯時のシステムラグやマウスの微振動をやり過ごす
                state = 2
                monitor_off_input_time = get_last_input_time_raw()
                low_net_standby_start_time = None
                time.sleep(config['check_interval_seconds'])
                continue
            
            if state == 0:
                # 【通常状態】
                print(f"\r[稼働中] 無操作時間: {idle_sec:.1f}/{config['idle_limit_seconds']}秒 | 通信速度: {speed:.1f} KB/s  ", end="", flush=True)
                
                # 操作がない時間がしきい値を超えたら、通信監視状態に遷移
                if idle_sec >= config['idle_limit_seconds']:
                    state = 1
                    low_net_start_time = None
                    print("\n[状態遷移] 無操作時間を超えました。ネットワーク通信量の監視を開始します。")

            elif state == 1:
                # 【通信監視状態】
                # 監視中にユーザーが操作を再開したら通常状態に戻る
                if idle_sec < config['idle_limit_seconds']:
                    state = 0
                    low_net_start_time = None
                    print("\n[状態遷移] 操作を検知したため、通常監視に戻ります。")
                    continue
                
                # 通信速度がしきい値以下か判定
                if speed <= config['network_limit_kbs']:
                    if low_net_start_time is None:
                        low_net_start_time = time.time()
                    
                    elapsed_low_net = time.time() - low_net_start_time
                    print(f"\r[通信監視中] 低通信継続: {elapsed_low_net:.1f}/{config['network_check_duration_seconds']}秒 | 通信速度: {speed:.1f} KB/s  ", end="", flush=True)
                    
                    # 低通信の状態が指定時間続いたらモニター消灯
                    if elapsed_low_net >= config['network_check_duration_seconds']:
                        print("\n[実行] モニターをオフにします。")
                        turn_off_monitor()
                        time.sleep(1.0) # 消灯時のシステムラグやマウスの微振動をやり過ごす
                        state = 2
                        monitor_off_input_time = get_last_input_time_raw()
                        low_net_standby_start_time = None # スタンバイ監視用タイマーを初期化
                else:
                    # 通信量がしきい値を超えたら計測タイマーをリセット
                    if low_net_start_time is not None:
                        print(f"\n[情報] 通信量上昇を検知したためタイマーをリセットします。速度: {speed:.1f} KB/s")
                    low_net_start_time = None
                    print(f"\r[通信監視中] 通信待機中... | 通信速度: {speed:.1f} KB/s  ", end="", flush=True)

            elif state == 2:
                # 【消灯状態】
                # 1. 最後に操作した時間（TickCount）が変わったかをチェックして復帰判定
                current_input_time = get_last_input_time_raw()
                if current_input_time != monitor_off_input_time:
                    print("\n[復帰] 操作を検知しました。モニターをオンにします。")
                    turn_on_monitor()
                    state = 0
                    last_wakeup_time = time.time() # 復帰した瞬間を基準時として記録
                    net_monitor.get_speed() # 復帰待ちの間の通信量をリセット
                    continue

                # 2. スタンバイ判定のためのネットワーク監視
                standby_limit = config.get("standby_after_monitor_off_seconds", 0)
                if standby_limit > 0:
                    if speed <= config['network_limit_kbs']:
                        if low_net_standby_start_time is None:
                            low_net_standby_start_time = time.time()
                        
                        elapsed_low_net_standby = time.time() - low_net_standby_start_time
                        print(f"\r[モニターOFF] スリープ待機: {elapsed_low_net_standby:.1f}/{standby_limit}秒 | 通信速度: {speed:.1f} KB/s  ", end="", flush=True)
                        
                        # スリープ監視時間経過でシステムをサスペンド/ハイバネート
                        if elapsed_low_net_standby >= standby_limit:
                            # スリープか休止状態かの時間判定
                            start_h = config.get("hibernate_start_hour")
                            end_h = config.get("hibernate_end_hour")
                            use_hibernate = is_hibernate_time(start_h, end_h)
                            
                            mode_name = "休止状態 (ハイバネート)" if use_hibernate else "スタンバイ (スリープ)"
                            pc_name = get_computer_name()
                            pending_sec = config.get("sleep_pending_seconds", 10)
                            
                            print(f"\n[スリープ予告] {pending_sec}秒後にシステムを {mode_name} に移行します。")
                            
                            # Discord通知の送信
                            webhook_url = config.get("discord_webhook_url", "")
                            if webhook_url:
                                send_discord_notification(
                                    webhook_url,
                                    f"🔔 **[{pc_name}]** まもなく {mode_name} に移行します。操作を検知した場合は自動でキャンセルされます。(猶予: {pending_sec}秒)"
                                )
                            
                            # 猶予期間中の割り込み（操作検知）の監視
                            canceled = False
                            start_pending_time = time.time()
                            monitor_off_input_time_before = get_last_input_time_raw()
                            
                            while time.time() - start_pending_time < pending_sec:
                                current_input = get_last_input_time_raw()
                                if current_input != monitor_off_input_time_before:
                                    canceled = True
                                    break
                                time.sleep(0.5) # 0.5秒おきに操作チェック
                                
                            if canceled:
                                print("\n[キャンセル] 猶予時間中に操作を検知したため、スリープを中止しました。モニターをONに戻します。")
                                turn_on_monitor()
                                state = 0
                                last_wakeup_time = time.time()
                                net_monitor.get_speed()
                                if webhook_url:
                                    send_discord_notification(
                                        webhook_url,
                                        f"🟢 **[{pc_name}]** 操作を検知したため、スリープ移行をキャンセルしました。通常稼働に戻ります。"
                                    )
                                continue
                            
                            # スリープを実行
                            print(f"[実行] システムを {mode_name} にします。")
                            if webhook_url:
                                send_discord_notification(
                                    webhook_url,
                                    f"💤 **[{pc_name}]** システムを {mode_name} にしました。おやすみなさい。"
                                )
                            
                            state = 0 # 復帰後に通常監視から開始するようにする
                            last_wakeup_time = time.time() # 復帰時に無操作時間がリセットされるようにする
                            low_net_standby_start_time = None
                            
                            go_to_sleep(hibernate=use_hibernate)
                            
                            # 復帰した直後, ネットワークモニターをリセット
                            time.sleep(2)
                            net_monitor.get_speed()
                    else:
                        if low_net_standby_start_time is not None:
                            print(f"\n[情報] 通信量上昇を検知したためスリープタイマーをリセットします。速度: {speed:.1f} KB/s")
                        low_net_standby_start_time = None
                        print(f"\r[モニターOFF] 通信待機中... | 通信速度: {speed:.1f} KB/s  ", end="", flush=True)
                else:
                    # スリープ無効時の静か待機
                    pass

            time.sleep(config['check_interval_seconds'])

    except KeyboardInterrupt:
        print("\n監視プログラムを終了しました。")
        # 終了時に念のためモニターをオンにする命令を送る
        turn_on_monitor()

if __name__ == "__main__":
    main()
