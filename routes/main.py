import os
import re
import math
import uuid
import json
import time
import threading
import flask
from datetime import datetime
from flask import Blueprint, request, jsonify, send_file, render_template
from ffmpeg_utils import (
    get_video_info, calculate_target_bitrate, estimate_min_recommended,
    CompressionTask, start_compression, cancel_compression, generate_thumbnail,
    format_filesize, format_duration, TASKS, TASKS_LOCK
)

bp = Blueprint('main', __name__)

UPLOAD_DIR = os.path.join(os.path.dirname(os.path.dirname(__file__)), 'uploads')
OUTPUT_DIR = os.path.join(os.path.dirname(os.path.dirname(__file__)), 'outputs')
THUMBNAIL_DIR = os.path.join(os.path.dirname(os.path.dirname(__file__)), 'static', 'thumbnails')


def _format_bitrate(bps):
    if not bps or bps == 0:
        return 'N/A'
    return f"{bps / 1000:.0f} kbps"
HISTORY_FILE = os.path.join(os.path.dirname(os.path.dirname(__file__)), 'history.json')
HISTORY_LOCK = threading.RLock()

ALLOWED_EXTENSIONS = {'.mp4', '.mov', '.avi', '.mkv', '.webm', '.m4v'}
MAX_FILE_SIZE = 10 * 1024 * 1024 * 1024
CLEANUP_MAX_AGE_SECONDS = 24 * 60 * 60
try:
    MAX_ACTIVE_TASKS = max(1, int(os.environ.get('MAX_ACTIVE_TASKS', '2')))
except ValueError:
    MAX_ACTIVE_TASKS = 2

VALID_CODECS = {'h264', 'h265'}
VALID_PRESETS = {
    'ultrafast', 'superfast', 'veryfast', 'faster', 'fast',
    'medium', 'slow', 'slower', 'veryslow',
}
VALID_MODES = {'smart', 'crf', 'bitrate'}
VALID_AUDIO_MODES = {'keep', 'compress', 'remove'}
MIN_TARGET_SIZE_MB = 1
MAX_TARGET_SIZE_MB = 10 * 1024
MIN_CRF = 0
MAX_CRF = 51
DEFAULT_CRF = 23
MIN_BITRATE_KBPS = 1
MAX_BITRATE_KBPS = 1_000_000
BITRATE_RE = re.compile(r'^(\d+(?:\.\d+)?)([kKmM]?)$')

UUID_RE = re.compile(r'^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$')
RANGE_RE = re.compile(r'^bytes=(\d*)-(\d*)$')
STREAM_CHUNK_SIZE = 1024 * 1024
MAX_RANGE_HEADER_LENGTH = 128

os.makedirs(UPLOAD_DIR, exist_ok=True)
os.makedirs(OUTPUT_DIR, exist_ok=True)


def _allowed_file(filename):
    _, ext = os.path.splitext(filename.lower())
    return ext in ALLOWED_EXTENSIONS


def _is_uuid(value):
    return isinstance(value, str) and UUID_RE.match(value) is not None


def _find_upload(file_id):
    """Resolve an uploaded file by UUID, without globbing or path traversal."""
    if not _is_uuid(file_id):
        return None
    for ext in ALLOWED_EXTENSIONS:
        candidate = os.path.join(UPLOAD_DIR, f"{file_id}{ext}")
        if os.path.isfile(candidate):
            return candidate
    return None


def _history_upload_path(entry):
    """Upload path referenced by a history entry, or None if it is not a stored upload."""
    filename = entry.get('filename') if isinstance(entry, dict) else None
    if not isinstance(filename, str):
        return None
    stem, ext = os.path.splitext(filename)
    if not _is_uuid(stem) or ext.lower() not in ALLOWED_EXTENSIONS:
        return None
    return os.path.join(UPLOAD_DIR, f"{stem}{ext.lower()}")


class ValidationError(ValueError):
    pass


def _json_body():
    data = request.get_json(silent=True)
    if not isinstance(data, dict):
        raise ValidationError('Body JSON tidak valid')
    return data


def _validate_file_id(value):
    if not _is_uuid(value):
        raise ValidationError('file_id tidak valid')
    return value


def _validate_choice(value, allowed, field):
    if not isinstance(value, str) or value not in allowed:
        raise ValidationError(f"{field} harus salah satu dari: {', '.join(sorted(allowed))}")
    return value


