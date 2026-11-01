# VideoKompres

Aplikasi web Flask untuk mengompres video menggunakan FFmpeg. Mendukung target ukuran file, CRF, target bitrate, codec H.264/H.265, pengaturan audio, preview, progress, dan riwayat hasil.

## Fitur

- Upload video hingga 10 GB dalam format MP4, MOV, AVI, MKV, WEBM, atau M4V.
- Mode Smart, CRF, dan Target Bitrate.
- Codec H.264 dan H.265/HEVC.
- Audio dipertahankan, dikompres, atau dihapus.
- Progress FFmpeg, speed, FPS, ETA, dan pembatalan task.
- Download hasil dan perbandingan original vs hasil kompresi.
- Riwayat kompresi dan cleanup file lama.

## Kebutuhan Sistem

- Python 3.10 atau lebih baru.
- FFmpeg dan FFprobe.
- Browser modern dengan dukungan JavaScript.

Ubuntu/Debian:

```bash
sudo apt update
sudo apt install python3 python3-venv ffmpeg
```

Verifikasi FFmpeg:

```bash
ffmpeg -version
ffprobe -version
```

## Instalasi

Gunakan virtual environment agar tidak terkena pembatasan PEP 668 dari Python sistem:

```bash
python3 -m venv venv
./venv/bin/python -m ensurepip --upgrade
./venv/bin/python -m pip install -r requirements.txt
```

## Menjalankan Lokal

```bash
./venv/bin/python app.py
```

Buka `http://127.0.0.1:5000`. Jika port 5000 digunakan:

```bash
PORT=5001 ./venv/bin/python app.py
```

## Menjalankan dengan Gunicorn

```bash
./venv/bin/gunicorn --workers 1 --threads 4 --bind 127.0.0.1:5000 app:app
```


## Test

```bash
./venv/bin/python -m pytest -q
```

Test mencakup validasi API, parser HTTP Range, file video invalid, callback status task, proteksi cleanup, dan concurrent update history.

## Konfigurasi Environment

| Variable | Default | Keterangan |
| --- | --- | --- |
| `HOST` | `127.0.0.1` | Host server Flask development |
| `PORT` | `5000` | Port server |
| `FLASK_DEBUG` | `0` | Debug hanya untuk development lokal |
| `MAX_ACTIVE_TASKS` | `2` | Jumlah kompresi aktif maksimum |

Contoh:

```bash
FLASK_DEBUG=0 MAX_ACTIVE_TASKS=2 PORT=5001 ./venv/bin/python app.py
```

## Struktur Direktori

```text
.
├── app.py
├── ffmpeg_utils.py
├── routes/main.py
├── templates/index.html
├── static/css/style.css
├── static/js/main.js
├── tests/test_app.py
├── uploads/                 # runtime, diabaikan Git
└── outputs/                 # runtime, diabaikan Git
```

## API Utama

- `POST /api/upload` upload video.
- `GET /api/info/<file_id>` metadata video.
- `GET /api/stream/<file_id>` preview video original.
- `POST /api/estimate` estimasi bitrate.
- `POST /api/compress` mulai kompresi.
- `GET /api/progress/<task_id>` progress task.
- `POST /api/cancel/<task_id>` batalkan kompresi.
- `GET /api/download/<task_id>` download hasil.
- `GET` atau `DELETE /api/history` baca atau hapus riwayat.
- `POST /api/cleanup` bersihkan file orphan lama.

## Catatan Keamanan

Aplikasi belum memiliki autentikasi. Jangan mengeksposnya langsung ke internet tanpa autentikasi, reverse proxy, pembatasan akses, dan HTTPS.

File upload, output, cache, log FFmpeg, dan virtualenv diabaikan melalui `.gitignore`.
