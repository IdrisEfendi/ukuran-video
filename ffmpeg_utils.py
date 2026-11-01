import subprocess
import json
import re
import os
import glob
import math
import threading
import time
import signal
import uuid
import shutil

TASKS = {}
TASKS_LOCK = threading.Lock()


def get_video_info(filepath):
    cmd = ['ffprobe', '-v', 'quiet', '-print_format', 'json',
           '-show_format', '-show_streams', filepath]
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
        if result.returncode != 0:
            return None
        data = json.loads(result.stdout)
        filesize = os.path.getsize(filepath)
    except (OSError, subprocess.SubprocessError, ValueError, TypeError):
        return None

    def as_int(value, default=0):
        try:
            return int(value)
        except (TypeError, ValueError):
            return default

    def as_float(value, default=0.0):
        try:
            parsed = float(value)
            return parsed if math.isfinite(parsed) else default
        except (TypeError, ValueError):
            return default

    format_data = data.get('format', {})
    if not isinstance(format_data, dict):
        format_data = {}

    info = {
        'filepath': filepath,
        'filename': os.path.basename(filepath),
        'filesize': filesize,
        'format': format_data.get('format_name', 'unknown'),
        'duration': as_float(format_data.get('duration', 0)),
        'bitrate': as_int(format_data.get('bit_rate', 0)),
    }

    video_stream = None
    audio_stream = None

    for stream in data.get('streams', []):
        if not isinstance(stream, dict):
            continue
        if stream.get('codec_type') == 'video':
            video_stream = stream
        elif stream['codec_type'] == 'audio':
            audio_stream = stream

    if video_stream:
        info['video_codec'] = video_stream.get('codec_name', 'unknown')
        info['width'] = as_int(video_stream.get('width', 0))
        info['height'] = as_int(video_stream.get('height', 0))

        avg_frame_rate = video_stream.get('avg_frame_rate', '0/1')
        if '/' in avg_frame_rate:
            num, den = avg_frame_rate.split('/')
            try:
                denominator = as_float(den)
                info['fps'] = round(as_float(num) / denominator, 2) if denominator > 0 else 0
            except (AttributeError, ValueError):
                info['fps'] = 0

        info['video_bitrate'] = as_int(video_stream.get('bit_rate', 0))
    else:
        return None

    if audio_stream:
        info['audio_codec'] = audio_stream.get('codec_name', 'unknown')
        info['audio_bitrate'] = as_int(audio_stream.get('bit_rate', 0))
        info['audio_channels'] = as_int(audio_stream.get('channels', 0))
        info['audio_sample_rate'] = as_int(audio_stream.get('sample_rate', 0))
    else:
        info['audio_codec'] = 'none'
        info['audio_bitrate'] = 0

    return info


def calculate_target_bitrate(target_size_mb, duration_sec, audio_kbps=128):
    if duration_sec <= 0:
        return None
    target_bits = target_size_mb * 8 * 1024 * 1024
    audio_bits = audio_kbps * 1000 * duration_sec
    video_bits = target_bits - audio_bits
    if video_bits <= 0:
        return None
    return int(video_bits / duration_sec)


def estimate_min_recommended(info):
    if not info or info.get('duration', 0) <= 0:
        return None
    duration = info['duration']
    width = info.get('width', 0)
    height = info.get('height', 0)
    fps = info.get('fps', 30)

    pixels = width * height
    ref_pixels = 1280 * 720
    quality_factor = max(0.5, pixels / ref_pixels) if pixels > 0 and ref_pixels > 0 else 1.0
    fps_factor = fps / 30.0 if fps > 0 else 1.0

    min_bitrate_h264 = int(1.5 * 1000 * 1000 * quality_factor * fps_factor)
    min_bitrate_h265 = int(1.0 * 1000 * 1000 * quality_factor * fps_factor)

    audio_kbps = 128
    if info.get('audio_codec') != 'none' and info.get('audio_bitrate', 0) > 0:
        audio_kbps = info['audio_bitrate'] / 1000

    min_size_h264_mb = ((min_bitrate_h264 + audio_kbps * 1000) * duration) / (8 * 1024 * 1024)
    min_size_h265_mb = ((min_bitrate_h265 + audio_kbps * 1000) * duration) / (8 * 1024 * 1024)

    return {
        'h264': {
            'bitrate': min_bitrate_h264,
            'size_mb': math.ceil(min_size_h264_mb)
        },
        'h265': {
            'bitrate': min_bitrate_h265,
            'size_mb': math.ceil(min_size_h265_mb)
        }
    }


class CompressionTask:
    def __init__(self, task_id, input_path, output_path, settings, info):
        self.task_id = task_id
        self.input_path = input_path
        self.output_path = output_path
        self.settings = settings
        self.info = info
        self.progress = 0
        self.status = 'queued'
        self.speed = ''
        self.current_fps = 0
        self.eta = ''
        self.ffmpeg_log = []
        self.error = None
        self.result = None
        self.pass_number = 1
        self.start_time = None
        self.process = None
        self.cancelled = False
        self.status_callback = None


