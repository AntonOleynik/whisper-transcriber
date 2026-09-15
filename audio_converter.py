"""Локальная HTML-страница для извлечения MP3 из видео через FFmpeg."""

from __future__ import annotations

import os
import shutil
import socket
import subprocess
import sys
import threading
import uuid
import webbrowser
from pathlib import Path

from flask import Flask, render_template_string, request, send_from_directory
from werkzeug.utils import secure_filename


APP_DIR = Path(__file__).resolve().parent
TEMP_DIR = APP_DIR / ".converter_temp"
OUTPUT_DIR = APP_DIR / "Готовые MP3"
ALLOWED_EXTENSIONS = {
    "mp4", "mkv", "mov", "avi", "webm", "mpeg", "mpg", "m4v", "wmv",
    "mp3", "wav", "m4a", "aac", "ogg", "opus", "flac", "wma",
}

app = Flask(__name__)
app.config["MAX_CONTENT_LENGTH"] = 25 * 1024 * 1024 * 1024  # до 25 ГБ


PAGE = """<!doctype html>
<html lang="ru"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Видео → MP3</title>
<style>
  :root { color-scheme: dark; font-family: Segoe UI, Arial, sans-serif; }
  body { margin:0; min-height:100vh; display:grid; place-items:center; background:linear-gradient(135deg,#101625,#172a45); color:#f6f8ff; }
  main { width:min(620px,calc(100% - 36px)); padding:36px; background:#202c42; border:1px solid #395071; border-radius:20px; box-shadow:0 22px 60px #05081288; }
  h1 { margin:0 0 10px; font-size:28px; } p { color:#c4d0e4; line-height:1.55; }
  .pick { display:block; margin:25px 0 14px; padding:25px; border:2px dashed #5c8ccc; border-radius:14px; text-align:center; cursor:pointer; background:#192439; }
  .pick:hover { border-color:#8bb8ff; background:#1d2b44; } input[type=file] { display:none; }
  .line { display:flex; align-items:center; gap:14px; flex-wrap:wrap; margin:16px 0 25px; }
  select, button { border-radius:9px; padding:11px 14px; font:inherit; } select { background:#152137; color:#fff; border:1px solid #536e94; }
  button { border:0; color:#061222; background:#85b8ff; font-weight:700; cursor:pointer; } button:hover { background:#b1d2ff; }
  button:disabled { opacity:.5; cursor:wait; } .filename { color:#91c4ff; min-height:24px; overflow-wrap:anywhere; }
  .notice { margin-top:22px; padding-top:18px; border-top:1px solid #40536e; font-size:14px; color:#afbdd1; }
  .error { padding:13px; border-radius:9px; background:#632b36; color:#ffe5e9; white-space:pre-wrap; }
  .ready { padding:17px; border-radius:11px; background:#173f35; color:#dbfff4; } .ready a { color:#fff; font-weight:700; }
</style></head><body><main>
<h1>Видео → MP3</h1>
<p>Выберите видео или аудиофайл. Из него будет создан один MP3 без видеодорожки.</p>
{% if error %}<div class="error">{{ error }}</div>{% endif %}
{% if result %}<div class="ready">MP3 готов: <a href="{{ url_for('download', filename=result) }}">скачать файл</a><br><small>Копия также сохранена в папке «Готовые MP3» рядом с программой.</small></div>{% endif %}
<form method="post" action="{{ url_for('convert') }}" enctype="multipart/form-data" id="form">
  <label class="pick" for="file"><strong>Нажмите, чтобы выбрать файл</strong><br><span>MP4, MKV, MOV, AVI, WebM и другие форматы</span></label>
  <input id="file" name="file" type="file" accept="video/*,audio/*,.mkv,.webm,.wmv,.flac,.opus" required>
  <div class="filename" id="name">Файл ещё не выбран</div>
  <div class="line"><label>Качество MP3
    <select name="bitrate"><option value="320k">Высокое — 320 кбит/с</option><option value="192k" selected>Обычное — 192 кбит/с</option><option value="128k">Компактное — 128 кбит/с</option></select>
  </label><button id="submit" type="submit">Сделать MP3</button></div>
</form>
<div class="notice">Обработка идёт только на этом компьютере. Большие видео могут конвертироваться несколько минут; не закрывайте это окно до появления ссылки.</div>
</main><script>
const file=document.querySelector('#file'), name=document.querySelector('#name'), form=document.querySelector('#form'), button=document.querySelector('#submit');
file.addEventListener('change',()=>name.textContent=file.files.length ? file.files[0].name : 'Файл ещё не выбран');
form.addEventListener('submit',()=>{button.disabled=true;button.textContent='Конвертирую…';});
</script></body></html>"""


