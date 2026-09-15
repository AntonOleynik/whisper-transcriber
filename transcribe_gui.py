"""Локальная расшифровка аудио и видео с помощью faster-whisper.

Программа ничего не загружает в облако: запись обрабатывается на этом ПК.
При первом запуске выбранная модель Whisper скачается из Hugging Face.
"""

from __future__ import annotations

import ctypes
import os
import queue
import re
import shutil
import sys
import subprocess
import threading
import time
import traceback
import webbrowser
from dataclasses import dataclass
from pathlib import Path
from tkinter import BOTH, END, LEFT, RIGHT, X, BooleanVar, StringVar, Tk, filedialog, messagebox, ttk
from typing import Any


APP_DIR = Path(__file__).resolve().parent


def default_output_dir() -> Path:
    """Выбирает Documents текущего пользователя, сохраняя привычный путь в OneDrive."""
    candidates = []
    one_drive = os.environ.get("OneDrive", "").strip()
    if one_drive:
        candidates.append(Path(one_drive) / "Documents")
    candidates.extend((Path.home() / "OneDrive" / "Documents", Path.home() / "Documents"))
    for documents in candidates:
        if documents.is_dir():
            return documents / "DC" / "Транскрипты" / "Выводы"
    return Path.home() / "Documents" / "DC" / "Транскрипты" / "Выводы"


DEFAULT_OUTPUT_DIR = default_output_dir()
TOKEN_SERVICE = "WhisperTranscriber"
TOKEN_ACCOUNT = "huggingface"
DEFAULT_PROMPT = ""
KNOWN_FIRST_NAMES = {
    "алексей", "александр", "алёна", "анна", "аня", "антон", "артём", "артем", "борис",
    "вадим", "валентин", "валентина", "валерий", "валерия", "василий", "вера", "виктор",
    "виктория", "виталий", "влад", "владимир", "владислав", "владлена", "владлена", "глеб",
    "дарья", "денис", "дима", "дмитрий", "евгений", "екатерина", "елена", "иван", "игорь",
    "илья", "инна", "кирилл", "константин", "ксения", "лариса", "лёша", "леша", "леонид",
    "максим", "мария", "марина", "михаил", "миша", "миш", "наталья", "настя", "анастасия",
    "никита", "николай", "оксана", "олег", "ольга", "павел", "петр", "пётр", "роман", "светлана",
    "сергей", "станислав", "татьяна", "тимур", "фёдор", "федор", "юлия", "юрий", "яна",
}
MODEL_OPTIONS = (
    "large-v3-turbo",
    "large-v3",
    "distil-large-v3",
    "medium",
    "small",
    "base",
)
LANGUAGES = {
    "Автоопределение": None,
    "Русский": "ru",
    "English": "en",
    "Українська": "uk",
    "Deutsch": "de",
    "Français": "fr",
    "Español": "es",
}
MEDIA_TYPES = (
    "*.mp3 *.wav *.m4a *.aac *.ogg *.opus *.flac *.wma "
    "*.mp4 *.mkv *.mov *.avi *.webm *.mpeg *.mpg"
)


@dataclass
class Segment:
    start: float
    end: float
    text: str
    speaker: str | None = None


def bootstrap_cuda_dlls() -> list[str]:
    """Подключает DLL CUDA из pip-пакетов, если они были установлены setup.bat."""
    added: list[str] = []
    if not hasattr(os, "add_dll_directory"):
        return added

    for base in map(Path, sys.path):
        nvidia_root = base / "nvidia"
        if not nvidia_root.is_dir():
            continue
        for candidate in nvidia_root.glob("*/bin"):
            if candidate.is_dir():
                # Дескриптор нельзя выбрасывать: при его удалении Windows исключает
                # папку из поиска DLL, и cublas64_12.dll пропадает при первом запуске GPU.
                CUDA_DLL_HANDLES.append(os.add_dll_directory(str(candidate)))
                added.append(str(candidate))
    # CTranslate2 загружает CUDA-библиотеки своим способом и использует PATH,
    # а не список os.add_dll_directory. Добавляем пути только в процесс программы.
    if added:
        os.environ["PATH"] = os.pathsep.join(added + [os.environ.get("PATH", "")])
    return added


CUDA_DLL_HANDLES: list[object] = []
CUDA_DLL_PATHS = bootstrap_cuda_dlls()