def _validate_number(value, field, minimum, maximum):
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValidationError(f'{field} harus berupa angka')
    if not math.isfinite(value) or not (minimum <= value <= maximum):
        raise ValidationError(f'{field} harus di antara {minimum:g} dan {maximum:g}')
    return float(value)


def _validate_target_size(value):
    return _validate_number(value, 'target_size_mb', MIN_TARGET_SIZE_MB, MAX_TARGET_SIZE_MB)


def _validate_crf(value):
    return _validate_number(value, 'crf', MIN_CRF, MAX_CRF)


def _validate_bitrate(value):
    """Validate a bitrate and normalize it to kbps, e.g. '1500k'."""
    message = 'custom_bitrate tidak valid, gunakan format seperti "1500k" atau "2M"'
    if isinstance(value, bool) or value is None:
        raise ValidationError(message)

    if isinstance(value, str):
        match = BITRATE_RE.match(value.strip())
        if not match:
            raise ValidationError(message)
        number, unit = match.groups()
        kbps = float(number) * (1000 if unit.lower() == 'm' else 1)
    elif isinstance(value, (int, float)):
        kbps = float(value)
    else:
        raise ValidationError(message)

    if not math.isfinite(kbps) or not (MIN_BITRATE_KBPS <= kbps <= MAX_BITRATE_KBPS):
        raise ValidationError(
            f'custom_bitrate harus di antara {MIN_BITRATE_KBPS} dan {MAX_BITRATE_KBPS} kbps'
        )
    return f'{kbps:g}k'


def _parse_range(range_header, file_size):
    """Parse a single byte range. Returns inclusive (start, end) or None."""
    if not isinstance(range_header, str) or len(range_header) > MAX_RANGE_HEADER_LENGTH:
        return None
    match = RANGE_RE.match(range_header.strip())
    if not match or file_size <= 0:
        return None

    start_str, end_str = match.groups()
    if not start_str and not end_str:
        return None

    try:
        if not start_str:
            suffix_length = int(end_str)
        else:
            start = int(start_str)
            end = int(end_str) if end_str else file_size - 1
    except ValueError:
        return None

    if not start_str:
        if suffix_length <= 0:
            return None
        return max(0, file_size - suffix_length), file_size - 1

    if start > end or start >= file_size:
        return None
    return start, min(end, file_size - 1)


def _file_chunks(path, start, length, chunk_size=STREAM_CHUNK_SIZE):
    with open(path, 'rb') as f:
        f.seek(start)
        remaining = length
        while remaining > 0:
            chunk = f.read(min(chunk_size, remaining))
            if not chunk:
                break
            remaining -= len(chunk)
            yield chunk


def _stream_file(path, mimetype='video/mp4'):
    """Serve a file with range support, streaming instead of loading it in memory."""
    file_size = os.path.getsize(path)
    range_header = request.headers.get('Range')

    if range_header:
        parsed = _parse_range(range_header, file_size)
        if parsed is None:
            response = flask.Response(status=416, mimetype='text/plain')
            response.headers['Content-Range'] = f'bytes */{file_size}'
            response.headers['Accept-Ranges'] = 'bytes'
            return response

        start, end = parsed
        length = end - start + 1
        response = flask.Response(
            _file_chunks(path, start, length), status=206, mimetype=mimetype
        )
        response.headers['Content-Range'] = f'bytes {start}-{end}/{file_size}'
        response.headers['Content-Length'] = str(length)
    else:
        response = flask.Response(
            _file_chunks(path, 0, file_size), status=200, mimetype=mimetype
        )
        response.headers['Content-Length'] = str(file_size)

    response.headers['Accept-Ranges'] = 'bytes'
    return response


def _load_history():
    with HISTORY_LOCK:
        if os.path.exists(HISTORY_FILE):
            try:
                with open(HISTORY_FILE, 'r') as f:
                    value = json.load(f)
                return value if isinstance(value, list) else []
            except (OSError, ValueError, TypeError):
                return []
        return []


def _write_history(history):
    """Atomically replace history so readers never see partial JSON."""
    temp_path = f'{HISTORY_FILE}.{os.getpid()}.tmp'
    with open(temp_path, 'w') as f:
        json.dump(history, f, indent=2)
        f.flush()
        os.fsync(f.fileno())
    os.replace(temp_path, HISTORY_FILE)