def find_ffmpeg() -> str:
    """Находит системный FFmpeg или компактную копию из imageio-ffmpeg."""
    executable = shutil.which("ffmpeg")
    if executable:
        return executable
    try:
        from imageio_ffmpeg import get_ffmpeg_exe
        executable = get_ffmpeg_exe()
        if Path(executable).is_file():
            return executable
    except Exception as error:
        raise RuntimeError("FFmpeg не найден. Запустите setup.bat повторно.") from error
    raise RuntimeError("FFmpeg не найден. Запустите setup.bat повторно.")


def unique_path(stem: str) -> Path:
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    clean_stem = stem.strip() or "audio"
    candidate = OUTPUT_DIR / f"{clean_stem}.mp3"
    number = 2
    while candidate.exists():
        candidate = OUTPUT_DIR / f"{clean_stem}_{number}.mp3"
        number += 1
    return candidate


@app.get("/")
def index():
    return render_template_string(PAGE, error=None, result=None)


@app.post("/convert")
def convert():
    uploaded = request.files.get("file")
    if not uploaded or not uploaded.filename:
        return render_template_string(PAGE, error="Выберите файл.", result=None), 400

    original_name = uploaded.filename
    extension = Path(original_name).suffix.lower().lstrip(".")
    if extension not in ALLOWED_EXTENSIONS:
        return render_template_string(PAGE, error="Этот формат не поддерживается. Выберите распространённый аудио- или видеофайл.", result=None), 400

    safe_name = secure_filename(original_name) or f"input.{extension}"
    job_dir = TEMP_DIR / uuid.uuid4().hex
    job_dir.mkdir(parents=True, exist_ok=False)
    input_file = job_dir / safe_name
    uploaded.save(input_file)
    output_file = unique_path(Path(original_name).stem)
    bitrate = request.form.get("bitrate", "192k")
    if bitrate not in {"128k", "192k", "320k"}:
        bitrate = "192k"

    try:
        command = [
            find_ffmpeg(), "-hide_banner", "-loglevel", "error", "-i", str(input_file),
            "-vn", "-map_metadata", "-1", "-c:a", "libmp3lame", "-b:a", bitrate,
            "-ar", "44100", "-y", str(output_file),
        ]
        process = subprocess.run(command, capture_output=True, text=True, encoding="utf-8", errors="replace")
        if process.returncode != 0 or not output_file.is_file():
            output_file.unlink(missing_ok=True)
            details = (process.stderr or process.stdout or "FFmpeg не смог обработать этот файл.").strip()
            raise RuntimeError(details[-1500:])
        return render_template_string(PAGE, error=None, result=output_file.name)
    except Exception as error:
        return render_template_string(PAGE, error=f"Конвертация не удалась:\n{error}", result=None), 500
    finally:
        shutil.rmtree(job_dir, ignore_errors=True)


@app.get("/download/<path:filename>")
def download(filename: str):
    return send_from_directory(OUTPUT_DIR, filename, as_attachment=True)


def find_available_port() -> int:
    """Выбирает свободный локальный порт, не занимая привычные 8765/8766."""
    for port in range(8790, 8811):
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
            probe.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            try:
                probe.bind(("127.0.0.1", port))
            except OSError:
                continue
            return port
    raise RuntimeError("Не удалось найти свободный локальный порт в диапазоне 8790–8810.")


PORT = find_available_port()


def open_browser() -> None:
    webbrowser.open_new(f"http://127.0.0.1:{PORT}")


if __name__ == "__main__":
    # При запуске через pythonw у процесса нет консольных потоков вывода.
    # Werkzeug всё равно пишет служебные сообщения, поэтому подставляем тихий
    # поток, чтобы окно конвертера не завершалось с ошибкой NoneType.write.
    if sys.stdout is None:
        sys.stdout = open(os.devnull, "w", encoding="utf-8")
    if sys.stderr is None:
        sys.stderr = open(os.devnull, "w", encoding="utf-8")
    TEMP_DIR.mkdir(exist_ok=True)
    threading.Timer(0.8, open_browser).start()
    app.run(host="127.0.0.1", port=PORT, debug=False, use_reloader=False)