def srt_timestamp(seconds: float) -> str:
    milliseconds = max(0, round(seconds * 1000))
    hours, rest = divmod(milliseconds, 3_600_000)
    minutes, rest = divmod(rest, 60_000)
    secs, millis = divmod(rest, 1000)
    return f"{hours:02}:{minutes:02}:{secs:02},{millis:03}"


def write_outputs(source: Path, output_dir: Path, segments: list[Segment], metadata: dict[str, Any]) -> Path:
    output_dir.mkdir(parents=True, exist_ok=True)
    base = output_dir / f"{source.stem}_расшифровка"
    index = 2
    while base.with_suffix(".txt").exists():
        base = output_dir / f"{source.stem}_расшифровка_{index}"
        index += 1

    text_file = base.with_suffix(".txt")

    text_lines = []
    for item in segments:
        if not item.text.strip():
            continue
        prefix = f"[{srt_timestamp(item.start)[:-4]}] "
        speaker = f"{item.speaker}: " if item.speaker else ""
        text_lines.append(prefix + speaker + item.text.strip())
    text_file.write_text("\n\n".join(text_lines) + "\n", encoding="utf-8")
    return text_file


def create_model(model_name: str, status: queue.Queue[tuple[str, Any]]) -> tuple[Any, str]:
    from faster_whisper import WhisperModel

    status.put(("status", "Инициализирую Whisper в памяти… Первый запуск может скачать модель."))
    try:
        model = WhisperModel(model_name, device="cuda", compute_type="float16")
        return model, "CUDA / RTX"
    except Exception as error:
        status.put(("status", f"GPU недоступна ({str(error).splitlines()[0]}). Перехожу на CPU…"))
        model = WhisperModel(model_name, device="cpu", compute_type="int8")
        return model, "CPU"


def add_bundled_ffmpeg_to_path() -> None:
    """Даёт pyannote доступ к FFmpeg, который поставляется с этой программой."""
    try:
        from imageio_ffmpeg import get_ffmpeg_exe
        folder = str(Path(get_ffmpeg_exe()).parent)
        if folder not in os.environ.get("PATH", "").split(os.pathsep):
            os.environ["PATH"] = folder + os.pathsep + os.environ.get("PATH", "")
    except Exception:
        pass


def load_saved_token() -> str:
    """Читает токен из Windows Credential Manager."""
    try:
        import keyring
        return keyring.get_password(TOKEN_SERVICE, TOKEN_ACCOUNT) or ""
    except Exception:
        return ""


def save_token(token: str) -> bool:
    """Сохраняет токен в системном защищённом хранилище."""
    try:
        import keyring
        keyring.set_password(TOKEN_SERVICE, TOKEN_ACCOUNT, token.strip())
        return True
    except Exception:
        return False


def ensure_background_output_streams() -> None:
    """Pythonw не создаёт stdout/stderr, а tqdm у загрузчика моделей их ожидает."""
    if sys.stdout is None:
        sys.stdout = open(os.devnull, "w", encoding="utf-8")
    if sys.stderr is None:
        sys.stderr = open(os.devnull, "w", encoding="utf-8")


def load_audio_for_diarization(source: Path, torch_module: Any) -> dict[str, Any]:
    """Декодирует файл через PyAV и не зависит от конфликтующего torchcodec/FFmpeg."""
    try:
        import av
        import numpy as np
    except ImportError as error:
        raise RuntimeError("Не найден аудиодекодер. Запустите setup.bat ещё раз.") from error

    try:
        container = av.open(str(source))
        stream = next(item for item in container.streams if item.type == "audio")
        resampler = av.audio.resampler.AudioResampler(format="fltp", layout="mono", rate=16000)
        chunks: list[Any] = []
        for frame in container.decode(stream):
            frames = resampler.resample(frame)
            if not isinstance(frames, list):
                frames = [frames]
            for converted in frames:
                chunks.append(converted.to_ndarray()[0])
        tail = resampler.resample(None)
        if tail:
            if not isinstance(tail, list):
                tail = [tail]
            chunks.extend(frame.to_ndarray()[0] for frame in tail)
        container.close()
    except Exception as error:
        raise RuntimeError(f"Не удалось прочитать аудиодорожку для определения спикеров: {error}") from error

    if not chunks:
        raise RuntimeError("В файле не найдена аудиодорожка для определения спикеров.")
    waveform = torch_module.from_numpy(np.concatenate(chunks).astype("float32", copy=False)).unsqueeze(0)
    return {"waveform": waveform, "sample_rate": 16000}


