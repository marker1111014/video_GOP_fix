import os
import re
import struct
import subprocess
from .ffmpeg_utils import get_subprocess_flags

def check_moov_atom(file_path):
    """
    Accurately checks whether the 'moov' atom precedes the 'mdat' atom (FastStart).
    Uses binary box header traversal without loading the file into memory.
    """
    ext = os.path.splitext(file_path)[1].lower()
    if ext == '.ts':
        return '不適用 (TS流)'
    if ext not in ('.mp4', '.mov', '.m4v'):
        return '不適用 (非MP4格式)'

    try:
        file_size = os.path.getsize(file_path)
        if file_size < 16:
            return '異常 (檔案過小)'

        with open(file_path, 'rb') as f:
            moov_pos = None
            mdat_pos = None
            pos = 0

            # Scan top-level boxes
            while pos < file_size:
                f.seek(pos)
                header = f.read(8)
                if len(header) < 8:
                    break

                box_size, box_type = struct.unpack('>I4s', header)

                if box_size == 1:
                    ext_header = f.read(8)
                    if len(ext_header) < 8:
                        break
                    box_size = struct.unpack('>Q', ext_header)[0]
                elif box_size == 0:
                    box_size = file_size - pos

                if box_type == b'moov' and moov_pos is None:
                    moov_pos = pos
                elif box_type == b'mdat' and mdat_pos is None:
                    mdat_pos = pos

                # Once both positions are known, determine relationship immediately
                if moov_pos is not None and mdat_pos is not None:
                    break

                if box_size <= 0:
                    break

                pos += box_size

            if moov_pos is not None and mdat_pos is not None:
                if moov_pos < mdat_pos:
                    return '開頭 (FastStart)'
                else:
                    return '結尾 (無FastStart)'
            elif moov_pos is not None:
                return '正常 (含索引)'
            else:
                return '異常 (無moov索引)'
    except Exception:
        return '未知'

def probe_media_info(ffmpeg_path, file_path):
    """
    Probes video metadata using ffmpeg stderr.
    Robust against attached pictures (covers/thumbnails) and variable frame rates.
    """
    flags = get_subprocess_flags()
    cmd = [ffmpeg_path, '-hide_banner', '-i', file_path]

    try:
        proc = subprocess.run(
            cmd,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.PIPE,
            text=True,
            encoding='utf-8',
            errors='ignore',
            timeout=15,
            creationflags=flags
        )
        stderr_text = proc.stderr
    except Exception as e:
        stderr_text = str(e)

    duration_str = '未知'
    duration_sec = 0.0
    bitrate = '未知'
    resolution = '未知'
    fps = 25.0
    fps_str = '未知'
    codec = '未知'

    primary_video_found = False

    for line in stderr_text.splitlines():
        # Parse Duration & Bitrate
        if 'Duration:' in line and duration_str == '未知':
            m_dur = re.search(r'Duration:\s*([0-9]+):([0-9]+):([0-9.]+)', line)
            if m_dur:
                duration_str = f"{m_dur.group(1)}:{m_dur.group(2)}:{m_dur.group(3).split('.')[0]}"
                try:
                    duration_sec = (
                        float(m_dur.group(1)) * 3600 +
                        float(m_dur.group(2)) * 60 +
                        float(m_dur.group(3))
                    )
                except ValueError:
                    pass

            m_br = re.search(r'bitrate:\s*([0-9]+\s*kb/s)', line)
            if m_br:
                bitrate = m_br.group(1)

        # Parse Primary Video Stream (filter out attached picture thumbnails)
        if 'Stream #0:' in line and 'Video:' in line and not primary_video_found:
            if 'attached pic' in line.lower() or 'mjpeg' in line.lower() and resolution != '未知':
                continue

            # Codec
            m_codec = re.search(r'Video:\s*([a-zA-Z0-9_-]+)', line)
            if m_codec:
                codec = m_codec.group(1)

            # Resolution (e.g. 1920x1080)
            m_res = re.search(r'([0-9]{3,5}x[0-9]{3,5})', line)
            if m_res:
                resolution = m_res.group(1)

            # Frame rate (fps / tbr, filter extreme values)
            m_fps = re.search(r'([0-9.]+)\s*fps', line)
            if not m_fps:
                m_fps = re.search(r'([0-9.]+)\s*tbr', line)

            if m_fps:
                try:
                    parsed_fps = float(m_fps.group(1))
                    if 1.0 <= parsed_fps <= 240.0:
                        fps = parsed_fps
                        fps_str = f"{fps:.1f}"
                except ValueError:
                    pass

            if resolution != '未知':
                primary_video_found = True

    return {
        'duration_str': duration_str,
        'duration_sec': duration_sec,
        'bitrate': bitrate,
        'resolution': resolution,
        'fps': fps,
        'fps_str': fps_str if fps_str != '未知' else f"{fps:.1f}",
        'codec': codec
    }

