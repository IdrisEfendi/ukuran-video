import json
import os
import threading

import pytest

import routes.main as main
from app import app
from ffmpeg_utils import CompressionTask, _notify_status, get_video_info


@pytest.fixture()
def client():
    app.config.update(TESTING=True)
    return app.test_client()


def test_invalid_api_inputs_return_client_errors(client):
    response = client.post('/api/estimate', json={})
    assert response.status_code == 400
    assert 'error' in response.get_json()

    response = client.get('/api/progress/not-a-uuid')
    assert response.status_code == 404


def test_range_parser_rejects_invalid_and_extreme_headers():
    assert main._parse_range('bytes=0-99', 1000) == (0, 99)
    assert main._parse_range('bytes=-100', 1000) == (900, 999)
    assert main._parse_range('bytes=0-1,2-3', 1000) is None
    assert main._parse_range('bytes=' + '9' * 1000 + '-', 1000) is None


def test_ffprobe_invalid_file_returns_none(tmp_path):
    path = tmp_path / 'invalid.mp4'
    path.write_bytes(b'not a video')
    assert get_video_info(str(path)) is None


def test_status_callback_supports_error_and_cancelled():
    statuses = []
    task = CompressionTask('id', 'input', 'output', {}, {})
    task.status_callback = statuses.append
    _notify_status(task, 'error')
    _notify_status(task, 'cancelled')
    assert statuses == ['error', 'cancelled']


def test_cleanup_protects_history_files(tmp_path, monkeypatch):
    upload = tmp_path / '3cd577bb-0577-498c-95ab-b9b37532f0b3.mp4'
    output = tmp_path / '181877f9-eb4b-44f3-8c27-558c174e9c1a.mp4'
    upload.write_bytes(b'upload')
    output.write_bytes(b'output')
    old = os.path.getmtime(upload) - main.CLEANUP_MAX_AGE_SECONDS - 1
    os.utime(upload, (old, old))
    os.utime(output, (old, old))

    monkeypatch.setattr(main, 'UPLOAD_DIR', str(tmp_path))
    monkeypatch.setattr(main, 'OUTPUT_DIR', str(tmp_path))
    entries = [{'id': '181877f9-eb4b-44f3-8c27-558c174e9c1a',
                'filename': '3cd577bb-0577-498c-95ab-b9b37532f0b3.mp4'}]
    monkeypatch.setattr(main, '_load_history', lambda: entries)
    protected = set(main._history_file_candidates(entries))
    main._cleanup_stale_files(str(tmp_path), protected, __import__('time').time())
    assert upload.exists()


def test_history_updates_are_serialized(tmp_path, monkeypatch):
    history_file = tmp_path / 'history.json'
    monkeypatch.setattr(main, 'HISTORY_FILE', str(history_file))
    with main.HISTORY_LOCK:
        main._write_history([])

    def save(index):
        main._save_history({'id': f'{index:08d}-0000-0000-0000-000000000000', 'status': 'processing'})

    threads = [threading.Thread(target=save, args=(index,)) for index in range(10)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    data = json.loads(history_file.read_text())
    assert len(data) == 10