def diarize_audio(source: Path, token: str, status: queue.Queue[tuple[str, Any]]) -> list[tuple[float, float, str]]:
    """Находит, кто говорит в каждый момент записи. Модель скачивается только один раз."""
    if not token.strip():
        raise RuntimeError(
            "Для определения спикеров нужен токен Hugging Face. Создайте его по ссылке из окна, "
            "примите условия модели и вставьте токен в поле. Токен хранится в Credential Manager Windows."
        )
    try:
        import torch
        from pyannote.audio import Pipeline
    except ImportError as error:
        raise RuntimeError("Компонент определения спикеров не установлен. Запустите setup_speakers.bat один раз.") from error

    add_bundled_ffmpeg_to_path()
    ensure_background_output_streams()
    status.put(("status", "Инициализирую модель спикеров… Первый запуск может скачать её."))
    pipeline = Pipeline.from_pretrained(
        "pyannote/speaker-diarization-community-1",
        token=token.strip(),
        cache_dir=str(APP_DIR / ".speaker_models"),
    )
    if pipeline is None:
        raise RuntimeError("Не удалось загрузить модель спикеров. Проверьте токен и доступ к модели Hugging Face.")
    device = torch.device("cuda" if torch.cuda.is_available() else "cpu")
    pipeline.to(device)
    status.put(("status", f"Определяю спикеров ({'GPU' if device.type == 'cuda' else 'CPU'})…"))
    # Передаём уже декодированное аудио, чтобы pyannote не пытался загрузить torchcodec.
    result = pipeline(load_audio_for_diarization(source, torch))
    annotation = getattr(result, "exclusive_speaker_diarization", None)
    if annotation is None:
        annotation = result.speaker_diarization
    return [(float(turn.start), float(turn.end), str(label)) for turn, _, label in annotation.itertracks(yield_label=True)]


def assign_speakers(
    segments: list[Segment],
    turns: list[tuple[float, float, str]],
    names: str,
) -> dict[str, str]:
    """Совмещает менее точные фразы Whisper с временными интервалами диаризации."""
    requested_names = [name.strip() for name in names.split(",") if name.strip()]
    mapping: dict[str, str] = {}
    for segment in segments:
        overlaps = [
            (max(0.0, min(segment.end, end) - max(segment.start, start)), raw_name)
            for start, end, raw_name in turns
        ]
        if not overlaps:
            continue
        overlap, raw_name = max(overlaps, key=lambda item: item[0])
        if overlap <= 0:
            continue
        if raw_name not in mapping:
            position = len(mapping)
            mapping[raw_name] = requested_names[position] if position < len(requested_names) else f"Спикер {position + 1}"
        segment.speaker = mapping[raw_name]
    return mapping


def merge_adjacent_segments(segments: list[Segment], max_gap: float = 0.8, max_chars: int = 900) -> list[Segment]:
    """Убирает дробление одной реплики Whisper на несколько строк."""
    merged: list[Segment] = []
    for item in segments:
        if not item.text.strip():
            continue
        if merged:
            previous = merged[-1]
            gap = item.start - previous.end
            combined_length = len(previous.text.rstrip()) + len(item.text.lstrip()) + 1
            if previous.speaker == item.speaker and 0 <= gap <= max_gap and combined_length <= max_chars:
                previous.end = item.end
                previous.text = previous.text.rstrip() + " " + item.text.lstrip()
                continue
        merged.append(item)
    return merged


