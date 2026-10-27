import os
import subprocess
from .ffmpeg_utils import get_subprocess_flags

# Cached detection results to avoid repeated encoder testing
_ENCODER_CACHE = {}

def test_single_encoder(ffmpeg_path, encoder_name):
    """Test whether a specific video encoder can successfully encode frames on the current hardware."""
    if not ffmpeg_path or not os.path.exists(ffmpeg_path):
        return False
    flags = get_subprocess_flags()
    cmd = [
        ffmpeg_path,
        '-f', 'lavfi',
        '-i', 'color=c=black:s=256x256:d=0.1',
        '-c:v', encoder_name,
        '-f', 'null',
        '-'
    ]
    try:
        p = subprocess.run(
            cmd,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            creationflags=flags,
            timeout=5
        )
        return p.returncode == 0
    except Exception:
        return False

def detect_hardware_encoders(ffmpeg_path, force_refresh=False):
    """
    Detect all available hardware and software video encoders.
    Supports NVIDIA (NVENC), Intel (QSV), AMD (AMF), and CPU (libx264).
    """
    global _ENCODER_CACHE
    if not force_refresh and ffmpeg_path in _ENCODER_CACHE:
        return _ENCODER_CACHE[ffmpeg_path]

    encoders_info = {
        'h264_nvenc': {
            'name': 'NVIDIA NVENC (GeForce / RTX 顯卡加速)',
            'short_name': 'NVIDIA NVENC',
            'vendor': 'NVIDIA',
            'available': False
        },
        'h264_qsv': {
            'name': 'Intel QuickSync (QSV 內顯/獨顯加速)',
            'short_name': 'Intel QSV',
            'vendor': 'Intel',
            'available': False
        },
        'h264_amf': {
            'name': 'AMD AMF (Radeon 顯卡硬解加速)',
            'short_name': 'AMD AMF',
            'vendor': 'AMD',
            'available': False
        },
        'libx264': {
            'name': 'CPU 多核心軟解 (x264 萬用相容)',
            'short_name': 'CPU x264',
            'vendor': 'CPU',
            'available': True  # libx264 is always available
        }
    }

    # Test each hardware encoder
    best_hw = None
    for enc_key in ['h264_nvenc', 'h264_qsv', 'h264_amf']:
        is_ok = test_single_encoder(ffmpeg_path, enc_key)
        encoders_info[enc_key]['available'] = is_ok
        if is_ok and best_hw is None:
            best_hw = enc_key

    result = {
        'best_hardware': best_hw,
        'active_encoder': best_hw if best_hw else 'libx264',
        'has_hardware_accel': best_hw is not None,
        'encoders': encoders_info
    }

    _ENCODER_CACHE[ffmpeg_path] = result
    return result

def get_encoder_args(encoder_name, gop_size, keyint_min, cq=22):
    """
    Returns vendor-optimized FFmpeg video arguments.
    Enforces '-pix_fmt yuv420p' to guarantee universal playback compatibility
    even when source videos are 10-bit HDR or yuv444p.
    """
    if encoder_name == 'h264_nvenc':
        return [
            '-c:v', 'h264_nvenc',
            '-preset', 'p4',
            '-tune', 'hq',
            '-rc', 'vbr',
            '-cq', str(cq),
            '-b:v', '0',
            '-g', str(gop_size),
            '-keyint_min', str(keyint_min),
            '-sc_threshold', '0',
            '-pix_fmt', 'yuv420p'
        ]
    elif encoder_name == 'h264_qsv':
        return [
            '-c:v', 'h264_qsv',
            '-preset', 'medium',
            '-global_quality', str(cq),
            '-g', str(gop_size),
            '-keyint_min', str(keyint_min),
            '-pix_fmt', 'yuv420p'
        ]
    elif encoder_name == 'h264_amf':
        return [
            '-c:v', 'h264_amf',
            '-quality', 'balanced',
            '-rc', 'cqp',
            '-qp_i', str(cq),
            '-qp_p', str(cq),
            '-g', str(gop_size),
            '-keyint_min', str(keyint_min),
            '-pix_fmt', 'yuv420p'
        ]
    else:  # libx264 (CPU fallback)
        return [
            '-c:v', 'libx264',
            '-preset', 'veryfast',
            '-crf', str(cq),
            '-g', str(gop_size),
            '-keyint_min', str(keyint_min),
            '-sc_threshold', '0',
            '-pix_fmt', 'yuv420p'
        ]

def build_transcode_command(
    ffmpeg_path,
    input_path,
    output_path,
    encoder_name='auto',
    gop_size=37,
    keyint_min=18,
    cq=22,
    audio_mode='copy'
):
    """
    Builds a complete, robust FFmpeg transcoding command.
    - Preserves primary video and all audio tracks ('-map 0:v:0 -map 0:a?')
    - Normalizes pixel format to yuv420p
    - Enables FastStart '+faststart' for web and seeking optimization
    """
    if encoder_name == 'auto':
        hw_info = detect_hardware_encoders(ffmpeg_path)
        actual_encoder = hw_info['active_encoder']
    else:
        actual_encoder = encoder_name

    v_args = get_encoder_args(actual_encoder, gop_size, keyint_min, cq)

    if audio_mode == 'aac':
        a_args = ['-c:a', 'aac', '-b:a', '192k']
    else:
        a_args = ['-c:a', 'copy']

    return [
        ffmpeg_path, '-y',
        '-i', input_path,
        *v_args,
        '-map', '0:v:0',
        '-map', '0:a?',
        *a_args,
        '-movflags', '+faststart',
        output_path
    ], actual_encoder

def build_remux_command(ffmpeg_path, input_path, output_path):
    """
    Builds a lossless remuxing command for TS to MP4.
    Preserves all audio streams without dropping secondary tracks.
    """
    return [
        ffmpeg_path, '-y',
        '-i', input_path,
        '-map', '0:v:0',
        '-map', '0:a?',
        '-c', 'copy',
        '-movflags', '+faststart',
        output_path
    ]
