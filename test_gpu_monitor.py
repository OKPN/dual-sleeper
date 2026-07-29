import os
import sys
import time
import subprocess
from datetime import datetime

if hasattr(sys.stdout, 'reconfigure'):
    sys.stdout.reconfigure(encoding='utf-8')

# Dual Sleeper の AI 保護対象キーワード
AI_KEYWORDS = ["python", "llama", "language_server", "lmstudio", "lm-studio", "lms", "vmmemwsl", "wslhost", "wsl"]

LOG_FILE = "gpu_monitor.log"

def get_timestamp():
    return datetime.now().strftime("[%Y-%m-%d %H:%M:%S]")

def log_and_print(msg, f):
    print(msg)
    f.write(msg + "\n")
    f.flush()

def main():
    print("=" * 70)
    print(" 🚀 GPU プロセスリアルタイム監視 & AI/ゲーム判定テストツール")
    print(f"   (ログ自動記録先: {os.path.abspath(LOG_FILE)})")
    print("   ※ 終了するには Ctrl + C を押してください")
    print("=" * 70)

    with open(LOG_FILE, "a", encoding="utf-8") as f:
        log_and_print(f"{get_timestamp()} === GPU監視ログ開始 ===", f)
        
        try:
            while True:
                # 1. GPU全体の利用率とVRAM使用量を取得
                try:
                    util_out = subprocess.check_output(
                        ["nvidia-smi", "--query-gpu=utilization.gpu,memory.used,memory.total", "--format=csv,noheader,nounits"],
                        stderr=subprocess.DEVNULL
                    ).decode("utf-8").strip()
                    parts = [p.strip() for p in util_out.split(",")]
                    gpu_util = int(parts[0]) if len(parts) > 0 else 0
                    mem_used = int(parts[1]) if len(parts) > 1 else 0
                    mem_total = int(parts[2]) if len(parts) > 2 else 0
                except Exception:
                    gpu_util, mem_used, mem_total = 0, 0, 0

                # 2. 現在GPUを使用している計算プロセス一覧を取得
                try:
                    proc_out = subprocess.check_output(
                        ["nvidia-smi", "--query-compute-apps=pid,process_name,used_memory", "--format=csv,noheader,nounits"],
                        stderr=subprocess.DEVNULL
                    ).decode("utf-8").strip()
                except Exception:
                    proc_out = ""

                now_str = get_timestamp()
                header = f"\n{now_str} GPU全体系: 使用率 {gpu_util:>2}% | VRAM: {mem_used:>5} MB / {mem_total:>5} MB"
                log_and_print(header, f)

                if not proc_out:
                    log_and_print("   └─ [アクティブプロセスなし] 現在 GPU を消費している計算プロセスはありません。", f)
                else:
                    lines = [l.strip() for l in proc_out.splitlines() if l.strip()]
                    for idx, line_str in enumerate(lines):
                        items = [item.strip() for item in line_str.split(",")]
                        if len(items) >= 3:
                            pid = items[0]
                            full_path = items[1]
                            vram_mb = items[2]
                            
                            filename = os.path.basename(full_path.replace("\\", "/")).lower()
                            target_name = filename[:-4] if filename.endswith(".exe") else filename
                            
                            # AI認定チェック
                            is_ai = any(kw in target_name for kw in AI_KEYWORDS)
                            tag = "🤖 [AI判定プロセス]" if is_ai else "🎮 [一般/ゲーム判定プロセス]"
                            vram_display = f"{vram_mb:>5} MB" if vram_mb != "[N/A]" else " (N/A)  MB"
                            
                            prefix = "   └─" if idx == len(lines) - 1 else "   ├─"
                            line_log = f"{prefix} PID: {pid:<6} | VRAM: {vram_display} | 判定: {tag} | EXE: {filename} ({full_path})"
                            log_and_print(line_log, f)

                time.sleep(2)
        except KeyboardInterrupt:
            log_and_print(f"\n{get_timestamp()} === GPU監視ログを終了しました ===\n", f)

if __name__ == "__main__":
    main()