def infer_speaker_names(segments: list[Segment]) -> dict[str, str]:
    """Находит имена по самопредставлению и обращениям к следующему отвечающему."""
    patterns = (
        re.compile(r"\bменя\s+зовут\s+([A-Za-zА-ЯЁа-яё][A-Za-zА-ЯЁа-яё-]{1,24})", re.IGNORECASE),
        re.compile(r"\bмо[её]\s+имя\s*[—–-]?\s*([A-Za-zА-ЯЁа-яё][A-Za-zА-ЯЁа-яё-]{1,24})", re.IGNORECASE),
        re.compile(r"\bя\s*[—–-]\s*([A-Za-zА-ЯЁа-яё][A-Za-zА-ЯЁа-яё-]{1,24})", re.IGNORECASE),
    )
    addressed_pattern = re.compile(
        r"^\s*([A-Za-zА-ЯЁа-яё][A-Za-zА-ЯЁа-яё-]{1,24}),\s+"
        r"(?=(?:ты|вы|что|как|почему|зачем|можешь|думаешь|расскажи|скажи|считаешь)\b)",
        re.IGNORECASE,
    )
    stop_words = {
        "это", "здесь", "думаю", "считаю", "хочу", "могу", "буду", "говорю", "вижу", "да",
        "прости", "слушай", "давайте", "ну", "короче", "ребята", "смотрите", "окей", "хорошо",
        "коллеги", "пожалуйста",
    }
    replacements: dict[str, str] = {}
    for item in segments:
        if not item.speaker:
            continue
        for pattern in patterns:
            match = pattern.search(item.text)
            if not match:
                continue
            candidate = match.group(1).strip(" .,!?;:")
            if candidate.casefold() in stop_words or len(candidate) < 2:
                continue
            replacements.setdefault(item.speaker, candidate[:1].upper() + candidate[1:])
            break

    # Вопрос «Влад, что думаешь?» обычно адресован следующему голосу.
    # Ищем первый отличный от текущего голос в ближайших фрагментах ответа.
    addressed_votes: dict[str, dict[str, int]] = {}
    for position, item in enumerate(segments):
        match = addressed_pattern.search(item.text)
        if not match or not item.speaker:
            continue
        candidate = match.group(1).strip(" .,!?;:")
        candidate_key = candidate.casefold()
        # Обращение должно быть похоже на настоящее имя: «Влад, что думаешь?».
        # Так «Да, что…» и «Прости, что…» не станут ложными именами спикеров.
        if candidate_key in stop_words or candidate_key not in KNOWN_FIRST_NAMES:
            continue
        target_speaker = None
        for following in segments[position + 1 : position + 4]:
            if following.speaker and following.speaker != item.speaker:
                target_speaker = following.speaker
                break
        if target_speaker and target_speaker not in replacements:
            normalized = candidate[:1].upper() + candidate[1:]
            votes_for_name = addressed_votes.setdefault(normalized, {})
            votes_for_name[target_speaker] = votes_for_name.get(target_speaker, 0) + 1

    for name, votes in addressed_votes.items():
        target_speaker, vote_count = max(votes.items(), key=lambda item: item[1])
        # Одно обращение может быть неверно привязано из-за перебивания.
        # Имя закрепляем только когда его подтвердили не меньше двух раз.
        if vote_count >= 2 and target_speaker not in replacements:
            replacements[target_speaker] = name

    for item in segments:
        if item.speaker in replacements:
            item.speaker = replacements[item.speaker]
    return replacements


def transcribe(
    source: Path,
    destination: Path,
    model_name: str,
    language: str | None,
    prompt: str,
    vad_filter: bool,
    diarize: bool,
    hf_token: str,
    speaker_names: str,
    auto_names: bool,
    status: queue.Queue[tuple[str, Any]],
    cancel: threading.Event,
) -> Path:
    model, device = create_model(model_name, status)
    if cancel.is_set():
        raise InterruptedError("Операция отменена")

    status.put(("status", f"Расшифровываю на {device}…"))
    started = time.monotonic()
    raw_segments, info = model.transcribe(
        str(source),
        language=language,
        initial_prompt=prompt.strip() or None,
        beam_size=5,
        vad_filter=vad_filter,
        vad_parameters={"min_silence_duration_ms": 700} if vad_filter else None,
        word_timestamps=False,
        condition_on_previous_text=True,
    )
    segments: list[Segment] = []
    for position, item in enumerate(raw_segments, 1):
        if cancel.is_set():
            raise InterruptedError("Операция отменена")
        segment = Segment(float(item.start), float(item.end), item.text.strip())
        if segment.text:
            segments.append(segment)
        status.put(("progress", (position, item.end, info.duration)))

    if not segments:
        raise RuntimeError("Речь не распознана. Проверьте, что в файле есть голос, или отключите фильтр тишины.")

    speaker_mapping: dict[str, str] = {}
    if diarize:
        turns = diarize_audio(source, hf_token, status)
        speaker_mapping = assign_speakers(segments, turns, speaker_names)
        inferred_names = infer_speaker_names(segments) if auto_names and not speaker_names.strip() else {}
        for technical_name, visible_name in list(speaker_mapping.items()):
            if visible_name in inferred_names:
                speaker_mapping[technical_name] = inferred_names[visible_name]
        segments = merge_adjacent_segments(segments)

    status.put(("status", "Сохраняю файлы…"))
    return write_outputs(
        source,
        destination,
        segments,
        {
            "source_file": str(source),
            "language": info.language,
            "language_probability": info.language_probability,
            "model": model_name,
            "device": device,
            "created_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
            "elapsed_seconds": round(time.monotonic() - started, 1),
            "speaker_diarization": diarize,
            "speaker_mapping": speaker_mapping,
        },
    )