def _notify_status(task, status):
    callback = task.status_callback
    if callback:
        try:
            callback(status)
        except Exception:
            pass


def start_compression(task):
    def run():
        global TASKS
        try:
            _do_compression(task)
        except Exception as e:
            with TASKS_LOCK:
                task.status = 'error'
                task.error = str(e)
            _notify_status(task, 'error')

    thread = threading.Thread(target=run, daemon=True)
    thread.start()


def _do_compression(task):
    settings = task.settings
    info = task.info
    input_path = task.input_path
    output_path = task.output_path

    codec = settings.get('codec', 'h264')
    preset = settings.get('preset', 'medium')
    mode = settings.get('mode', 'smart')
    audio_mode = settings.get('audio_mode', 'keep')
    target_size_mb = settings.get('target_size_mb')
    custom_bitrate = settings.get('custom_bitrate')
    crf_value = settings.get('crf', 23)

    duration = info.get('duration', 0)
    audio_bitrate = info.get('audio_bitrate', 0)

    video_codec = 'libx264' if codec == 'h264' else 'libx265'
    logfile_prefix = os.path.join(os.path.dirname(output_path), f'passlog_{task.task_id}')
    # x265 defaults to ./x265_2pass.log, which collides between concurrent tasks
    # and litters the working directory. Keep the stats file next to the output.
    x265_stats = f'{logfile_prefix}.log'

    audio_opts = []
    if audio_mode == 'remove':
        audio_opts = ['-an']
    elif audio_mode == 'compress':
        audio_opts = ['-c:a', 'aac', '-b:a', '96k', '-af', 'aresample=resampler=soxr']
    else:
        audio_opts = ['-c:a', 'copy']

    video_base = ['-c:v', video_codec, '-preset', preset]
    use_two_pass = False

    if mode == 'crf':
        video_base.extend(['-crf', str(crf_value)])
    elif mode == 'bitrate' and custom_bitrate:
        video_base.extend(['-b:v', str(custom_bitrate)])
        use_two_pass = True
    elif mode == 'smart' and target_size_mb:
        if audio_mode == 'keep' and audio_bitrate > 0:
            audio_kbps = audio_bitrate / 1000
        elif audio_mode == 'compress':
            audio_kbps = 96
        else:
            audio_kbps = 0
        target_bps = calculate_target_bitrate(target_size_mb, duration, audio_kbps)
        if target_bps and target_bps > 0:
            video_base.extend(['-b:v', str(target_bps)])
            use_two_pass = True

    common_opts = ['-movflags', '+faststart', '-threads', '0']

    task.start_time = time.time()
    with TASKS_LOCK:
        task.status = 'processing'

    if use_two_pass:
        task.pass_number = 1
        null_output = '/dev/null' if os.name != 'nt' else 'NUL'

        try:
            pass1_cmd = ['ffmpeg', '-y', '-i', input_path]

            if codec == 'h264':
                pass1_cmd.extend(video_base + ['-pass', '1', '-passlogfile', logfile_prefix])
                pass1_cmd.extend(['-an', '-f', 'mp4', null_output])
            else:
                x265_params = f'pass=1:stats={x265_stats}:log-level=error'
                pass1_cmd.extend(video_base + ['-x265-params', x265_params])
                pass1_cmd.extend(['-an', '-f', 'mp4', null_output])

            _run_ffmpeg(pass1_cmd, task, duration, 1)

            if task.cancelled or task.status == 'error':
                return

            task.pass_number = 2
            pass2_cmd = ['ffmpeg', '-y', '-i', input_path]

            if codec == 'h264':
                pass2_cmd.extend(video_base + ['-pass', '2', '-passlogfile', logfile_prefix])
            else:
                x265_params = f'pass=2:stats={x265_stats}:log-level=error'
                pass2_cmd.extend(video_base + ['-x265-params', x265_params])

            pass2_cmd.extend(audio_opts + common_opts + [output_path])

            _run_ffmpeg(pass2_cmd, task, duration, 2)
        finally:
            _cleanup_passlog(logfile_prefix)
    else:
        task.pass_number = 1
        cmd = ['ffmpeg', '-y', '-i', input_path]
        cmd.extend(video_base + audio_opts + common_opts + [output_path])
        _run_ffmpeg(cmd, task, duration, 1)

    with TASKS_LOCK:
        if task.status == 'processing':
            if os.path.exists(output_path) and os.path.getsize(output_path) > 0:
                result_info = get_video_info(output_path)
                if result_info:
                    task.result = result_info
                task.progress = 100
                task.status = 'completed'
            else:
                task.status = 'error'
                task.error = 'Output file not found or empty'
                _notify_status(task, 'error')


