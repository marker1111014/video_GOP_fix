import os
import sys
import glob
import shutil
import subprocess

def get_subprocess_flags():
    """Return subprocess flags to prevent opening console windows on Windows."""
    if sys.platform == 'win32':
        return subprocess.CREATE_NO_WINDOW
    return 0

def find_ffmpeg():
    """
    Locate a working ffmpeg executable across portable packages,
    local bin folders, system PATH, and common installation locations.
    Free of hardcoded personal usernames.
    """
    # 1. PyInstaller frozen environment
    if getattr(sys, 'frozen', False):
        exe_dir = os.path.dirname(sys.executable)
        base_dir = getattr(sys, '_MEIPASS', exe_dir)
        candidates = [
            os.path.join(base_dir, 'bin', 'ffmpeg.exe'),
            os.path.join(base_dir, 'ffmpeg.exe'),
            os.path.join(exe_dir, 'bin', 'ffmpeg.exe'),
            os.path.join(exe_dir, '_internal', 'bin', 'ffmpeg.exe'),
            os.path.join(exe_dir, 'ffmpeg.exe')
        ]
        for c in candidates:
            if os.path.exists(c):
                return os.path.abspath(c)

    # 2. Local project bin paths relative to code
    cur_dir = os.path.dirname(os.path.abspath(__file__))
    project_root = os.path.dirname(cur_dir)
    candidates = [
        os.path.join(cur_dir, 'bin', 'ffmpeg.exe'),
        os.path.join(project_root, 'bin', 'ffmpeg.exe'),
        os.path.join(project_root, 'VideoFixer_Portable_Project', 'bin', 'ffmpeg.exe'),
        os.path.join(project_root, '影片卡頓檢測與極速修復大師_可攜版', 'bin', 'ffmpeg.exe'),
        os.path.join(cur_dir, 'ffmpeg.exe'),
        os.path.join(project_root, 'ffmpeg.exe')
    ]
    for c in candidates:
        if os.path.exists(c):
            return os.path.abspath(c)

    # 3. imageio_ffmpeg if available
    try:
        import imageio_ffmpeg
        exe = imageio_ffmpeg.get_ffmpeg_exe()
        if exe and os.path.exists(exe):
            return os.path.abspath(exe)
    except Exception:
        pass

    # 4. System PATH
    system_ffmpeg = shutil.which('ffmpeg')
    if system_ffmpeg and os.path.exists(system_ffmpeg):
        return os.path.abspath(system_ffmpeg)

    # 5. Dynamic Windows paths using environment variables
    env_local = os.environ.get('LOCALAPPDATA', '')
    if env_local:
        winget_pattern = os.path.join(env_local, 'Microsoft', 'WinGet', 'Packages', '*FFmpeg*', '*', 'bin', 'ffmpeg.exe')
        matches = glob.glob(winget_pattern)
        if matches and os.path.exists(matches[0]):
            return os.path.abspath(matches[0])

    env_prog = os.environ.get('ProgramFiles', r'C:\Program Files')
    env_user = os.environ.get('USERPROFILE', '')
    common_paths = [
        os.path.join(env_prog, 'ffmpeg', 'bin', 'ffmpeg.exe'),
        os.path.join(env_prog, 'Jellyfin', 'Server', 'ffmpeg.exe'),
        r'C:\ffmpeg\bin\ffmpeg.exe'
    ]
    if env_user:
        conda_pattern = os.path.join(env_user, '*conda*', 'Lib', 'site-packages', 'imageio_ffmpeg', 'binaries', 'ffmpeg*.exe')
        matches = glob.glob(conda_pattern)
        if matches and os.path.exists(matches[0]):
            return os.path.abspath(matches[0])

    for p in common_paths:
        if os.path.exists(p):
            return os.path.abspath(p)

    return None

def find_ffprobe(ffmpeg_path=None):
    """Locate ffprobe if available (optional companion to ffmpeg)."""
    # 1. Check beside ffmpeg if provided
    if ffmpeg_path:
        probe_beside = os.path.join(os.path.dirname(ffmpeg_path), 'ffprobe.exe' if sys.platform == 'win32' else 'ffprobe')
        if os.path.exists(probe_beside):
            return os.path.abspath(probe_beside)

    # 2. Check system PATH
    system_probe = shutil.which('ffprobe')
    if system_probe and os.path.exists(system_probe):
        return os.path.abspath(system_probe)

    # 3. Dynamic Windows Winget
    env_local = os.environ.get('LOCALAPPDATA', '')
    if env_local:
        matches = glob.glob(os.path.join(env_local, 'Microsoft', 'WinGet', 'Packages', '*FFmpeg*', '*', 'bin', 'ffprobe.exe'))
        if matches and os.path.exists(matches[0]):
            return os.path.abspath(matches[0])

    return None

def check_nvenc(ffmpeg_path):
    """Verify NVIDIA NVENC hardware encoder via actual test encoding."""
    if not ffmpeg_path or not os.path.exists(ffmpeg_path):
        return False
    try:
        flags = get_subprocess_flags()
        cmd = [
            ffmpeg_path,
            '-f', 'lavfi',
            '-i', 'color=c=black:s=256x256:d=0.1',
            '-c:v', 'h264_nvenc',
            '-f', 'null',
            '-'
        ]
        p = subprocess.run(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, creationflags=flags, timeout=5)
        return p.returncode == 0
    except Exception:
        return False