def _save_history(entry):
    with HISTORY_LOCK:
        history = _load_history()
        history.insert(0, entry)
        _write_history(history[:100])


def _update_history_status(task_id, status, **fields):
    with HISTORY_LOCK:
        history = _load_history()
        for entry in history:
            if isinstance(entry, dict) and entry.get('id') == task_id:
                entry['status'] = status
                entry.update(fields)
                break
        _write_history(history)


@bp.route('/')
def index():
    return render_template('index.html')


@bp.route('/api/upload', methods=['POST'])
def upload_file():
    if 'file' not in request.files:
        return jsonify({'error': 'Tidak ada file yang diupload'}), 400

    file = request.files['file']
    if file.filename == '':
        return jsonify({'error': 'Nama file kosong'}), 400

    if not _allowed_file(file.filename):
        return jsonify({'error': f'Format file tidak didukung. Gunakan: {", ".join(ALLOWED_EXTENSIONS)}'}), 400

    file_id = str(uuid.uuid4())
    _, ext = os.path.splitext(file.filename.lower())
    filename = f"{file_id}{ext}"
    filepath = os.path.join(UPLOAD_DIR, filename)
    file.save(filepath)

    file_size = os.path.getsize(filepath)
    if file_size > MAX_FILE_SIZE:
        os.remove(filepath)
        return jsonify({'error': 'File terlalu besar. Maksimal 10 GB.'}), 400

    info = get_video_info(filepath)
    if not info:
        os.remove(filepath)
        return jsonify({'error': 'Gagal membaca informasi video. File mungkin rusak atau tidak didukung.'}), 400

    info['file_id'] = file_id
    info['filepath'] = filepath

    return jsonify(info)


@bp.route('/api/info/<file_id>')
def file_info(file_id):
    filepath = _find_upload(file_id)
    if not filepath:
        return jsonify({'error': 'File tidak ditemukan'}), 404
    info = get_video_info(filepath)
    if not info:
        return jsonify({'error': 'Gagal membaca informasi video'}), 400
    info['file_id'] = file_id
    return jsonify(info)


@bp.route('/api/stream/<file_id>')
def stream_video(file_id):
    filepath = _find_upload(file_id)
    if not filepath:
        return jsonify({'error': 'File tidak ditemukan'}), 404

    return _stream_file(filepath)


@bp.route('/api/thumbnail/<file_id>')
def video_thumbnail(file_id):
    filepath = _find_upload(file_id)
    if not filepath:
        return jsonify({'error': 'File tidak ditemukan'}), 404

    os.makedirs(THUMBNAIL_DIR, exist_ok=True)
    thumb_path = os.path.join(THUMBNAIL_DIR, f"{file_id}.jpg")

    if not os.path.exists(thumb_path):
        generate_thumbnail(filepath, thumb_path)

    if os.path.exists(thumb_path):
        return send_file(thumb_path, mimetype='image/jpeg')
    return jsonify({'error': 'Gagal membuat thumbnail'}), 500


@bp.route('/api/estimate', methods=['POST'])
def estimate():
    try:
        data = _json_body()
        file_id = _validate_file_id(data.get('file_id'))
        target_size_mb = _validate_target_size(data.get('target_size_mb'))
        codec = _validate_choice(data.get('codec', 'h264'), VALID_CODECS, 'codec')
        audio_mode = _validate_choice(data.get('audio_mode', 'keep'), VALID_AUDIO_MODES, 'audio_mode')
    except ValidationError as exc:
        return jsonify({'error': str(exc)}), 400

    input_path = _find_upload(file_id)
    if not input_path:
        return jsonify({'error': 'File tidak ditemukan'}), 404

    info = get_video_info(input_path)
    if not info:
        return jsonify({'error': 'Gagal membaca informasi video'}), 400

    duration = info.get('duration', 0)
    audio_bitrate = info.get('audio_bitrate', 0)

    if audio_mode == 'keep' and audio_bitrate > 0:
        audio_kbps = audio_bitrate / 1000
    elif audio_mode == 'compress':
        audio_kbps = 96
    else:
        audio_kbps = 0

    target_bps = calculate_target_bitrate(target_size_mb, duration, audio_kbps)
    min_rec = estimate_min_recommended(info)

    warning = None
    if target_bps and min_rec:
        min_bitrate_key = 'h264' if codec == 'h264' else 'h265'
        min_bitrate = min_rec[min_bitrate_key]['bitrate']
        # Target seukuran file asli tidak menurunkan kualitas di bawah sumbernya,
        # jadi jangan tampilkan peringatan "target terlalu kecil" untuk itu.
        original_size_mb = info.get('filesize', 0) / (1024 * 1024)
        keeps_source_quality = original_size_mb > 0 and target_size_mb >= original_size_mb * 0.9
        if target_bps < min_bitrate * 0.7 and not keeps_source_quality:
            rec_size = min_rec[min_bitrate_key]['size_mb']
            warning = {
                'message': f'Target ukuran terlalu kecil untuk video ini. Disarankan minimal {rec_size} MB untuk kualitas yang baik.',
                'recommended_size': rec_size
            }

    return jsonify({
        'target_bitrate': target_bps,
        'target_bitrate_formatted': f"{target_bps / 1000:.0f} kbps" if target_bps else 'N/A',
        'warning': warning,
        'min_recommended': min_rec
    })


