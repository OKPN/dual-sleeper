import time
import subprocess
import os

def get_sleep_blocking_requests():
    """WindowsのSYSTEMおよびAWAYMODEセクションから、スリープを妨害している要求を抽出します。"""
    try:
        # 例外を投げない subprocess.run を使用し、標準エラー出力もキャッチする
        res = subprocess.run(
            "powercfg -requests",
            shell=True,
            capture_output=True,
            text=True,
            errors="ignore"
        )
        
        # エラーコードが返ってきた場合は、その詳細エラーを画面に表示
        if res.returncode != 0:
            error_msg = res.stderr.strip() if res.stderr.strip() else "詳細不明なエラー"
            return False, [f"エラー (コード {res.returncode}): {error_msg}\n  ※「管理者として実行」でバッチを起動すると解決する場合があります。"], []
            
        output = res.stdout
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
                
        # 日本語OS特有の「なし。」や英語の「None」などの無効行を除外して判定
        def is_valid_request(l):
            if not l:
                return False
            # 句点を取り除いて判定
            l_clean = l.replace("。", "").strip().lower()
            if l_clean in ["なし", "none", "なし。"]:
                return False
            if l.startswith("["):
                return False
            return True
            
        active_system = [l for l in system_lines if is_valid_request(l)]
        active_away = [l for l in away_lines if is_valid_request(l)]
        
        all_blocks = active_system + active_away
        is_blocked = len(all_blocks) > 0
        
        return is_blocked, active_system, active_away
    except Exception as e:
        return False, [f"プログラム例外が発生しました: {e}"], []

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