def _cleanup_passlog(prefix):
    """Remove every two-pass stats artifact belonging to this task.

    x264 writes <prefix>-0.log plus optional .mbtree/.cutree files, while
    x265 writes the stats file given by the `stats` x265-param and appends
    .cutree. Globbing the task-unique prefix covers all of them.
    """
    for filepath in glob.glob(glob.escape(prefix) + '*'):
        try:
            if os.path.isfile(filepath):
                os.remove(filepath)
        except OSError:
            pass


def _run_ffmpeg(cmd, task, total_duration, pass_num):
    time_pattern = re.compile(r'time=(\d+):(\d+):(\d+)\.(\d+)')
    fps_pattern = re.compile(r'fps=\s*([\d.]+)')
    speed_pattern = re.compile(r'speed=\s*([\d.]+)x')

    try:
        task.process = subprocess.Popen(
            cmd,
            stderr=subprocess.PIPE,
            stdout=subprocess.PIPE,
            universal_newlines=True,
            bufsize=1,
            preexec_fn=os.setsid if os.name != 'nt' else None
        )
    except FileNotFoundError:
        task.status = 'error'
        task.error = 'FFmpeg tidak ditemukan. Pastikan FFmpeg terinstall di sistem.'
        _notify_status(task, 'error')
        return

    for line in iter(task.process.stderr.readline, ''):
        line = line.strip()
        if not line:
            continue

        with TASKS_LOCK:
            if task.cancelled:
                _kill_process(task)
                break
            task.ffmpeg_log.append(line)

        time_match = time_pattern.search(line)
        fps_match = fps_pattern.search(line)
        speed_match = speed_pattern.search(line)

        if time_match:
            try:
                h, m, s, ms = map(int, time_match.groups())
                current_time = h * 3600 + m * 60 + s + ms / 100
            except:
                continue

            if total_duration > 0:
                if pass_num == 1:
                    progress = (current_time / total_duration) * 50.0
                else:
                    if current_time >= total_duration:
                        progress = 100.0
                    else:
                        progress = 50.0 + (current_time / total_duration) * 50.0
                with TASKS_LOCK:
                    task.progress = min(round(progress, 1), 99.9)

            if fps_match:
                try:
                    with TASKS_LOCK:
                        task.current_fps = round(float(fps_match.group(1)), 1)
                except:
                    pass

            if speed_match:
                try:
                    speed_val = float(speed_match.group(1))
                    with TASKS_LOCK:
                        task.speed = f"{speed_val:.1f}x"
                except:
                    pass

            if current_time > 0 and task.start_time:
                remaining = (total_duration - current_time) / current_time
                elapsed = time.time() - task.start_time
                eta_secs = max(0, elapsed * remaining) if remaining > 0 else 0
                with TASKS_LOCK:
                    task.eta = f"{int(eta_secs // 60):02d}:{int(eta_secs % 60):02d}"

    task.process.wait()

    with TASKS_LOCK:
        if task.cancelled:
            return
        if task.process.returncode != 0:
            last_lines = '\n'.join(task.ffmpeg_log[-20:])
            task.status = 'error'
            task.error = f'FFmpeg gagal (kode {task.process.returncode}).\n{last_lines}'
            _notify_status(task, 'error')


def _kill_process(task):
    if task.process and task.process.poll() is None:
        try:
            if os.name != 'nt':
                os.killpg(os.getpgid(task.process.pid), signal.SIGTERM)
            else:
                task.process.terminate()
        except:
            try:
                task.process.kill()
            except:
                pass
    task.status = 'cancelled'
    _notify_status(task, 'cancelled')


def cancel_compression(task_id):
    with TASKS_LOCK:
        task = TASKS.get(task_id)
        if task and task.status == 'processing':
            task.cancelled = True
            _kill_process(task)
            return True
    return False


def format_filesize(size_bytes):
    if size_bytes < 1024:
        return f"{size_bytes} B"
    elif size_bytes < 1024 * 1024:
        return f"{size_bytes / 1024:.1f} KB"
    elif size_bytes < 1024 * 1024 * 1024:
        return f"{size_bytes / (1024 * 1024):.1f} MB"
    else:
        return f"{size_bytes / (1024 * 1024 * 1024):.2f} GB"


def format_duration(seconds):
    h = int(seconds // 3600)
    m = int((seconds % 3600) // 60)
    s = int(seconds % 60)
    if h > 0:
        return f"{h}:{m:02d}:{s:02d}"
    return f"{m}:{s:02d}"


def generate_thumbnail(filepath, output_path, time_sec=None):
    duration = 0
    info = get_video_info(filepath)
    if info:
        duration = info.get('duration', 0)

    if time_sec is None and duration > 0:
        time_sec = duration * 0.2
    elif time_sec is None:
        time_sec = 1

    cmd = [
        'ffmpeg', '-y',
        '-ss', str(time_sec),
        '-i', filepath,
        '-vframes', '1',
        '-q:v', '5',
        '-vf', 'scale=640:-1',
        output_path
    ]
    try:
        subprocess.run(cmd, capture_output=True, timeout=30)
        return os.path.exists(output_path)
    except Exception:
        return False