@bp.route('/api/compress', methods=['POST'])
def compress():
    try:
        data = _json_body()
        file_id = _validate_file_id(data.get('file_id'))
        codec = _validate_choice(data.get('codec', 'h264'), VALID_CODECS, 'codec')
        preset = _validate_choice(data.get('preset', 'medium'), VALID_PRESETS, 'preset')
        mode = _validate_choice(data.get('mode', 'smart'), VALID_MODES, 'mode')
        audio_mode = _validate_choice(data.get('audio_mode', 'keep'), VALID_AUDIO_MODES, 'audio_mode')

        crf = data.get('crf')
        crf = DEFAULT_CRF if crf is None else _validate_crf(crf)

        target_size_mb = data.get('target_size_mb')
        if target_size_mb is not None:
            target_size_mb = _validate_target_size(target_size_mb)

        custom_bitrate = data.get('custom_bitrate')
        if custom_bitrate is not None:
            custom_bitrate = _validate_bitrate(custom_bitrate)

        if mode == 'smart' and target_size_mb is None:
            raise ValidationError('target_size_mb wajib diisi untuk mode smart')
        if mode == 'bitrate' and custom_bitrate is None:
            raise ValidationError('custom_bitrate wajib diisi untuk mode bitrate')
    except ValidationError as exc:
        return jsonify({'error': str(exc)}), 400

    input_path = _find_upload(file_id)
    if not input_path:
        return jsonify({'error': 'File tidak ditemukan. Silakan upload ulang.'}), 404

    info = get_video_info(input_path)
    if not info:
        return jsonify({'error': 'Gagal membaca informasi video'}), 400

    task_id = str(uuid.uuid4())
    output_ext = '.mp4'
    output_path = os.path.join(OUTPUT_DIR, f"{task_id}{output_ext}")

    info['file_id'] = file_id

    settings = {
        'codec': codec,
        'preset': preset,
        'mode': mode,
        'audio_mode': audio_mode,
        'target_size_mb': target_size_mb,
        'crf': crf,
        'custom_bitrate': custom_bitrate,
    }

    task = CompressionTask(task_id, input_path, output_path, settings, info)
    task.status_callback = lambda status, task_id=task_id: _update_history_status(task_id, status)
    with TASKS_LOCK:
        active_tasks = sum(task.status in ('queued', 'processing') for task in TASKS.values())
        if active_tasks >= MAX_ACTIVE_TASKS:
            return jsonify({'error': 'Terlalu banyak kompresi berjalan. Silakan coba lagi nanti.'}), 429
        TASKS[task_id] = task

    history_entry = {
        'id': task_id,
        'filename': info['filename'],
        'timestamp': datetime.now().isoformat(),
        'original_size': info['filesize'],
        'original_size_formatted': format_filesize(info['filesize']),
        'status': 'processing',
    }
    _save_history(history_entry)
    start_compression(task)

    return jsonify({'task_id': task_id})


