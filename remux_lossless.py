import os
import sys
import shutil
import subprocess
import time

if sys.platform == 'win32':
    try:
        sys.stdout.reconfigure(encoding='utf-8')
        sys.stderr.reconfigure(encoding='utf-8')
    except Exception:
        pass

from core.ffmpeg_utils import find_ffmpeg
from core.encoder_engine import build_remux_command


def main():
    print("=" * 70)
    print("  ⚡ TS 轉 MP4 無損秒轉工具 (Stream Copy + FastStart)")
    print("=" * 70)

    ffmpeg = find_ffmpeg()
    if not ffmpeg:
        print("❌ 錯誤：未找到 ffmpeg 工具！")
        sys.exit(1)

    work_dir = os.path.abspath('.')
    ts_files = [f for f in os.listdir(work_dir) if f.lower().endswith('.ts') and os.path.isfile(os.path.join(work_dir, f))]

    if not ts_files:
        print("ℹ️ 當前目錄下沒有發現 .ts 檔案。")
        return

    print(f"找到 {len(ts_files)} 個 .ts 檔案，準備進行無損秒轉...\n")

    success_count = 0
    total_orig_mb = 0
    total_new_mb = 0

    for i, ts_file in enumerate(ts_files, 1):
        ts_path = os.path.join(work_dir, ts_file)
        mp4_name = os.path.splitext(ts_file)[0] + '.mp4'
        mp4_path = os.path.join(work_dir, mp4_name)

        orig_size_mb = os.path.getsize(ts_path) / (1024 * 1024)
        total_orig_mb += orig_size_mb

        print(f"[{i}/{len(ts_files)}] 正在無損轉裝: {ts_file[:40]}... ({orig_size_mb:.1f} MB)", flush=True)
        start_t = time.time()

        cmd = build_remux_command(ffmpeg, ts_path, mp4_path)


        proc = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, encoding='utf-8', errors='ignore')
        cost = time.time() - start_t

        if proc.returncode == 0 and os.path.exists(mp4_path) and os.path.getsize(mp4_path) > 0:
            new_size_mb = os.path.getsize(mp4_path) / (1024 * 1024)
            total_new_mb += new_size_mb
            success_count += 1
            print(f"      ✅ 完成！耗時 {cost:.1f} 秒 | 輸出: {mp4_name[:35]}... ({new_size_mb:.1f} MB)")
        else:
            print(f"      ❌ 失敗！錯誤訊息: {proc.stderr[-300:]}")

    print("\n" + "=" * 70)
    print(f"  🎉 轉裝完成！成功: {success_count}/{len(ts_files)}")
    print(f"  📦 總原始體積: {total_orig_mb:.1f} MB -> 轉裝後體積: {total_new_mb:.1f} MB (去除TS傳輸冗餘)")
    print("=" * 70)

if __name__ == '__main__':
    main()
