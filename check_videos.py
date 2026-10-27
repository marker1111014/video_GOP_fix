import os
import sys
import re
import json
import shutil
import subprocess
import argparse
from datetime import datetime

# Ensure utf-8 output in Windows terminal
if sys.platform == 'win32':
    try:
        sys.stdout.reconfigure(encoding='utf-8')
        sys.stderr.reconfigure(encoding='utf-8')
    except Exception:
        pass

from core.ffmpeg_utils import find_ffmpeg
from core.media_analyzer import analyze_video, check_moov_atom


def generate_html_report(results, report_path):
    total = len(results)
    high_count = sum(1 for r in results if '高風險' in r['level'])
    med_count = sum(1 for r in results if '中風險' in r['level'])
    low_count = sum(1 for r in results if '良好' in r['level'])

    rows_html = []
    for r in results:
        badge_class = 'badge-danger' if '高風險' in r['level'] else ('badge-warning' if '中風險' in r['level'] else 'badge-success')
        gop_style = 'color: #dc3545; font-weight: bold;' if r['max_gop'] >= 5.0 else ('color: #ffc107;' if r['max_gop'] >= 3.0 else 'color: #28a745;')
        
        rows_html.append(f"""
        <tr>
            <td class="file-name" title="{r['filename']}">{r['filename']}</td>
            <td><span class="badge {badge_class}">{r['level']}</span></td>
            <td>{r['format']}</td>
            <td>{r['resolution']} @ {r['fps']}fps</td>
            <td>{r['duration']}</td>
            <td>{r['bitrate']}</td>
            <td style="{gop_style}">{r['max_gop']} 秒</td>
            <td>{r['moov']}</td>
            <td><span class="issues-text">{r['issues']}</span></td>
        </tr>
        """)

    html = f"""<!DOCTYPE html>
<html lang="zh-TW">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>影片拖曳卡頓與健康度分析報告</title>
    <style>
        :root {{
            --bg-color: #0f172a;
            --card-bg: #1e293b;
            --text-main: #f8fafc;
            --text-sub: #94a3b8;
            --accent: #38bdf8;
            --danger: #ef4444;
            --warning: #f59e0b;
            --success: #10b981;
            --border: #334155;
        }}
        body {{
            font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, "Helvetica Neue", Arial, sans-serif;
            background-color: var(--bg-color);
            color: var(--text-main);
            margin: 0;
            padding: 24px;
        }}
        .container {{
            max-width: 1400px;
            margin: 0 auto;
        }}
        header {{
            margin-bottom: 24px;
            padding-bottom: 16px;
            border-bottom: 1px solid var(--border);
        }}
        h1 {{
            font-size: 24px;
            margin: 0 0 8px 0;
            display: flex;
            align-items: center;
            gap: 12px;
        }}
        .time-tag {{
            font-size: 14px;
            color: var(--text-sub);
        }}
        .stats-grid {{
            display: grid;
            grid-template-columns: repeat(auto-fit, minmax(200px, 1fr));
            gap: 16px;
            margin-bottom: 24px;
        }}
        .stat-card {{
            background: var(--card-bg);
            border: 1px solid var(--border);
            border-radius: 8px;
            padding: 16px;
            text-align: center;
        }}
        .stat-card .num {{
            font-size: 32px;
            font-weight: bold;
            margin: 4px 0;
        }}
        .stat-card .label {{
            font-size: 14px;
            color: var(--text-sub);
        }}
        table {{
            width: 100%;
            border-collapse: collapse;
            background: var(--card-bg);
            border-radius: 8px;
            overflow: hidden;
            border: 1px solid var(--border);
        }}
        th, td {{
            padding: 12px 14px;
            text-align: left;
            border-bottom: 1px solid var(--border);
            font-size: 13.5px;
        }}
        th {{
            background: #243248;
            color: var(--text-sub);
            font-weight: 600;
        }}
        tr:hover {{
            background: #2a3b53;
        }}
        .file-name {{
            font-weight: 500;
            max-width: 320px;
            white-space: nowrap;
            overflow: hidden;
            text-overflow: ellipsis;
        }}
        .badge {{
            display: inline-block;
            padding: 4px 8px;
            border-radius: 4px;
            font-size: 12px;
            font-weight: 600;
        }}
        .badge-danger {{ background: rgba(239, 68, 68, 0.2); color: var(--danger); border: 1px solid var(--danger); }}
        .badge-warning {{ background: rgba(245, 158, 11, 0.2); color: var(--warning); border: 1px solid var(--warning); }}
        .badge-success {{ background: rgba(16, 185, 129, 0.2); color: var(--success); border: 1px solid var(--success); }}
        .guide-box {{
            margin-top: 24px;
            padding: 16px;
            background: #1e293b;
            border-left: 4px solid var(--accent);
            border-radius: 4px;
            font-size: 14px;
            line-height: 1.6;
        }}
    </style>
</head>
<body>
    <div class="container">
        <header>
            <h1>🎬 影片拖曳進度條卡頓檢測報告</h1>
            <div class="time-tag">檢測時間：{datetime.now().strftime('%Y-%m-%d %H:%M:%S')} ｜ 掃描路徑：{os.getcwd()}</div>
        </header>

        <div class="stats-grid">
            <div class="stat-card">
                <div class="label">總檢測影片數</div>
                <div class="num">{total}</div>
            </div>
            <div class="stat-card">
                <div class="label">🔴 高風險 (卡頓嚴重)</div>
                <div class="num" style="color: var(--danger);">{high_count}</div>
            </div>
            <div class="stat-card">
                <div class="label">🟡 中風險 (輕度延遲)</div>
                <div class="num" style="color: var(--warning);">{med_count}</div>
            </div>
            <div class="stat-card">
                <div class="label">🟢 良好順暢</div>
                <div class="num" style="color: var(--success);">{low_count}</div>
            </div>
        </div>

        <table>
            <thead>
                <tr>
                    <th>檔案名稱</th>
                    <th>健康狀態</th>
                    <th>格式</th>
                    <th>解析度 / 幀率</th>
                    <th>時長</th>
                    <th>碼率</th>
                    <th>關鍵影格間隔 (GOP)</th>
                    <th>Moov 索引位置</th>
                    <th>檢測出的問題</th>
                </tr>
            </thead>
            <tbody>
                {''.join(rows_html)}
            </tbody>
        </table>

        <div class="guide-box">
            <strong>💡 解讀與修復指南：</strong><br>
            1. <strong>TS 格式檔案</strong>：因為缺乏隨機索引表，拖動進度條容易黑畫面或卡頓。建議使用 <code>ffmpeg -i input.ts -c copy output.mp4</code> 無損轉為 MP4。<br>
            2. <strong>GOP &gt; 5 秒</strong>：表示關鍵影格過少，播放器跳轉時必須重算數百張畫面才能顯示第一幀。在 PotPlayer 中可勾選 <strong>「如為關鍵影格則移動至關鍵影格 (Fast Seek)」</strong> 來立即消除卡頓。
        </div>
    </div>
</body>
</html>
"""
    with open(report_path, 'w', encoding='utf-8') as f:
        f.write(html)