@bp.route('/api/progress/<task_id>')
def get_progress(task_id):
    if not _is_uuid(task_id):
        return jsonify({'error': 'Task tidak ditemukan'}), 404

    with TASKS_LOCK:
        task = TASKS.get(task_id)
        if not task:
            return jsonify({'error': 'Task tidak ditemukan'}), 404

        original_file_id = task.info.get('file_id', '')

        response = {
            'task_id': task_id,
            'status': task.status,
            'progress': task.progress,
            'speed': task.speed,
            'fps': task.current_fps,
            'eta': task.eta,
            'pass_number': task.pass_number,
            'error': task.error,
            'original_file_id': original_file_id,
            'ffmpeg_log': task.ffmpeg_log[-50:],
        }

        if task.status == 'completed' and task.result:
            result = task.result
            original = task.info
            savings = ((original['filesize'] - result['filesize']) / original['filesize'] * 100) if original['filesize'] > 0 else 0
            response['result'] = {
                'original_file_id': original_file_id,
                'original_size': original['filesize'],
                'original_size_formatted': format_filesize(original['filesize']),
                'original_resolution': f"{original.get('width', 0)}x{original.get('height', 0)}",
                'original_bitrate': original.get('bitrate', 0) or original.get('video_bitrate', 0),
                'original_bitrate_formatted': _format_bitrate(original.get('bitrate', 0) or original.get('video_bitrate', 0)),
                'original_duration': original.get('duration', 0),
                'final_size': result['filesize'],
                'final_size_formatted': format_filesize(result['filesize']),
                'savings_percent': round(savings, 1),
                'resolution': f"{result.get('width', 0)}x{result.get('height', 0)}",
                'duration': format_duration(result.get('duration', 0)),
                'duration_sec': result.get('duration', 0),
                'codec': result.get('video_codec', 'unknown'),
                'bitrate': result.get('bitrate', 0) or result.get('video_bitrate', 0),
                'bitrate_formatted': _format_bitrate(result.get('bitrate', 0) or result.get('video_bitrate', 0)),
                'filename': f"{task_id}.mp4",
            }

            history = _load_history()
            for entry in history:
                if entry['id'] == task_id:
                    entry['status'] = 'completed'
                    entry['final_size'] = result['filesize']
                    entry['final_size_formatted'] = format_filesize(result['filesize'])
                    entry['savings_percent'] = round(savings, 1)
                    break
            with HISTORY_LOCK:
                _write_history(history)

        return jsonify(response)


@bp.route('/api/download/<task_id>')
def download(task_id):
    output_path = os.path.join(OUTPUT_DIR, f"{task_id}.mp4")
    if not _is_uuid(task_id) or not os.path.exists(output_path):
        return jsonify({'error': 'File tidak ditemukan'}), 404

    with TASKS_LOCK:
        task = TASKS.get(task_id)

    original_filename = 'compressed_video.mp4'
    if task and task.info:
        name_no_ext = os.path.splitext(task.info['filename'])[0]
        original_filename = f"{name_no_ext}_compressed.mp4"

    return send_file(
        output_path,
        as_attachment=True,
        download_name=original_filename,
        mimetype='video/mp4'
    )


@bp.route('/api/stream_result/<task_id>')
def stream_result(task_id):
    output_path = os.path.join(OUTPUT_DIR, f"{task_id}.mp4")
    if not _is_uuid(task_id) or not os.path.exists(output_path):
        return jsonify({'error': 'File tidak ditemukan'}), 404

    return _stream_file(output_path)


@bp.route('/api/cancel/<task_id>', methods=['POST'])
def cancel(task_id):
    if not _is_uuid(task_id):
        return jsonify({'error': 'Task tidak ditemukan atau sudah selesai'}), 400

    if cancel_compression(task_id):
        return jsonify({'success': True})
    return jsonify({'error': 'Task tidak ditemukan atau sudah selesai'}), 400


def _history_file_candidates(entries):
    """Upload/output paths referenced by history entries, deduplicated."""
    candidates = []
    for entry in entries:
        if not isinstance(entry, dict):
            continue
        entry_id = entry.get('id')
        if _is_uuid(entry_id):
            candidates.append(os.path.join(OUTPUT_DIR, f"{entry_id}.mp4"))
        upload_path = _history_upload_path(entry)
        if upload_path:
            candidates.append(upload_path)

    seen = set()
    unique = []
    for path in candidates:
        absolute = os.path.abspath(path)
        if absolute not in seen:
            seen.add(absolute)
            unique.append(path)
    return unique