class TranscriberApp:
    def __init__(self, root: Tk) -> None:
        self.root = root
        self.root.title("Whisper — расшифровка аудио")
        self.root.geometry("760x720")
        self.root.minsize(640, 620)
        self.root.protocol("WM_DELETE_WINDOW", self.close)
        self.events: queue.Queue[tuple[str, Any]] = queue.Queue()
        self.cancel_event = threading.Event()
        self.worker: threading.Thread | None = None
        self.converter_process: subprocess.Popen[Any] | None = None

        self.file_var = StringVar()
        DEFAULT_OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
        self.folder_var = StringVar(value=str(DEFAULT_OUTPUT_DIR))
        self.model_var = StringVar(value="large-v3-turbo")
        self.language_var = StringVar(value="Русский")
        self.vad_var = BooleanVar(value=True)
        self.diarize_var = BooleanVar(value=True)
        self.status_var = StringVar(value="Выберите аудио- или видеофайл.")
        self._build()
        self.root.after(150, self._poll_events)

    def _build(self) -> None:
        outer = ttk.Frame(self.root, padding=18)
        outer.pack(fill=BOTH, expand=True)
        outer.columnconfigure(1, weight=1)

        ttk.Label(outer, text="Файл", font=("Segoe UI", 10, "bold")).grid(row=0, column=0, sticky="w", pady=(0, 6))
        ttk.Entry(outer, textvariable=self.file_var).grid(row=0, column=1, sticky="ew", padx=(12, 8), pady=(0, 6))
        ttk.Button(outer, text="Выбрать…", command=self.choose_file).grid(row=0, column=2, pady=(0, 6))

        ttk.Label(outer, text="Папка результата").grid(row=1, column=0, sticky="w", pady=6)
        ttk.Entry(outer, textvariable=self.folder_var).grid(row=1, column=1, sticky="ew", padx=(12, 8), pady=6)
        ttk.Button(outer, text="Выбрать…", command=self.choose_folder).grid(row=1, column=2, pady=6)

        options = ttk.Frame(outer)
        options.grid(row=2, column=0, columnspan=3, sticky="ew", pady=(14, 4))
        options.columnconfigure(1, weight=1)
        options.columnconfigure(3, weight=1)
        ttk.Label(options, text="Модель").grid(row=0, column=0, sticky="w")
        ttk.Combobox(options, textvariable=self.model_var, values=MODEL_OPTIONS, state="readonly").grid(row=0, column=1, sticky="ew", padx=(8, 22))
        ttk.Label(options, text="Язык").grid(row=0, column=2, sticky="w")
        ttk.Combobox(options, textvariable=self.language_var, values=tuple(LANGUAGES), state="readonly").grid(row=0, column=3, sticky="ew", padx=(8, 0))
        ttk.Checkbutton(outer, text="Пропускать длинные паузы и фоновый шум (рекомендуется)", variable=self.vad_var).grid(row=3, column=0, columnspan=3, sticky="w", pady=(9, 14))

        speaker_options = ttk.Frame(outer)
        speaker_options.grid(row=4, column=0, columnspan=3, sticky="ew", pady=(0, 12))
        speaker_options.columnconfigure(1, weight=1)
        ttk.Checkbutton(speaker_options, text="Определять спикеров в расшифровке", variable=self.diarize_var).grid(row=0, column=0, columnspan=3, sticky="w")
        ttk.Label(speaker_options, text="Токен Hugging Face").grid(row=1, column=0, sticky="w", pady=(8, 0))
        self.hf_token = ttk.Entry(speaker_options, show="●")
        self.hf_token.grid(row=1, column=1, sticky="ew", padx=(10, 8), pady=(8, 0))
        saved_token = load_saved_token()
        if saved_token:
            self.hf_token.insert(0, saved_token)
        self.hf_token.bind("<Control-v>", self.paste_token)
        self.hf_token.bind("<<Paste>>", self.paste_token)
        ttk.Button(speaker_options, text="Вставить", command=self.paste_token).grid(row=1, column=2, pady=(8, 0))
        ttk.Button(speaker_options, text="Получить токен…", command=self.open_huggingface).grid(row=1, column=3, padx=(8, 0), pady=(8, 0))
        ttk.Label(speaker_options, text="Хранится в защищённом хранилище Windows. Нужен для загрузки модели спикеров.", foreground="#555555").grid(row=2, column=1, columnspan=3, sticky="w")
        ttk.Label(speaker_options, text="Имена по порядку",).grid(row=3, column=0, sticky="w", pady=(8, 0))
        self.speaker_names = ttk.Entry(speaker_options)
        self.speaker_names.grid(row=3, column=1, columnspan=2, sticky="ew", padx=(10, 0), pady=(8, 0))
        self.speaker_names.insert(0, "Например: Антон, Мария")

        ttk.Label(outer, text="Подсказка для терминов, имён и сокращений", font=("Segoe UI", 10, "bold")).grid(row=5, column=0, columnspan=3, sticky="w")
        self.prompt = ttk.Entry(outer)
        self.prompt.insert(0, DEFAULT_PROMPT)
        self.prompt.grid(row=6, column=0, columnspan=3, sticky="ew", pady=(6, 4))
        ttk.Label(outer, text="Например: названия людей, компаний, профессиональные термины через запятую.", foreground="#555555").grid(row=7, column=0, columnspan=3, sticky="w")

        self.progress = ttk.Progressbar(outer, mode="indeterminate")
        self.progress.grid(row=8, column=0, columnspan=3, sticky="ew", pady=(22, 5))
        ttk.Label(outer, textvariable=self.status_var, wraplength=680).grid(row=9, column=0, columnspan=3, sticky="w")

        controls = ttk.Frame(outer)
        controls.grid(row=10, column=0, columnspan=3, sticky="ew", pady=(17, 0))
        self.start_button = ttk.Button(controls, text="Расшифровать", command=self.start)
        self.start_button.pack(side=LEFT)
        self.cancel_button = ttk.Button(controls, text="Отменить", command=self.cancel, state="disabled")
        self.cancel_button.pack(side=LEFT, padx=10)
        ttk.Button(controls, text="Видео → MP3", command=self.open_converter).pack(side=RIGHT, padx=(0, 10))
        ttk.Button(controls, text="Открыть папку", command=self.open_result_folder).pack(side=RIGHT)

        note = "Файлы обрабатываются локально. Первый запуск скачает выбранную модель (около 1,5 ГБ для large-v3-turbo)."
        ttk.Label(outer, text=note, foreground="#555555", wraplength=680).grid(row=11, column=0, columnspan=3, sticky="w", pady=(18, 0))

    def open_huggingface(self) -> None:
        webbrowser.open_new("https://huggingface.co/settings/tokens")

    def paste_token(self, _event: Any = None) -> str:
        """Обходит нестабильную обработку Ctrl+V у Tk в оконном режиме Python."""
        try:
            token = self.root.clipboard_get().strip()
        except Exception:
            messagebox.showinfo("Буфер обмена", "В буфере обмена нет текста токена.")
            return "break"
        if token:
            self.hf_token.insert("insert", token)
        return "break"

    def choose_file(self) -> None:
        filename = filedialog.askopenfilename(title="Выберите запись", filetypes=[("Аудио и видео", MEDIA_TYPES), ("Все файлы", "*.*")])
        if filename:
            self.file_var.set(filename)
            if not self.folder_var.get():
                self.folder_var.set(str(Path(filename).parent))

    def choose_folder(self) -> None:
        folder = filedialog.askdirectory(title="Куда сохранить расшифровку")
        if folder:
            self.folder_var.set(folder)

    def open_result_folder(self) -> None:
        folder = self.folder_var.get().strip()
        if folder and Path(folder).is_dir():
            os.startfile(folder)
        else:
            messagebox.showinfo("Папка результата", "Сначала выберите существующую папку результата.")

    def open_converter(self) -> None:
        """Запускает HTML-конвертер из единого окна программы."""
        converter = APP_DIR / "audio_converter.py"
        if not converter.is_file():
            messagebox.showerror("Конвертер", "Файл конвертера не найден рядом с программой.")
            return
        if self.converter_process and self.converter_process.poll() is None:
            messagebox.showinfo("Конвертер", "Конвертер уже запущен. Его страница открыта в браузере.")
            return
        try:
            self.converter_process = subprocess.Popen(
                [sys.executable, str(converter)],
                cwd=str(APP_DIR),
                env=os.environ.copy(),
            )
        except Exception as error:
            messagebox.showerror("Конвертер", f"Не удалось запустить конвертер:\n{error}")

    def close(self) -> None:
        """Закрывает окно и запущенный рядом локальный конвертер."""
        if self.converter_process and self.converter_process.poll() is None:
            try:
                self.converter_process.terminate()
            except Exception:
                pass
        self.root.destroy()

    def start(self) -> None:
        source = Path(self.file_var.get().strip().strip('"'))
        if not source.is_file():
            messagebox.showerror("Нет файла", "Выберите существующий аудио- или видеофайл.")
            return
        target = Path(self.folder_var.get().strip().strip('"')) if self.folder_var.get().strip() else source.parent
        self.folder_var.set(str(target))
        self.cancel_event.clear()
        self.start_button.config(state="disabled")
        self.cancel_button.config(state="normal")
        self.progress.start(12)
        self.status_var.set("Подготавливаю расшифровку…")
        language = LANGUAGES[self.language_var.get()]
        prompt = self.prompt.get()
        speaker_names = self.speaker_names.get()
        if speaker_names == "Например: Антон, Мария":
            speaker_names = ""
        hf_token = self.hf_token.get().strip()
        if hf_token:
            save_token(hf_token)
        self.worker = threading.Thread(
            target=self._run,
            args=(
                source, target, self.model_var.get(), language, prompt, self.vad_var.get(),
                self.diarize_var.get(), hf_token, speaker_names, True,
            ),
            daemon=True,
        )
        self.worker.start()

    def _run(self, *args: Any) -> None:
        try:
            outputs = transcribe(*args, status=self.events, cancel=self.cancel_event)
            self.events.put(("done", outputs))
        except InterruptedError:
            self.events.put(("cancelled", None))
        except Exception as error:
            self.events.put(("error", (str(error), traceback.format_exc())))

    def cancel(self) -> None:
        self.cancel_event.set()
        self.status_var.set("Отмена: жду завершения текущего фрагмента…")
        self.cancel_button.config(state="disabled")

    def _finish(self) -> None:
        self.progress.stop()
        self.start_button.config(state="normal")
        self.cancel_button.config(state="disabled")
        self.worker = None

    def _poll_events(self) -> None:
        try:
            while True:
                kind, payload = self.events.get_nowait()
                if kind == "status":
                    self.status_var.set(payload)
                elif kind == "progress":
                    number, current, duration = payload
                    duration_text = f" из {duration / 60:.1f} мин" if duration else ""
                    self.status_var.set(f"Готово фрагментов: {number}; обработано {current / 60:.1f} мин{duration_text}.")
                elif kind == "done":
                    self._finish()
                    self.status_var.set(f"Готово. Сохранено: {payload.name}")
                    messagebox.showinfo("Расшифровка готова", "Создан TXT-файл. Папка результата уже выбрана в окне.")
                elif kind == "cancelled":
                    self._finish()
                    self.status_var.set("Расшифровка отменена. Файлы результата не созданы.")
                elif kind == "error":
                    self._finish()
                    message, details = payload
                    self.status_var.set("Ошибка: " + message)
                    messagebox.showerror("Не удалось расшифровать", message + "\n\nЕсли это первая попытка, запустите setup.bat ещё раз.\n\nПодробности: " + details[-1200:])
        except queue.Empty:
            pass
        self.root.after(150, self._poll_events)


def main() -> None:
    root = Tk()
    try:
        ttk.Style().theme_use("vista")
    except Exception:
        pass
    TranscriberApp(root)
    root.mainloop()


if __name__ == "__main__":
    main()
