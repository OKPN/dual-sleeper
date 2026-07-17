import ctypes
import os
import time
import glob

# GUIDの定義
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

def check_download_temp_files(download_dir):
    """ダウンロード一時ファイルがあるかチェックします。"""
    crdownload_files = glob.glob(os.path.join(download_dir, "*.crdownload"))
    part_files = glob.glob(os.path.join(download_dir, "*.part"))
    return crdownload_files + part_files

def main():
    os.system("")  # Windows ANSIエスケープシーケンス有効化
    os.system("cls" if os.name == "nt" else "clear")
    
    print("=" * 75)
    print(" 📂 ダウンロードフォルダ取得 ＆ 一時ファイル検出 テスト")
    print("=" * 75)
    
    # 1. フォルダパスの取得テスト
    dl_folder = get_downloads_folder()
    print(f"◎ 自動取得したダウンロードフォルダのパス:\n   -> {dl_folder}\n")
    
    # フォルダの存在確認
    if os.path.exists(dl_folder):
        print("🟢 フォルダの存在確認: OK (正常にアクセス可能です)")
    else:
        print("🔴 フォルダの存在確認: NG (フォルダが存在しないか、アクセス権がありません)")
        return
        
    print("\n--- リアルタイム一時ファイル監視テストを開始します ---")
    print("ブラウザでテスト的に何か大きめのファイルをダウンロードしてみてください。")
    print("（一時的なダウンロードが始まると、検出結果がONになります）")
    print("終了するには Ctrl+C を押してください。\n")
    
    try:
        while True:
            temp_files = check_download_temp_files(dl_folder)
            is_downloading = len(temp_files) > 0
            
            # 画面クリアして再描画（チラつき防止）
            os.system("cls")
            print("=" * 75)
            print(" 📂 ダウンロードフォルダ取得 ＆ 一時ファイル検出 テスト")
            print("=" * 75)
            print(f"◎ 対象ダウンロードフォルダ:\n   -> {dl_folder}\n")
            
            status_char = "🟢 ON (ダウンロード中)" if is_downloading else "🔴 OFF (ダウンロードなし)"
            print(f"【監視状況】 一時ファイル検出: {status_char}  (検出数: {len(temp_files)})")
            
            if is_downloading:
                print(f"\n【検出された一時ファイル名】:")
                for f in temp_files:
                    print(f"  - {os.path.basename(f)}")
            print("=" * 75)
            
            time.sleep(1.0)
    except KeyboardInterrupt:
        print("\n\nテストを終了しました。")

if __name__ == "__main__":
    main()