@bp.route('/api/history', methods=['GET', 'DELETE'])
def history():
    if request.method == 'DELETE':
        delete_files_flag = request.args.get('delete_files', '0')
        if delete_files_flag not in ('0', '1'):
            return jsonify({'error': 'delete_files harus 0 atau 1'}), 400

        entries = _load_history()
        deleted_files = []

        if delete_files_flag == '1':
            with TASKS_LOCK:
                busy = any(
                    task.status in ('queued', 'processing')
                    for task in TASKS.values()
                )
            if busy:
                return jsonify({'error': 'Masih ada kompresi berjalan, file tidak bisa dihapus'}), 400

            for candidate in _history_file_candidates(entries):
                try:
                    if os.path.isfile(candidate):
                        os.remove(candidate)
                        deleted_files.append(os.path.basename(candidate))
                except OSError:
                    return jsonify({'error': 'Gagal menghapus file video'}), 500

        try:
            with HISTORY_LOCK:
                _write_history([])
        except OSError:
            return jsonify({'error': 'Gagal menghapus riwayat'}), 500

        return jsonify({'success': True, 'deleted_files': deleted_files})

    return jsonify(_load_history())


@bp.route('/api/history/<entry_id>', methods=['DELETE'])
def delete_history_entry(entry_id):
    if not _is_uuid(entry_id):
        return jsonify({'error': 'Riwayat tidak ditemukan'}), 404

    delete_files_flag = request.args.get('delete_files', '0')
    if delete_files_flag not in ('0', '1'):
        return jsonify({'error': 'delete_files harus 0 atau 1'}), 400
    delete_files = delete_files_flag == '1'

    history = _load_history()
    entry = next(
        (item for item in history if isinstance(item, dict) and item.get('id') == entry_id),
        None,
    )
    if entry is None:
        return jsonify({'error': 'Riwayat tidak ditemukan'}), 404

    remaining = [
        item for item in history
        if not (isinstance(item, dict) and item.get('id') == entry_id)
    ]

    deleted_files = []
    if delete_files:
        with TASKS_LOCK:
            task = TASKS.get(entry_id)
            if task and task.status in ('queued', 'processing'):
                return jsonify({'error': 'Kompresi masih berjalan, file tidak bisa dihapus'}), 400

            # Never remove a file another task is still using.
            protected = set()
            for other_id, other_task in TASKS.items():
                if other_id == entry_id:
                    continue
                for path in (other_task.input_path, other_task.output_path):
                    if path:
                        protected.add(os.path.abspath(path))

        # Never remove an upload still referenced by another history entry.
        for item in remaining:
            other_upload = _history_upload_path(item)
            if other_upload:
                protected.add(os.path.abspath(other_upload))

        candidates = [
            os.path.join(OUTPUT_DIR, f"{entry_id}.mp4"),
            _history_upload_path(entry),
        ]
        for candidate in candidates:
            if not candidate or os.path.abspath(candidate) in protected:
                continue
            try:
                if os.path.isfile(candidate):
                    os.remove(candidate)
                    deleted_files.append(os.path.basename(candidate))
            except OSError:
                return jsonify({'error': 'Gagal menghapus file video'}), 500

    try:
        with HISTORY_LOCK:
            _write_history(remaining)
    except OSError:
        return jsonify({'error': 'Gagal menghapus riwayat'}), 500

    return jsonify({'success': True, 'deleted_files': deleted_files})


def _task_file_paths():
    """Files referenced by any known task, which cleanup must never remove."""
    paths = set()
    with TASKS_LOCK:
        for task in TASKS.values():
            for path in (task.input_path, task.output_path):
                if path:
                    paths.add(os.path.abspath(path))
    return paths


def _cleanup_stale_files(directory, protected_paths, now, max_age=CLEANUP_MAX_AGE_SECONDS):
    """Delete orphaned files older than max_age. Returns the number removed."""
    deleted = 0
    try:
        entries = os.listdir(directory)
    except OSError:
        return deleted

    for name in entries:
        filepath = os.path.join(directory, name)
        if not os.path.isfile(filepath):
            continue
        if os.path.abspath(filepath) in protected_paths:
            continue
        try:
            if now - os.path.getmtime(filepath) <= max_age:
                continue
            os.remove(filepath)
            deleted += 1
        except OSError:
            continue
    return deleted


@bp.route('/api/cleanup', methods=['POST'])
def cleanup():
    protected_paths = _task_file_paths()
    protected_paths.update(
        os.path.abspath(path)
        for path in _history_file_candidates(_load_history())
    )
    now = time.time()
    deleted = _cleanup_stale_files(UPLOAD_DIR, protected_paths, now)
    deleted += _cleanup_stale_files(OUTPUT_DIR, protected_paths, now)
    return jsonify({'success': True, 'deleted': deleted})
