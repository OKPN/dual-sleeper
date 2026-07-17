import time
import subprocess
import os

def get_sleep_blocking_requests():
    """WindowsのSYSTEMおよびAWAYMODEセクションから、スリープを妨害している要求を抽出します。"""
    try:
        output = subprocess.check_output("powercfg -requests", shell=True).decode("utf-8", errors="ignore")
        
        lines = output.splitlines()
        system_lines = []
        away_lines = []
        current_section = None
        
        for line in lines:
            line_str = line.strip()
            if line.startswith("DISPLAY:"):
                current_section = "DISPLAY"
                continue
            elif line.startswith("SYSTEM:"):
                current_section = "SYSTEM"
                continue
            elif line.startswith("AWAYMODE:"):
                current_section = "AWAYMODE"
                continue
            elif line.startswith("EXECUTION:"):
                current_section = "EXECUTION"
                continue
            elif line.startswith("PERFBOOST:"):
                current_section = "PERFBOOST"
                continue
            elif line.startswith("ACTIVELOCKSCREEN:"):
                current_section = "ACTIVELOCKSCREEN"
                continue
                
            if current_section == "SYSTEM" and line_str:
                system_lines.append(line_str)
            elif current_section == "AWAYMODE" and line_str:
                away_lines.append(line_str)
                
        # "なし" や 空行、およびセクション見出しのブラケットを除外した、本当の妨害リクエストのリスト
        active_system = [l for l in system_lines if l and l != "なし" and l != "None" and not l.startswith("[")]
        active_away = [l for l in away_lines if l and l != "なし" and l != "None" and not l.startswith("[")]
        
        all_blocks = active_system + active_away
        is_blocked = len(all_blocks) > 0
        
        return is_blocked, active_system, active_away
    except Exception as e:
        return False, [f"エラーが発生しました: {e}"], []

def main():
    # Windows ANSI有効化
    os.system("")
    os.system("cls" if os.name == "nt" else "clear")
    
    print("=" * 75)
    print(" 🛡️  Windows OS スリープ禁止信号 (SYSTEM/AWAYMODE) 検出チェッカー")
    print("=" * 75)
    print("OSのスリープ要求をブロックしているアプリやサービスをリアルタイムで検出します。")
    print("終了するには Ctrl+C を押してください。\n")
    
    try:
        while True:
            is_blocked, system_reqs, away_reqs = get_sleep_blocking_requests()
            
            os.system("cls")
            print("=" * 75)
            print(" 🛡️  Windows OS スリープ禁止信号 (SYSTEM/AWAYMODE) 検出チェッカー")
            print("=" * 75)
            
            if is_blocked:
                print("【総合判定】 スリープ禁止信号: 🔴 ON (スリープ命令はOSに拒否されます)")
            else:
                print("【総合判定】 スリープ禁止信号: 🟢 OFF (いつでもスリープ可能です)")
                
            print("\n【SYSTEM要求 (システム休止の防止)】:")
            if system_reqs:
                for r in system_reqs:
                    print(f"  - {r}")
            else:
                print("  なし")
                
            print("\n【AWAYMODE要求 (アウェイモードの維持)】:")
            if away_reqs:
                for r in away_reqs:
                    print(f"  - {r}")
            else:
                print("  なし")
            print("=" * 75)
            
            time.sleep(1.0)
    except KeyboardInterrupt:
        print("\nチェッカーを終了しました。")

if __name__ == "__main__":
    main()