def sample_keyframes_gop(ffmpeg_path, file_path, total_duration_sec):
    """
    Intelligent multi-point sampling to accurately detect GOP length.
    Eliminates false positives/negatives from evaluating only the first 45 seconds.
    """
    flags = get_subprocess_flags()

    # Determine sampling windows (start_sec, sample_duration_sec)
    windows = []
    if total_duration_sec <= 60 or total_duration_sec <= 0:
        sample_dur = min(45, max(10, int(total_duration_sec))) if total_duration_sec > 0 else 45
        windows.append((0, sample_dur))
    elif total_duration_sec <= 300:
        # Medium length: sample early segment (skip intro) and mid segment
        windows.append((5, 25))
        windows.append((int(total_duration_sec * 0.5), 25))
    else:
        # Long video (> 5 minutes): sample 3 segments (early, middle, late)
        windows.append((15, 20))
        windows.append((int(total_duration_sec * 0.5), 25))
        windows.append((int(total_duration_sec * 0.8), 20))

    all_diffs = []
    window_max_intervals = []

    for start_sec, dur_sec in windows:
        cmd = [
            ffmpeg_path,
            '-ss', str(start_sec),
            '-t', str(dur_sec),
            '-i', file_path,
            '-vf', 'select=eq(pict_type\\,I),showinfo',
            '-f', 'null',
            '-'
        ]

        try:
            proc = subprocess.run(
                cmd,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.PIPE,
                text=True,
                encoding='utf-8',
                errors='ignore',
                timeout=25,
                creationflags=flags
            )
            stderr_text = proc.stderr
        except Exception:
            stderr_text = ''

        pts_list = []
        for line in stderr_text.splitlines():
            if 'showinfo' in line and 'pts_time:' in line:
                m = re.search(r'pts_time:([0-9.]+)', line)
                if m:
                    try:
                        pts_list.append(float(m.group(1)))
                    except ValueError:
                        pass

        if len(pts_list) > 1:
            diffs = [pts_list[i] - pts_list[i - 1] for i in range(1, len(pts_list))]
            all_diffs.extend(diffs)
            window_max_intervals.append(max(diffs))
        elif len(pts_list) == 1:
            # Only 1 keyframe found across the entire sample window
            # GOP is at least as long as the window length
            window_max_intervals.append(float(dur_sec))
        else:
            # Zero keyframes found in sample window! Extremely sparse GOP
            window_max_intervals.append(float(dur_sec))

    if all_diffs:
        max_gop = max(max(all_diffs), max(window_max_intervals) if window_max_intervals else 0.0)
        avg_gop = sum(all_diffs) / len(all_diffs)
    elif window_max_intervals:
        max_gop = max(window_max_intervals)
        avg_gop = max_gop
    else:
        max_gop = 0.0
        avg_gop = 0.0

    return max_gop, avg_gop

def evaluate_health_risk(format_ext, max_gop, avg_gop, moov_status):
    """Standardized health and risk evaluation based on GOP and container format."""
    is_ts = format_ext.lower() == '.ts'
    issues = []

    if is_ts:
        issues.append('TS格式無隨機索引')
    if max_gop >= 5.0:
        issues.append(f'關鍵影格過少({max_gop:.1f}s)')
    elif max_gop >= 3.0:
        issues.append(f'GOP偏長({max_gop:.1f}s)')
    if moov_status == '結尾 (無FastStart)':
        issues.append('索引在檔案尾端')

    if is_ts or max_gop >= 5.0:
        level = '[高風險] 嚴重卡頓'
    elif max_gop >= 3.0 or moov_status == '結尾 (無FastStart)':
        level = '[中風險] 偶有延遲'
    else:
        level = '[良好] 順暢秒拖'

    return level, '、'.join(issues) if issues else '無卡頓風險'

def analyze_video(ffmpeg_path, file_path):
    """
    Main analysis entry point.
    Extracts metadata, performs multi-point sampling, and evaluates playback health.
    """
    filename = os.path.basename(file_path)
    file_size_mb = os.path.getsize(file_path) / (1024 * 1024)
    ext = os.path.splitext(filename)[1].lower()

    # 1. FastStart / Moov check
    moov_status = check_moov_atom(file_path)

    # 2. Probe media metadata
    meta = probe_media_info(ffmpeg_path, file_path)

    # 3. Intelligent multi-point keyframe sampling
    max_gop, avg_gop = sample_keyframes_gop(ffmpeg_path, file_path, meta['duration_sec'])

    # 4. Risk evaluation
    level, issues_str = evaluate_health_risk(ext, max_gop, avg_gop, moov_status)

    return {
        'filepath': file_path,
        'filename': filename,
        'format': ext.upper().replace('.', ''),
        'size_mb': round(file_size_mb, 1),
        'duration': meta['duration_str'],
        'duration_sec': meta['duration_sec'],
        'resolution': meta['resolution'],
        'fps': meta['fps_str'],
        'fps_val': meta['fps'],
        'codec': meta['codec'],
        'bitrate': meta['bitrate'],
        'max_gop': round(max_gop, 2),
        'avg_gop': round(avg_gop, 2),
        'moov': moov_status,
        'level': level,
        'issues': issues_str
    }
