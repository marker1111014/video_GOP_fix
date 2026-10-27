import os
import sys
import re
import time
import shutil
import subprocess
import argparse

if sys.platform == 'win32':
    try:
        sys.stdout.reconfigure(encoding='utf-8')
        sys.stderr.reconfigure(encoding='utf-8')
    except Exception:
        pass

from core.ffmpeg_utils import find_ffmpeg
from core.media_analyzer import probe_media_info
from core.encoder_engine import detect_hardware_encoders, build_transcode_command

def get_video_info(ffmpeg, file_path):
    meta = probe_media_info(ffmpeg, file_path)
    return meta['fps'], meta['duration_sec']

def fix_single_video(ffmpeg, input_path, output_path, encoder_name='auto'):
    filename = os.path.basename(input_path)
    fps, total_dur = get_video_info(ffmpeg, input_path)
    
    # Target GOP: 1.5 seconds (ensures dense keyframes, instant seek)
    gop_size = max(15, int(fps * 1.5))
    keyint_min = max(10, int(fps * 0.75))
    out_dir = os.path.dirname(output_path)
    if out_dir:
        os.makedirs(out_dir, exist_ok=True)

    cmd, active_enc = build_transcode_command(
        ffmpeg, input_path, output_path,
        encoder_name=encoder_name,
        gop_size=gop_size,
        keyint_min=keyint_min,
        cq=22
    )

    print(f"🎬 開始修復: {filename}")
    print(f"   時長: {int(total_dur//60)}分{int(total_dur%60)}秒 | 偵測幀率: {fps:.1f} fps | 目標關鍵幀間隔: 1.5秒 (GOP={gop_size})")
    print(f"   編碼加速: {active_enc} | 像素格式: yuv420p (高相容)")

    start_time = time.time()
    proc = subprocess.Popen(cmd, stderr=subprocess.PIPE, stdout=subprocess.DEVNULL, text=True, encoding='utf-8', errors='ignore')

    time_pattern = re.compile(r'time=([0-9]+):([0-9]+):([0-9.]+)')
    speed_pattern = re.compile(r'speed=\s*([0-9.]+)x')

    last_update = 0
    while True:
        line = proc.stderr.readline()
        if not line and proc.poll() is not None:
            break

        now = time.time()
        if now - last_update > 0.8:
            tm = time_pattern.search(line)
            sm = speed_pattern.search(line)
            if tm and total_dur > 0:
                cur_sec = float(tm.group(1))*3600 + float(tm.group(2))*60 + float(tm.group(3))
                pct = min(100.0, (cur_sec / total_dur) * 100)
                speed_str = sm.group(1) if sm else '?'
                elapsed = now - start_time
                eta_sec = (total_dur - cur_sec) / (cur_sec / elapsed) if cur_sec > 0 and cur_sec < total_dur else 0
                
                bar_len = 25
                filled = int(bar_len * (pct / 100))
                bar = '█' * filled + '░' * (bar_len - filled)
                print(f"\r   [{bar}] {pct:5.1f}% | 速度: {speed_str}x | 剩餘時間: {int(eta_sec//60)}分{int(eta_sec%60):02d}秒  ", end='', flush=True)
                last_update = now

    proc.wait()
    total_cost = time.time() - start_time
    print()

    # Automatic CPU fallback if GPU encoder failed
    if proc.returncode != 0 and active_enc != 'libx264':
        print(f"   ⚠️ {active_enc} 硬解異常 (代碼 {proc.returncode})，自動無縫切換 CPU 軟解重試...")
        if os.path.exists(output_path):
            try: os.remove(output_path)
            except Exception: pass
        return fix_single_video(ffmpeg, input_path, output_path, encoder_name='libx264')

    if proc.returncode == 0 and os.path.exists(output_path) and os.path.getsize(output_path) > 0:
        new_size_mb = os.path.getsize(output_path) / (1024 * 1024)
        print(f"   ✅ 修復完成！總耗時: {int(total_cost//60)}分{int(total_cost%60):02d}秒 | 檔案大小: {new_size_mb:.1f} MB")
        print(f"   👉 儲存至: {output_path}\n")
        return True
    else:
        print(f"   ❌ 修復失敗！代碼: {proc.returncode}\n")
        return False

def main():
    parser = argparse.ArgumentParser(description='顯卡硬解極速重整關鍵影格 (治本消滅卡頓)')
    parser.add_argument('--file', help='指定單一影片修復')
    parser.add_argument('--outdir', default='修復_零卡頓', help='輸出目錄')
    parser.add_argument('--encoder', default='auto', help='指定編碼器 (auto / h264_nvenc / h264_qsv / h264_amf / libx264)')
    args = parser.parse_args()

    ffmpeg = find_ffmpeg()
    if not ffmpeg:
        print("❌ 錯誤：未找到 ffmpeg 工具！")
        sys.exit(1)

    hw_info = detect_hardware_encoders(ffmpeg)
    active_name = hw_info['encoders'][hw_info['active_encoder']]['name']

    print("=" * 75)
    print("  🚀 影片進度條拖曳「零卡頓」極速修復工具 (1.5秒高密度關鍵影格)")
    print(f"  🎮 編碼加速: {active_name}")
    print("=" * 75)

    work_dir = os.path.abspath('.')
    out_dir = os.path.abspath(args.outdir)

    if args.file:
        targets = [args.file]
    else:
        targets = [
            f for f in sorted(os.listdir(work_dir))
            if f.lower().endswith('.mp4') and os.path.isfile(os.path.join(work_dir, f))
        ]

    print(f"共排程 {len(targets)} 部影片進行修復。\n")

    for idx, f in enumerate(targets, 1):
        in_path = os.path.join(work_dir, f)
        out_path = os.path.join(out_dir, f)
        print(f"[{idx}/{len(targets)}] --------------------------------------------------")
        fix_single_video(ffmpeg, in_path, out_path, encoder_name=args.encoder)

    print("=" * 75)
    print(f"🎉 全部處理完畢！所有修復後的零延遲影片均已存入: {out_dir}")
    print("=" * 75)


if __name__ == '__main__':
    main()
