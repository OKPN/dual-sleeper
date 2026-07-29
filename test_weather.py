import os
import sys
import json
import time

# Windowsコンソールでの文字化け・UnicodeEncodeError防止
if hasattr(sys.stdout, 'reconfigure'):
    try:
        sys.stdout.reconfigure(encoding='utf-8')
    except Exception:
        pass

# dual_sleeper モジュールをインポートできるようにパス追加
sys.path.append(os.path.dirname(os.path.abspath(__file__)))

try:
    from dual_sleeper import load_config, parse_location, check_lightning_alert
except ImportError as e:
    print(f"エラー: dual_sleeper.py の読み込みに失敗しました: {e}")
    sys.exit(1)

def main():
    print("=" * 65)
    print(" Dual Sleeper - 天気情報・落雷API 疎通テストプログラム")
    print("=" * 65)
    
    config = load_config()
    lightning_cfg = config.get("lightning_protection", {})
    
    if not isinstance(lightning_cfg, dict):
        print("[エラー] config.json 内の lightning_protection 設定が不正です。")
        return
        
    enabled = lightning_cfg.get("enabled", False)
    loc_str = lightning_cfg.get("location", "未設定")
    lat, lon = parse_location(lightning_cfg)
    interval = lightning_cfg.get("check_interval_seconds", 300)
    auto_hib = lightning_cfg.get("auto_hibernate", "off")
    
    fc_cfg = lightning_cfg.get("forecast_protection", {})
    fc_enabled = fc_cfg.get("enabled", False) if isinstance(fc_cfg, dict) else False
    fc_hours = fc_cfg.get("lookahead_hours", 3) if isinstance(fc_cfg, dict) else 3
    
    print("[設定状況]")
    print(f"  ・機能有効化 status   : {'有効 (true)' if enabled else '無効 (false)'}")
    print(f"  ・登録位置 (location) : {loc_str}")
    print(f"  ・解析緯度・経度      : 緯度 {lat}, 経度 {lon}")
    print(f"  ・チェック周期        : {interval} 秒")
    print(f"  ・自動休止モード      : {auto_hib}")
    print(f"  ・予報連動昇格モード  : {'有効' if fc_enabled else '無効'} (対象: 直近 {fc_hours} 時間)")
    print("-" * 65)
    
    if lat is None or lon is None:
        print("[エラー] 位置情報が取得できませんでした。config.json の location または latitude/longitude を確認してください。")
        return
        
    print("Open-Meteo API へアクセス中...")
    start_t = time.time()
    is_now, desc_now, loc_desc, is_fc, desc_fc, clear_info = check_lightning_alert(lat, lon, lookahead_hours=fc_hours)
    elapsed = time.time() - start_t
    
    print(f"レスポンス時間: {elapsed:.2f} 秒\n")
    print("[取得・解析結果]")
    print(f"  ・位置解析           : {loc_desc or '端末直近エリア'}")
    print(f"  ・現在の実況天気     : {desc_now}")
    print(f"  ・実況雷検知ステータス : {'【雷発生中！】(DANGER)' if is_now else '【雷なし】(NORMAL)'}")
    print(f"  ・直近 {fc_hours} 時間雷予報   : {desc_fc}")
    print(f"  ・予報雷ステータス   : {'【今後雷の可能性あり】(WARNING)' if is_fc else '【今後も雷予報なし】(CLEAR)'}")
    print(f"  ・雷解除予想時間     : {clear_info if clear_info else 'なし (平穏)'}")
    print("=" * 65)
    print("APIの疎通・データ解析テストが正常に完了しました。\n")

if __name__ == "__main__":
    main()
    if sys.stdin.isatty():
        input("何かキーを押すと終了します...")
