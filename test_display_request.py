import time
import subprocess
import os
import sys

def get_display_requests():
    """WindowsのDISPLAY要求状態を取得します。"""
    try:
        output = subprocess.check_output("powercfg -requests", shell=True).decode("utf-8", errors="ignore")
        
        # DISPLAY: セクションから SYSTEM: セクションまでの行を抽出
        lines = output.splitlines()
        display_lines = []
        in_display = False
        
        for line in lines:
            if line.startswith("DISPLAY:"):
                in_display = True
                continue
            if line.startswith("SYSTEM:"):
                break
            if in_display:
                display_lines.append(line.strip())
                
        # 有効な要求があるか（"なし" や空行以外の行があるか）
        cleaned_requests = [l for l in display_lines if l and l != "なし" and l != "None" and not l.startswith("[")]
        raw_text = "\n".join(display_lines).strip()
        
        # [PROCESS] などの文字がある、または有効な要求行がある場合にONとする
        is_active = "[PROCESS]" in raw_text or len(cleaned_requests) > 0
        
        return is_active, raw_text
    except Exception as e:
        return False, f"エラーが発生しました: {e}"

def main():
    # ANSIエスケープシーケンスをWindowsで有効化
    os.system("")
    
    os.system("cls" if os.name == "nt" else "clear")
    print("=" * 70)
    print(" 🖥️  Windows DISPLAY要求 (動画再生フラグ) リアルタイム監視チェッカー")
    print("=" * 70)
    print("ブラウザでYouTube動画を「再生」または「一時停止」させて、表示が変わるか確認してください。")
    print("終了するには Ctrl+C を押してください。\n")
    
    try:
        while True:
            is_active, raw_info = get_display_requests()
            
            # 出力位置を固定して更新するために画面をクリア
            os.system("cls")
            print("=" * 70)
            print(" 🖥️  Windows DISPLAY要求 (動画再生フラグ) リアルタイム監視チェッカー")
            print("=" * 70)
            print("ブラウザでYouTube動画を「再生」または「一時停止」させて、表示が変わるか確認してください。")
            print("終了するには Ctrl+C を押してください。\n")
            
            status_char = "🟢 ON (動画再生を検知中)" if is_active else "🔴 OFF (停止中)"
            print(f"【判定結果】 動画再生フラグ: {status_char}")
            print("\n【OSのDISPLAY要求の生データ】:")
            if raw_info and raw_info != "なし":
                print(f"  {raw_info}")
            else:
                print("  なし (画面消灯を妨げるアプリはありません)")
            print("=" * 70)
            
            time.sleep(1.0)
    except KeyboardInterrupt:
        print("\nチェッカーを終了しました。")

if __name__ == "__main__":
    main()