def main():
    parser = argparse.ArgumentParser(description='影片跳轉卡頓檢測工具')
    parser.add_argument('--dir', default='.', help='要掃描的目錄（預設當前目錄）')
    args = parser.parse_args()

    scan_dir = os.path.abspath(args.dir)
    print("=" * 70)
    print("  🚀 影片拖曳進度條卡頓分析檢測工具")
    print(f"  📁 掃描目錄: {scan_dir}")
    print("=" * 70)

    ffmpeg_path = find_ffmpeg()
    if not ffmpeg_path:
        print("❌ 錯誤：未找到 ffmpeg 工具！請確保系統已安裝 ffmpeg。")
        sys.exit(1)

    print(f"  🔍 使用 FFmpeg: {ffmpeg_path}\n")

    # Find video files, ignore backup folders
    video_exts = ('.mp4', '.ts', '.mkv', '.mov', '.avi', '.flv')
    targets = []
    for item in os.listdir(scan_dir):
        full_path = os.path.join(scan_dir, item)
        if os.path.isfile(full_path) and item.lower().endswith(video_exts):
            targets.append(full_path)

    if not targets:
        print("⚠️ 未在此目錄下找到任何影片檔案！")
        return

    print(f"共找到 {len(targets)} 部影片，開始深入檢測...\n")

    results = []
    for i, target in enumerate(targets, 1):
        filename = os.path.basename(target)
        print(f"[{i}/{len(targets)}] 正在分析: {filename[:40]}...", flush=True)
        info = analyze_video(ffmpeg_path, target)
        results.append(info)
        print(f"      👉 {info['level']} | GOP: {info['max_gop']}s | 原因: {info['issues']}")

    # Print summary table
    print("\n" + "=" * 80)
    print("  📊 檢測結果統計")
    print("=" * 80)
    high_count = sum(1 for r in results if '高風險' in r['level'])
    med_count = sum(1 for r in results if '中風險' in r['level'])
    low_count = sum(1 for r in results if '良好' in r['level'])

    print(f"  總數: {len(results)} | 🔴 嚴重卡頓: {high_count} | 🟡 偶爾卡頓: {med_count} | 🟢 順暢: {low_count}")
    print("=" * 80)

    # Save HTML report
    report_file = os.path.join(scan_dir, "影片卡頓檢測報告.html")
    generate_html_report(results, report_file)
    print(f"\n✅ 視覺化 HTML 報告已生成: {report_file}")
    print("   (正在為您自動於瀏覽器開啟報告...)\n")
    try:
        os.startfile(report_file)
    except Exception:
        pass

if __name__ == '__main__':
    main()
