#!/usr/bin/env python3
"""
randomcutt - extractor de clips random para VJ / TouchDesigner
==============================================================

Saca N clips de duración fija desde un video largo y los exporta en HAP
(u otro códec) para alimentar videocutter / moviefilein en TouchDesigner.

v0.10.0 - versión para macOS (.app) y clips sin los metadatos del original.
Un solo archivo, sin dependencias externas (solo stdlib + ffmpeg/ffprobe).
"""

from __future__ import annotations

import hashlib
import json
import os
import queue
import random
import re
import shutil
import subprocess
import sys
import threading
import time
import tkinter as tk
import unicodedata
import urllib.request
import zipfile
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from tkinter import filedialog, messagebox, ttk

APP_NAME = "randomcutt"
APP_VERSION = "0.10.0"

VIDEO_TYPES = [
    ("Video", "*.mp4 *.mov *.mkv *.avi *.m4v *.webm *.mpg *.mpeg *.ts *.wmv *.flv"),
    ("Todos los archivos", "*.*"),
]

# --------------------------------------------------------------------------
# Paleta / tema
# --------------------------------------------------------------------------

C = {
    "bg":       "#15171c",
    "panel":    "#1d2027",
    "border":   "#30343e",
    "field":    "#11131a",
    "fg":       "#e7e9ee",
    "muted":    "#8b91a0",
    "accent":   "#ff3864",
    "accent_h": "#ff5a7e",
    "accent_d": "#c9284d",
    "ghost":    "#2a2e38",
    "ghost_h":  "#363b47",
    "ok":       "#3ddc97",
    "warn":     "#ffb03a",
    "err":      "#ff6b6b",
}

IS_WIN = os.name == "nt"
IS_MAC = sys.platform == "darwin"
UI_FONT = "Segoe UI" if IS_WIN else "Helvetica"
MONO_FONT = "Consolas" if IS_WIN else "Menlo"

# NVENC es solo de NVIDIA; en Mac el H.264 por hardware es VideoToolbox.
H264_HW = "h264_videotoolbox" if IS_MAC else "h264_nvenc"
CODECS = ["hap", "prores_ks", "libx264", H264_HW, "copy"]
CODEC_EXT = {
    "hap": ".mov",
    "prores_ks": ".mov",
    "libx264": ".mp4",
    "h264_nvenc": ".mp4",
    "h264_videotoolbox": ".mp4",
}
HAP_FORMATS = ["hap", "hap_alpha", "hap_q"]
DISTRIBUCIONES = ["Random puro", "Estratificado", "Lineal"]

MODOS = ["Archivo", "Carpeta"]
SALIDAS = ["Subcarpeta por video", "Todo en una carpeta", "Junto al video de origen"]
NOMBRES = ["Limpio", "Limpio sin espacios", "snake_case", "Original"]

VIDEO_EXTS = {".mp4", ".mov", ".mkv", ".avi", ".m4v", ".webm",
              ".mpg", ".mpeg", ".ts", ".wmv", ".flv"}
# Tamaño mínimo para descartar stubs que no son video (los sidecars AppleDouble
# ._nombre.mkv pesan ~4 KB). Bajo a propósito: un clip corto de verdad puede
# pesar unos pocos cientos de KB y tiene que pasar.
MIN_SOURCE_BYTES = 64 << 10
CLIP_RE = re.compile(r"_clip_\d+", re.IGNORECASE)


# --------------------------------------------------------------------------
# Utilidades de proceso / ffmpeg
# --------------------------------------------------------------------------

def app_dir() -> Path:
    """Carpeta del .exe (congelado) o del .py. Aquí se busca un ffmpeg portable."""
    if getattr(sys, "frozen", False):
        return Path(sys.executable).parent
    return Path(__file__).resolve().parent


def resource_dir() -> Path:
    """Recursos empaquetados: _MEIPASS en onefile, la carpeta del .py si no."""
    return Path(getattr(sys, "_MEIPASS", app_dir()))


def _no_window_kwargs() -> dict:
    """Evita que cada llamada a ffmpeg abra una consola negra en Windows."""
    if not IS_WIN:
        return {}
    si = subprocess.STARTUPINFO()
    si.dwFlags |= subprocess.STARTF_USESHOWWINDOW
    si.wShowWindow = subprocess.SW_HIDE
    return {"startupinfo": si, "creationflags": 0x08000000}  # CREATE_NO_WINDOW


def find_tool(name: str, override_dir: str | None = None) -> str | None:
    """Busca ffmpeg/ffprobe: override -> junto al exe -> PATH -> rutas típicas."""
    exe = name + (".exe" if IS_WIN else "")
    cands: list[Path] = []

    if override_dir:
        d = Path(override_dir)
        cands += [d / exe, d / "bin" / exe]

    base = app_dir()
    cands += [base / exe, base / "bin" / exe, base / "ffmpeg" / "bin" / exe]

    found = shutil.which(name)
    if found:
        cands.append(Path(found))

    if IS_WIN:
        cands += [
            Path(r"C:\Program Files\ffmpeg\bin") / exe,
            Path(r"C:\ffmpeg\bin") / exe,
            Path(r"C:\Program Files (x86)\ffmpeg\bin") / exe,
        ]
        la = os.environ.get("LOCALAPPDATA")
        if la:
            lap = Path(la)
            cands.append(lap / "Microsoft" / "WinGet" / "Links" / exe)
            for pat in (
                f"Microsoft/WinGet/Packages/*FFmpeg*/*/bin/{exe}",
                f"Microsoft/WinGet/Packages/*FFmpeg*/*/*/bin/{exe}",
            ):
                try:
                    cands += sorted(lap.glob(pat), reverse=True)
                except OSError:
                    pass
    elif IS_MAC:
        # Abierta desde el Finder, la app no hereda el PATH de la terminal.
        cands += [Path("/opt/homebrew/bin") / exe, Path("/usr/local/bin") / exe]

    # Último recurso: la copia que descarga la propia app.
    cands.append(managed_ffmpeg_dir() / "bin" / exe)

    for c in cands:
        try:
            if c.is_file():
                return str(c)
        except OSError:
            continue
    return None


def probe(ffprobe: str, path: str) -> dict:
    """Devuelve duración / códec / resolución / fps / audio del archivo."""
    cmd = [
        ffprobe, "-v", "error",
        "-show_entries",
        "stream=index,codec_type,codec_name,width,height,r_frame_rate,pix_fmt,duration"
        ":format=duration",
        "-of", "json", path,
    ]
    res = subprocess.run(cmd, capture_output=True, text=True,
                         errors="replace", stdin=subprocess.DEVNULL,
                         **_no_window_kwargs())
    if res.returncode != 0:
        raise RuntimeError((res.stderr or "ffprobe falló").strip())

    data = json.loads(res.stdout or "{}")
    streams = data.get("streams") or []
    vs = next((s for s in streams if s.get("codec_type") == "video"), None)
    if vs is None:
        raise RuntimeError("El archivo no tiene stream de video.")

    duration = 0.0
    for src in (data.get("format", {}).get("duration"), vs.get("duration")):
        try:
            duration = float(src)
            break
        except (TypeError, ValueError):
            continue
    if duration <= 0:
        raise RuntimeError("No se pudo leer la duración del video.")

    fps = 0.0
    try:
        num, _, den = str(vs.get("r_frame_rate", "0/1")).partition("/")
        fps = float(num) / float(den or 1)
    except (TypeError, ValueError, ZeroDivisionError):
        pass

    return {
        "duration": duration,
        "codec": vs.get("codec_name", "?"),
        "width": int(vs.get("width") or 0),
        "height": int(vs.get("height") or 0),
        "pix_fmt": vs.get("pix_fmt", "?"),
        "fps": fps,
        "has_audio": any(s.get("codec_type") == "audio" for s in streams),
    }


def fmt_dur(seconds: float) -> str:
    seconds = int(round(seconds))
    h, rem = divmod(seconds, 3600)
    m, s = divmod(rem, 60)
    return f"{h}:{m:02d}:{s:02d}" if h else f"{m}:{s:02d}"


# --------------------------------------------------------------------------
# Descarga de ffmpeg (Windows)
# --------------------------------------------------------------------------
# Build "full" de gyan.dev: el "essentials" no trae libsnappy y sin eso ffmpeg
# no tiene encoder HAP. La variante "shared" pesa ~95 MB, la estática ~250 MB.
FFMPEG_RELEASE_API = "https://api.github.com/repos/GyanD/codexffmpeg/releases/latest"
FFMPEG_ASSET_RE = re.compile(r"full_build-shared\.zip$", re.IGNORECASE)
# Respaldo con URL fija (también trae snappy) por si la API de GitHub falla
# o se agotó el límite de consultas sin cuenta.
FFMPEG_FALLBACK = ("ffmpeg-master-latest-win64-gpl-shared.zip",
                   "https://github.com/BtbN/FFmpeg-Builds/releases/download/latest/"
                   "ffmpeg-master-latest-win64-gpl-shared.zip")
HTTP_HEADERS = {"User-Agent": f"{APP_NAME}/{APP_VERSION}"}
# El ffmpeg de Homebrew no trae HAP (se compila sin libsnappy); el del tap
# homebrew-ffmpeg sí. Choca con el de Homebrew: hay que desinstalar ese antes.
BREW_HAP_CMD = "brew install homebrew-ffmpeg/ffmpeg/ffmpeg"


def managed_ffmpeg_dir() -> Path:
    """Dónde deja ffmpeg la descarga: junto al config, siempre escribible y
    sin tocar el PATH ni el resto del sistema."""
    return config_file().parent / "ffmpeg"


def has_hap_encoder(ffmpeg: str) -> bool:
    try:
        res = subprocess.run([ffmpeg, "-hide_banner", "-encoders"],
                             capture_output=True, text=True, errors="replace",
                             stdin=subprocess.DEVNULL, timeout=30,
                             **_no_window_kwargs())
    except (OSError, subprocess.SubprocessError):
        return False
    return bool(re.search(r"^\s*V\S*\s+hap\s", res.stdout, re.MULTILINE))


class DownloadCancelled(Exception):
    pass


class FFmpegDownloader:
    """Descarga e instala ffmpeg + ffprobe en un hilo; reporta por la cola."""

    def __init__(self, ui_queue: queue.Queue):
        self.q = ui_queue
        self.cancel = threading.Event()

    def log(self, msg: str, tag: str = "info"):
        self.q.put(("log", msg, tag))

    def run(self):
        try:
            final = self._run()
            self.q.put(("dl_done", {"ok": True, "dir": str(final)}))
        except DownloadCancelled:
            self.q.put(("dl_done", {"ok": False, "cancelled": True}))
        except Exception as exc:                                  # noqa: BLE001
            self.q.put(("dl_done", {"ok": False, "error": str(exc)}))

    def _run(self) -> Path:
        dest = managed_ffmpeg_dir()
        dest.mkdir(parents=True, exist_ok=True)

        sources = []
        try:
            sources.append(self._latest_release())
        except (OSError, ValueError, KeyError, StopIteration) as exc:
            self.log(f"No pude consultar la última versión en GitHub ({exc}). "
                     "Uso el respaldo.", "warn")
        sources.append((*FFMPEG_FALLBACK, 0, ""))

        errors = []
        for i, (name, url, size, digest) in enumerate(sources):
            last = i == len(sources) - 1
            try:
                return self._install(name, url, size, digest, dest, require_hap=not last)
            except DownloadCancelled:
                raise
            except (OSError, ValueError, RuntimeError, zipfile.BadZipFile) as exc:
                errors.append(f"{name}: {exc}")
                self.log(f"  FALLO {name}: {exc}", "err")
        raise RuntimeError(" | ".join(errors))

    def _latest_release(self) -> tuple[str, str, int, str]:
        req = urllib.request.Request(FFMPEG_RELEASE_API, headers=HTTP_HEADERS)
        with urllib.request.urlopen(req, timeout=20) as r:
            data = json.loads(r.read().decode("utf-8"))
        asset = next(a for a in data["assets"] if FFMPEG_ASSET_RE.search(a["name"]))
        return (asset["name"], asset["browser_download_url"],
                int(asset.get("size") or 0), asset.get("digest") or "")

    def _install(self, name: str, url: str, size: int, digest: str, dest: Path,
                 require_hap: bool) -> Path:
        part = dest / "descarga.zip.part"
        tmp = dest / "bin.new"
        try:
            self.log(f"Descargando {name} ...")
            sha = self._download(url, size, part)
            if digest.startswith("sha256:") and sha != digest[7:].lower():
                raise RuntimeError("el checksum no coincide: descarga corrupta")
            if digest.startswith("sha256:"):
                self.log("  checksum sha256 verificado", "muted")

            # Solo bin\: ffmpeg, ffprobe y sus DLL. ffplay no hace falta.
            self.log("  descomprimiendo ...", "muted")
            shutil.rmtree(tmp, ignore_errors=True)
            tmp.mkdir()
            with zipfile.ZipFile(part) as z:
                for m in z.infolist():
                    parts = m.filename.replace("\\", "/").split("/")
                    fname = parts[-1]
                    if m.is_dir() or "bin" not in parts[:-1] or not fname:
                        continue
                    if fname.lower().startswith("ffplay"):
                        continue
                    if self.cancel.is_set():
                        raise DownloadCancelled
                    # Solo el nombre del archivo: una ruta del zip nunca
                    # puede escribir fuera de la carpeta (zip slip).
                    with z.open(m) as src, open(tmp / fname, "wb") as out:
                        shutil.copyfileobj(src, out)

            exe = ".exe" if IS_WIN else ""
            ffm, ffp = tmp / f"ffmpeg{exe}", tmp / f"ffprobe{exe}"
            if not (ffm.is_file() and ffp.is_file()):
                raise RuntimeError("el zip no trae ffmpeg y ffprobe")
            res = subprocess.run([str(ffp), "-hide_banner", "-version"],
                                 capture_output=True, stdin=subprocess.DEVNULL,
                                 timeout=30, **_no_window_kwargs())
            if res.returncode != 0:
                raise RuntimeError("el ffprobe descargado no arranca")
            hap = has_hap_encoder(str(ffm))
            if not hap and require_hap:
                raise RuntimeError("este build no trae el encoder HAP")

            # Reemplazo casi atómico: la copia anterior se borra al final.
            final, old = dest / "bin", dest / "bin.old"
            shutil.rmtree(old, ignore_errors=True)
            if final.exists():
                final.rename(old)
            tmp.rename(final)
            shutil.rmtree(old, ignore_errors=True)
            if not hap:
                self.log("  AVISO: este ffmpeg no trae HAP; usa otro códec o "
                         "instala el build full de gyan.dev.", "warn")
            return final
        finally:
            try:
                part.unlink(missing_ok=True)
            except OSError:
                pass
            shutil.rmtree(tmp, ignore_errors=True)       # ya renombrado si salió bien

    def _download(self, url: str, size: int, out: Path) -> str:
        req = urllib.request.Request(url, headers=HTTP_HEADERS)
        sha = hashlib.sha256()
        with urllib.request.urlopen(req, timeout=30) as r, open(out, "wb") as f:
            total = int(r.headers.get("Content-Length") or size or 0)
            done, last = 0, 0.0
            while True:
                if self.cancel.is_set():
                    raise DownloadCancelled
                chunk = r.read(1 << 16)
                if not chunk:
                    break
                f.write(chunk)
                sha.update(chunk)
                done += len(chunk)
                now = time.time()
                if now - last > 0.15:
                    self.q.put(("dl_progress", {"done": done, "total": total}))
                    last = now
        self.q.put(("dl_progress", {"done": done, "total": total}))
        if total and done != total:
            raise RuntimeError(f"descarga incompleta ({done} de {total} bytes)")
        return sha.hexdigest()


# --------------------------------------------------------------------------
# Lógica de corte
# --------------------------------------------------------------------------

def compute_starts(total: float, clip_dur: float, n: int,
                   mode: str, spread: float, rng: random.Random) -> list[float]:
    """Puntos de inicio de cada clip según la distribución elegida."""
    span = max(0.0, total - clip_dur)
    if span <= 0:
        return [0.0] * n

    if mode == "Lineal":
        step = span / max(n - 1, 1)
        return [min(i * step, span) for i in range(n)]

    if mode == "Estratificado":
        bucket = span / n
        out = []
        for i in range(n):
            centre = bucket * i + bucket / 2.0
            jitter = rng.uniform(-bucket / 2.0, bucket / 2.0) * spread
            out.append(min(max(centre + jitter, 0.0), span))
        return out

    # Random puro (comportamiento histórico de v006)
    return [rng.uniform(0.0, span) for _ in range(n)]


def collect_sources(folder: str, recursive: bool, skip_clips: bool) -> list[Path]:
    """Videos usables dentro de una carpeta, ordenados por nombre."""
    root = Path(folder)
    try:
        it = root.rglob("*") if recursive else root.glob("*")
    except OSError:
        return []
    out: list[Path] = []
    for p in it:
        try:
            if p.suffix.lower() not in VIDEO_EXTS or not p.is_file():
                continue
            if p.name.startswith("._"):                 # sidecar AppleDouble
                continue
            if p.stat().st_size < MIN_SOURCE_BYTES:
                continue
            if skip_clips and CLIP_RE.search(p.stem):   # ya es un clip generado
                continue
        except OSError:
            continue
        out.append(p)
    return sorted(out, key=lambda q: str(q).lower())


# --------------------------------------------------------------------------
# Nombres limpios
# --------------------------------------------------------------------------
# Los nombres de release siguen una convención de facto (scene, P2P, fansubs):
#   Titulo.Del.Video.1998.1080p.BluRay.x264.AAC5.1-GRUPO
#   [Fansub] Titulo - 05 [1080p][ABCD1234]
#   Serie.S02E03.Titulo.Del.Episodio.720p.WEB-DL
# El título va primero; desde el año, la primera etiqueta técnica o el
# marcador de episodio, todo lo que sigue es metadata. No hay listas de
# películas: solo vocabulario técnico, así que sirve para cualquier nombre.

# Etiquetas técnicas que nunca forman parte de un título. Desde la primera
# que aparece (salvo en la primera palabra), el resto se descarta. Quedan
# fuera palabras que sí aparecen en títulos ("web": Charlotte's Web).
_JUNK_WORDS = frozenset("""
    bluray blu-ray bdrip brrip bdremux remux bd bd25 bd50 bdmv hddvd
    dvdrip dvd dvdr dvd-r dvd5 dvd9 dvdscr dvdscreener ntsc pal
    webrip web-dl webdl webcap hdtv pdtv sdtv hdrip tvrip satrip dsr dsrip
    vhsrip vhs ldrip laserdisc hdcam camrip telesync telecine ppv
    amzn nf dsnp hmax atvp hulu itunes
    x264 x265 h264 h265 hevc avc xvid divx av1 vp9 mpeg2 mpeg4
    hi10 hi10p hdr hdr10 hdr10+ dovi sdr uhd fhd qhd 4k 8k
    aac ac3 eac3 e-ac3 ddp dts dts-hd dtshd dts-x dtsx truehd atmos flac mp3
    opus lpcm pcm
    yts yify rarbg ettv eztv
""".split())

_JUNK_RE = re.compile(r"""(?x)^(?:
      \d{3,4}[pi]                                     # 1080p, 576i
    | \d{3,4}x\d{3,4}p?                               # 1920x1080
    | (?:aac|ac3|eac3|ddp?|dts|flac|truehd|opus|lpcm|mp3)[\d.x+]+   # AAC5.1, FLACx2
    | \d\.\d(?:x\d)?                                  # 5.1, 2.0
    | \d{1,2}ch                                       # 6ch
    | \d{1,2}bits?                                    # 10bit
    | [hx]\.?26[45]                                   # h.264, x265
)$""")

# Ediciones, idiomas y tags de release: son palabras comunes, así que solo se
# quitan cuando quedan al final del título ("Uncut Gems" se salva).
_TAIL_WORDS = frozenset("""
    remastered remaster restored restoration repack proper rerip
    extended unrated uncut uncensored theatrical imax hybrid upscale upscaled
    dubbed subbed dual audio multi multisub subs sub subtitled subtitulado
    vose vostfr eng esp spa jap jpn ita fre fra ger lat latino castellano
    english spanish japanese french german italian
    bfi criterion limited internal readnfo dc
""".split())
_CUT_PREV = frozenset({"directors", "extended", "theatrical", "uncut"})
_EDITION_PREV = frozenset({"special", "anniversary", "collectors", "limited",
                           "ultimate", "deluxe", "definitive", "criterion",
                           "extended", "remastered", "restored"})

# Episodios compactos: cortan el título (lo que sigue es el nombre del
# episodio) y se conservan al final: S02E03, S01, 1x05, E05, EP05.
_EP_RE = re.compile(r"^(?:s\d{1,2}(?:e\d{1,3}){0,2}|\d{1,2}x\d{2,3}|ep?\d{1,3}(?:v\d)?)$",
                    re.IGNORECASE)
# Partes/volúmenes/discos: no cortan (pueden ser parte del título, como en
# "Kill Bill Vol 1"), pero si aparecen después del corte se recuperan, para
# que "Parte 1" y "Parte 2" no terminen con el mismo nombre.
_PART_WORDS = frozenset({"part", "pt", "parte", "cd", "disc", "disk", "disco",
                         "vol", "volume", "volumen", "chapter", "capitulo",
                         "cap", "episode", "episodio", "ep"})
_PART_NUM_RE = re.compile(r"^(?:\d{1,3}|[ivx]{1,5})$", re.IGNORECASE)
_PART_JOINED_RE = re.compile(r"^(cd|disc|disk|pt|part|vol)(\d{1,2})$", re.IGNORECASE)

_ANIME_EP_RE = re.compile(r"^\d{1,3}(?:v\d)?$")        # "Titulo - 05" / "- 05v2"
_MAX_YEAR = time.localtime().tm_year + 1                # 2049 no es un año de estreno
_DOT = ""                                         # punto protegido (5.1, L.A.)
_WIN_RESERVED = frozenset({"con", "prn", "aux", "nul",
                           *(f"com{i}" for i in range(1, 10)),
                           *(f"lpt{i}" for i in range(1, 10))})
MAX_NAME_LEN = 80


def _key(tok: str) -> str:
    """Forma normalizada para comparar: minúsculas, sin apóstrofes ni puntuación."""
    return re.sub(r"['’`]", "", tok.lower()).strip(".,;:!?")


def _is_year(tok: str) -> bool:
    return len(tok) == 4 and tok.isdigit() and 1888 <= int(tok) <= _MAX_YEAR


def _is_junk(tok: str) -> bool:
    k = _key(tok)
    if k in _JUNK_WORDS or _JUNK_RE.match(k):
        return True
    head = re.split(r"[-+]", k, maxsplit=1)[0]          # x264-GRUPO, AAC-GRUPO
    return head != k and (head in _JUNK_WORDS or bool(_JUNK_RE.match(head)))


def _is_tail(tok: str) -> bool:
    return any(p in _TAIL_WORDS for p in re.split(r"[+/]", _key(tok)) if p)


def _fmt_ep(tok: str) -> str:
    return tok.upper() if tok[:1].isalpha() else tok.lower()    # S02E03, 1x05


def _part_label(key: str) -> str:
    return "CD" if key == "cd" else key.capitalize()


def _split_chunk(chunk: str, group: int, out: list):
    """Parte un tramo de texto en tokens; los guiones sueltos quedan como '-'."""
    for raw in re.sub(r"[()]", " ", chunk).split():
        raw = raw.replace(_DOT, ".")
        core = raw.strip("-")
        if raw.startswith("-"):
            out.append(("-", group))
        if core:
            out.append((core, group))
        if raw.endswith("-") and core:
            out.append(("-", group))


def _tokenize(stem: str, drop_brackets: bool) -> list[tuple[str, int]]:
    """Tokens del nombre como (texto, grupo). grupo > 0 = dentro de (...)."""
    # NFKC: unifica acentos descompuestos (macOS) y paréntesis de ancho completo.
    s = unicodedata.normalize("NFKC", stem)
    s = re.sub(r"(?i)\bwww\.[^\s\[\](){}]+", " ", s)
    s = re.sub(r"(?i)^\s*[\w-]+\.(?:com|net|org|to|tv|mx|am|ag|lt|se|cc|io|me|in|info)"
               r"\s+-\s+", " ", s)                      # "sitio.com - Titulo"
    if drop_brackets:
        s = re.sub(r"\[[^\]]*\]|\{[^}]*\}|【[^】]*】|〔[^〕]*〕", " ", s)
    s = re.sub(r"[\[\]{}【】〔〕]", " ", s)
    s = re.sub(r"(?i)(?<![a-z0-9])([hx])\.(26[45])(?!\d)", r"\1\2", s)   # H.264
    s = re.sub(r"(?<!\d)(\d)\.(\d)(?!\d)", rf"\1{_DOT}\2", s)   # 5.1 queda entero
    s = re.sub(r"(?<![^\W\d_])((?:[^\W\d_]\.){2,})",             # L.A. / S.W.A.T.
               lambda m: m.group(1).replace(".", _DOT) + " ", s)
    s = re.sub(r"[._~]", " ", s)
    s = re.sub(r"\s*[–—]\s*", " - ", s)

    toks: list[tuple[str, int]] = []
    pos = group = 0
    for m in re.finditer(r"\(([^()]*)\)", s):
        _split_chunk(s[pos:m.start()], 0, toks)
        group += 1
        _split_chunk(m.group(1), group, toks)
        pos = m.end()
    _split_chunk(s[pos:], 0, toks)
    return toks


def _parse(stem: str, drop_brackets: bool = True) -> tuple[list[str], str, str]:
    """Separa (palabras del título, marcador de episodio/parte, año)."""
    toks = _tokenize(stem, drop_brackets)
    words = [t for t, _ in toks]
    n = len(words)
    first = next((i for i, t in enumerate(words) if t != "-"), n)

    # 1. Primera etiqueta técnica o episodio compacto: ahí termina el título.
    stop, ep_at = n, None
    for i in range(first + 1, n):
        t = words[i]
        if t == "-":
            if i + 1 < n and _ANIME_EP_RE.match(words[i + 1]):
                stop, ep_at = i, i + 1
                break
            continue
        if _EP_RE.match(t):
            stop, ep_at = i, i
            break
        if _is_junk(t):
            stop = i
            break

    # 2. Un paréntesis con metadata adentro también corta: "(1998)", "(Dual Audio)".
    for i in range(first + 1, stop):
        g = toks[i][1]
        if g and (i == 0 or toks[i - 1][1] != g):
            inner = [t for t, gg in toks if gg == g]
            if any(_is_year(t) or _is_junk(t) or _is_tail(t) or _EP_RE.match(t)
                   for t in inner):
                stop, ep_at = i, None
                break

    # 3. El año es el último candidato antes del corte: "Wonder Woman 1984 2020".
    year, cut = "", stop
    for i in range(first + 1, stop):
        if _is_year(words[i]):
            year, cut = words[i], i
    if not year:
        year = next((t for t in words[stop:] if _is_year(t)), "")

    # 4. Marcador: episodio compacto, o parte/volumen que quedó después del corte.
    marker = _fmt_ep(words[ep_at]) if ep_at is not None else ""
    if not marker:
        for i in range(cut, n):
            k = _key(words[i])
            m = _PART_JOINED_RE.match(k)
            if m:
                marker = f"{_part_label(m.group(1))} {m.group(2)}"
                break
            if k in _PART_WORDS and i + 1 < n and _PART_NUM_RE.match(words[i + 1]):
                marker = f"{_part_label(k)} {words[i + 1].upper()}"
                break
            if _EP_RE.match(words[i]):
                marker = _fmt_ep(words[i])
                break

    # 5. Quitar ediciones y tags sueltos que quedaron al final del título.
    title = words[first:cut]
    while title:
        k = _key(title[-1])
        if title[-1] == "-" or _is_tail(title[-1]):
            title.pop()
        elif k == "cut" and len(title) > 1 and _key(title[-2]) in _CUT_PREV:
            del title[-2:]
        elif k == "edition":
            title.pop()
            while title and (_key(title[-1]) in _EDITION_PREV
                             or re.fullmatch(r"\d+(?:st|nd|rd|th)", _key(title[-1]))):
                title.pop()
        elif k == "collection" and len(title) > 1 and _key(title[-2]) == "criterion":
            del title[-2:]
        else:
            break
    return title, marker, year


def _cap(word: str) -> str:
    """Mayúscula inicial también después de guion: spider-man -> Spider-Man."""
    if re.fullmatch(r"(?:[^\W\d_]\.)+", word):
        return word.upper()                             # l.a. -> L.A.
    return re.sub(r"(^|-)([^\W\d_])", lambda m: m.group(1) + m.group(2).upper(), word)


def _render(words: list[str], marker: str, year: str,
            style: str, keep_year: bool) -> str:
    # Solo se tocan mayúsculas si el nombre venía entero en minúsculas: un
    # "MADOX" o "NorthStar" es intencional y se respeta.
    letters = [c for w in words for c in w if c.isalpha()]
    if letters and not any(c.isupper() for c in letters):
        words = [_cap(w) for w in words]
    parts = words + ([marker] if marker else [])

    if style == "Limpio":
        s = re.sub(r"(?:\s+-)+\s+", " - ", " ".join(parts))
        return s + (f" ({year})" if keep_year and year else "")

    flat = [x for p in parts
            for x in re.split(r"\W+", re.sub(r"['’`.]", "", p)) if x]
    if keep_year and year:
        flat.append(year)
    if style == "snake_case":
        return "_".join(x.lower() for x in flat)
    return "".join(x[:1].upper() + x[1:] for x in flat)


def sanitize_name(name: str) -> str:
    """Deja el nombre válido como archivo y carpeta en Windows, macOS y Linux."""
    s = unicodedata.normalize("NFC", name or "")
    s = re.sub(r'[<>:"/\\|?*\x00-\x1f]', " ", s)
    s = re.sub(r"\s+", " ", s).strip(" .-_")
    if len(s) > MAX_NAME_LEN:                           # la ruta de Windows tiene tope
        s = s[:MAX_NAME_LEN]
        sp = s.rfind(" ")
        s = (s[:sp] if sp > MAX_NAME_LEN // 2 else s).rstrip(" .-_")
    if s.split(".")[0].lower() in _WIN_RESERVED:        # CON, NUL, COM1...
        s += "_"
    return s


def clip_base_name(stem: str, style: str, keep_year: bool = False) -> str:
    """Nombre base de los clips a partir del nombre del video de origen."""
    if style not in NOMBRES or style == "Original":
        return stem
    # Primero sin corchetes (grupos, hashes, sitios). Si el título queda vacío
    # o es solo un año ("[REC] (2007)"), se reintenta conservándolos.
    for drop in (True, False):
        words, marker, year = _parse(stem, drop)
        if words and not (len(words) == 1 and (_is_year(words[0]) or _is_junk(words[0]))):
            break
    name = sanitize_name(_render(words, marker, year, style, keep_year)) if words else ""
    return name or sanitize_name(stem) or "video"


def style_manual(name: str, style: str) -> str:
    """Un nombre corregido a mano fija el título; el estilo le da formato
    ("Angel Cop" -> angel_cop / AngelCop). Limpio y Original lo dejan igual."""
    if style in ("Limpio sin espacios", "snake_case"):
        return sanitize_name(_render(name.split(), "", "", style, False)) or name
    return name


def resolve_names(sources: list[Path], style: str, keep_year: bool,
                  overrides: dict) -> list[tuple[str, str]]:
    """Nombre final de cada video del lote: (nombre, nota).

    Un nombre puesto a mano gana sobre el automático. Si dos videos terminan
    con el mismo nombre, el segundo recibe "(2)": si no, se pisarían en la
    misma subcarpeta y 'Omitir ya procesados' daría el segundo por hecho.
    """
    out, seen = [], set()
    for src in sources:
        manual = sanitize_name(overrides.get(src.name, ""))
        base = (style_manual(manual, style) if manual
                else clip_base_name(src.stem, style, keep_year))
        name, k = base, 2
        while name.lower() in seen:
            name = f"{base} ({k})" if style == "Limpio" else f"{base}_{k}"
            k += 1
        seen.add(name.lower())
        out.append((name, "manual" if manual else ("repetido" if name != base else "")))
    return out


def norm_suffix(raw: str) -> str:
    sfx = (raw or "").strip()
    return "_" + sfx if sfx and not sfx.startswith(("_", "-", ".")) else sfx


def output_dir_for(src: Path, name: str, cfg: dict) -> Path:
    """Dónde caen los clips de este video, según cómo se quiera organizar."""
    modo = cfg["salida_modo"]
    if modo == "Junto al video de origen":
        return src.parent
    base = Path(cfg["outdir"])
    return base / name if modo == "Subcarpeta por video" else base


def already_done(outdir: Path, stem: str) -> int:
    """Cuántos clips de este video ya existen en su carpeta de salida."""
    prefix = f"{stem.lower()}_clip_"
    try:
        return sum(1 for p in outdir.iterdir()
                   if p.is_file() and p.stem.lower().startswith(prefix))
    except OSError:
        return 0


def unique_path(path: Path, reserved: set) -> Path:
    """Evita pisar archivos existentes y nombres ya reservados en este lote."""
    base, ext = path.with_suffix(""), path.suffix
    out, i = path, 1
    while out.exists() or str(out).lower() in reserved:
        out = Path(f"{base}_{i}{ext}")
        i += 1
    reserved.add(str(out).lower())
    return out


def build_cmd(ffmpeg: str, src: str, start: float, dur: float, out: Path,
              cfg: dict, info: dict) -> list[str]:
    codec = cfg["codec"]
    cmd = [
        ffmpeg, "-hide_banner", "-nostdin", "-loglevel", "error", "-y",
        "-ss", f"{start:.3f}",
        "-i", src,
        "-t", f"{dur:g}",
        "-map", "0:v:0",
        # Sin esto el clip hereda el título del original (VLC lo muestra en vez
        # del nombre del archivo) y sus capítulos, como una pista de texto extra.
        "-map_metadata", "-1", "-map_chapters", "-1",
    ]
    want_audio = cfg["audio"] and info["has_audio"]
    if want_audio:
        cmd += ["-map", "0:a:0?"]

    # HAP exige ancho y alto múltiplos de 4 o el encoder aborta.
    if codec == "hap" and (info["width"] % 4 or info["height"] % 4):
        if cfg["align"] == "crop":
            cmd += ["-vf", "crop=trunc(iw/4)*4:trunc(ih/4)*4"]
        else:
            cmd += ["-vf", "pad=ceil(iw/4)*4:ceil(ih/4)*4:0:0"]

    if codec == "hap":
        cmd += ["-c:v", "hap", "-format", cfg["hap_format"],
                "-chunks", str(cfg["hap_chunks"])]
    elif codec == "prores_ks":
        cmd += ["-c:v", "prores_ks", "-profile:v", "3", "-qscale:v", "10"]
    elif codec == "libx264":
        cmd += ["-c:v", "libx264", "-preset", "medium", "-crf", "18",
                "-pix_fmt", "yuv420p"]
    elif codec == "h264_nvenc":
        cmd += ["-c:v", "h264_nvenc", "-preset", "p5", "-rc", "vbr",
                "-cq", "19", "-pix_fmt", "yuv420p"]
    elif codec == "h264_videotoolbox":
        # Sin CRF, y su modo de calidad (-q:v) no existe en Macs Intel: bitrate
        # según resolución, ~12 Mbps en 1080p24.
        rate = max(2_000_000, int(info["width"] * info["height"]
                                  * (info["fps"] or 30) * 0.25))
        cmd += ["-c:v", "h264_videotoolbox", "-b:v", str(rate), "-pix_fmt", "yuv420p"]
    elif codec == "copy":
        cmd += ["-c:v", "copy"]

    if want_audio:
        cmd += ["-c:a", "copy"] if codec == "copy" else ["-c:a", "aac", "-b:a", "192k"]
    else:
        cmd += ["-an"]

    cmd.append(str(out))
    return cmd


class Renderer:
    """Corre el lote de ffmpeg en hilos y reporta a la GUI por una cola."""

    def __init__(self, ui_queue: queue.Queue):
        self.q = ui_queue
        self.cancel = threading.Event()
        self._procs: set = set()
        self._lock = threading.Lock()

    # -- mensajes a la GUI ------------------------------------------------
    def log(self, msg: str, tag: str = "info"):
        self.q.put(("log", msg, tag))

    def emit(self, kind: str, payload=None):
        self.q.put((kind, payload))

    # -- control ----------------------------------------------------------
    def abort(self):
        self.cancel.set()
        with self._lock:
            procs = list(self._procs)
        for p in procs:
            try:
                p.terminate()
            except OSError:
                pass

    # -- trabajo ----------------------------------------------------------
    def run(self, cfg: dict):
        try:
            self._run(cfg)
        except Exception as exc:                                  # noqa: BLE001
            self.log(f"ERROR: {exc}", "err")
            self.emit("done", {"ok": 0, "fail": 0, "skipped": 0, "aborted": True,
                               "outdir": cfg.get("outdir", "")})

    def _run(self, cfg: dict):
        t0 = time.time()

        if cfg["batch"]:
            sources = collect_sources(cfg["input"], cfg["recursive"],
                                      cfg["skip_clips"])
            if not sources:
                self.log("No encontré videos usables en esa carpeta.", "err")
                self.emit("done", {"ok": 0, "fail": 0, "skipped": 0,
                                   "aborted": False, "outdir": cfg["outdir"]})
                return
            self.log(f"{len(sources)} videos en {cfg['input']}")
        else:
            sources = [Path(cfg["input"])]

        if cfg["codec"] == "copy":
            self.log("AVISO: con 'copy' el corte salta al keyframe anterior; "
                     "la duración real puede variar.", "warn")

        if cfg["batch"]:
            names = resolve_names(sources, cfg["nombres"], cfg["keep_year"],
                                  cfg["overrides"])
        else:
            names = [(cfg["name"], "")]
        prev = self._previous_names(sources, names, cfg)

        n = cfg["clips"]
        state = {"done": 0, "total": len(sources) * n, "t0": t0}
        self._emit_progress(state)

        tot = {"ok": 0, "fail": 0, "skipped": 0}
        for i, (src, (name, note)) in enumerate(zip(sources, names)):
            if self.cancel.is_set():
                break
            if len(sources) > 1:
                self.log("")
                self.log(f"[{i + 1}/{len(sources)}] {src.name}")
            else:
                self.log(f"Analizando {src.name} ...")
            if name != src.stem:
                self.log(f"  nombre: {name}" + (f"  ({note})" if note else ""),
                         "warn" if note == "repetido" else "muted")
            res = self._run_source(src, name, prev[i], cfg, state)
            for k in tot:
                tot[k] += res[k]

        aborted = self.cancel.is_set()
        bits = [f"{tot['ok']} clips en {time.time() - t0:.1f}s"]
        if tot["fail"]:
            bits.append(f"{tot['fail']} con error")
        if tot["skipped"]:
            bits.append(f"{tot['skipped']} videos omitidos")
        self.log("")
        self.log(("Cancelado: " if aborted else "Listo: ") + ", ".join(bits),
                 "err" if tot["fail"] else "ok")
        self.emit("done", {**tot, "aborted": aborted, "outdir": cfg["outdir"]})

    def _emit_progress(self, state: dict):
        done, total = state["done"], state["total"]
        elapsed = time.time() - state["t0"]
        eta = (elapsed / done) * (total - done) if done else None
        self.emit("progress", {"done": done, "total": total, "eta": eta})

    def _skip_rest(self, state: dict, n: int, key: str) -> dict:
        """Este video no se procesa: avanzar la barra igual y contar aparte."""
        state["done"] += n
        self._emit_progress(state)
        return {"ok": 0, "fail": 0, "skipped": 0, key: 1}

    @staticmethod
    def _previous_names(sources: list[Path], names: list, cfg: dict) -> list[list[str]]:
        """Por video, los nombres con que pudo exportarse antes: el actual, el
        de cualquier otro estilo (con su misma resolución de repetidos) y el
        nombre de archivo tal cual (v0.8 y anteriores). Así 'Omitir ya
        procesados' sigue funcionando al cambiar de estilo. Se descartan los
        que en este lote son el nombre actual de otro video."""
        if not (cfg["batch"] and cfg["skip_done"]):
            return [[nm] for nm, _ in names]
        alts = [resolve_names(sources, st, ky, cfg["overrides"])
                for st in NOMBRES for ky in (False, True)]
        taken = {nm.lower() for nm, _ in names}
        out = []
        for i, src in enumerate(sources):
            own = names[i][0]
            cands = [own] + [a[i][0] for a in alts] + [src.stem]
            keep: list[str] = []
            for c in cands:
                if c not in keep and (c.lower() == own.lower()
                                      or c.lower() not in taken):
                    keep.append(c)
            out.append(keep)
        return out

    def _run_source(self, src: Path, name: str, prev: list, cfg: dict,
                    state: dict) -> dict:
        n, dur = cfg["clips"], cfg["duration"]
        outdir = output_dir_for(src, name, cfg)

        if cfg["batch"] and cfg["skip_done"]:
            for old in prev:
                old_dir = output_dir_for(src, old, cfg)
                have = already_done(old_dir, old)
                if have:
                    extra = ("" if old == name else
                             "  (nombre anterior)" if old_dir.name == old else
                             f" como '{old}'")
                    self.log(f"  omitido: {old_dir.name} ya tiene {have} clips{extra}",
                             "muted")
                    return self._skip_rest(state, n, "skipped")

        try:
            info = probe(cfg["ffprobe"], str(src))
        except (RuntimeError, ValueError, OSError) as exc:
            self.log(f"  FALLO al leer: {exc}", "err")
            return self._skip_rest(state, n, "fail")

        self.log(
            "  {codec}  {w}x{h}  {fps:.3f} fps  {d}  audio: {a}".format(
                codec=info["codec"], w=info["width"], h=info["height"],
                fps=info["fps"], d=fmt_dur(info["duration"]),
                a="sí" if info["has_audio"] else "no"),
            "muted")

        if info["duration"] <= dur:
            self.log(f"  AVISO: el video dura {info['duration']:.2f}s y pediste "
                     f"clips de {dur:g}s. Todos empiezan en 0.", "warn")
        if cfg["codec"] == "hap" and (info["width"] % 4 or info["height"] % 4):
            verb = "recortando" if cfg["align"] == "crop" else "rellenando"
            self.log(f"  AVISO: {info['width']}x{info['height']} no es múltiplo "
                     f"de 4 (HAP lo exige) -> {verb}.", "warn")

        try:
            outdir.mkdir(parents=True, exist_ok=True)
        except OSError as exc:
            self.log(f"  FALLO: no pude crear {outdir}: {exc}", "err")
            return self._skip_rest(state, n, "fail")

        # Seed derivado del nombre: el lote entero es reproducible, pero cada
        # video recibe su propia tirada.
        rng = (random.Random(f"{cfg['seed']}:{src.name}")
               if cfg["seed"] is not None else random.Random())
        starts = compute_starts(info["duration"], dur, n,
                                cfg["distribucion"], cfg["spread"], rng)

        # Nombres reservados de una sola vez: los workers corren en paralelo.
        ext = src.suffix if cfg["codec"] == "copy" else CODEC_EXT[cfg["codec"]]
        sfx = cfg["suffix"]
        reserved: set = set()
        paths = [unique_path(outdir / f"{name}_clip_{i + 1}{sfx}{ext}", reserved)
                 for i in range(n)]

        ok = fail = 0
        with ThreadPoolExecutor(max_workers=cfg["workers"]) as pool:
            futures = [pool.submit(self._one, src, i, starts[i], paths[i], cfg, info)
                       for i in range(n)]
            for fut in futures:
                status, idx, detail = fut.result()
                if status == "ok":
                    ok += 1
                    self.log(f"  [{idx + 1}/{n}] {Path(detail).name}"
                             f"   @ {fmt_dur(starts[idx])}", "ok")
                elif status == "err":
                    fail += 1
                    self.log(f"  [{idx + 1}/{n}] FALLO: {detail}", "err")
                state["done"] += 1
                self._emit_progress(state)

        return {"ok": ok, "fail": fail, "skipped": 0}

    def _one(self, src: Path, idx: int, start: float, out: Path,
             cfg: dict, info: dict):
        if self.cancel.is_set():
            return ("skip", idx, "")
        cmd = build_cmd(cfg["ffmpeg"], str(src), start, cfg["duration"],
                        out, cfg, info)
        try:
            # stdin=DEVNULL es obligatorio: congelado con --windowed el proceso
            # no tiene handles estándar válidos que heredar.
            p = subprocess.Popen(cmd, stdin=subprocess.DEVNULL,
                                 stdout=subprocess.DEVNULL,
                                 stderr=subprocess.PIPE, text=True,
                                 errors="replace", **_no_window_kwargs())
        except OSError as exc:
            return ("err", idx, str(exc))

        with self._lock:
            self._procs.add(p)
        try:
            _, err = p.communicate()
        finally:
            with self._lock:
                self._procs.discard(p)

        if self.cancel.is_set():
            try:
                out.unlink(missing_ok=True)      # no dejar clips a medio escribir
            except OSError:
                pass
            return ("skip", idx, "")
        if p.returncode != 0:
            msg = " | ".join(l.strip() for l in (err or "").splitlines() if l.strip())
            return ("err", idx, (msg or f"ffmpeg salió con código {p.returncode}")[-500:])
        return ("ok", idx, str(out))


# --------------------------------------------------------------------------
# Config persistente
# --------------------------------------------------------------------------

def config_file() -> Path:
    if IS_MAC:
        return Path.home() / "Library" / "Application Support" / APP_NAME / "config.json"
    base = os.environ.get("LOCALAPPDATA") or os.environ.get("XDG_CONFIG_HOME")
    root = Path(base) if base else Path.home() / ".config"
    return root / APP_NAME / "config.json"


def load_config() -> dict:
    # utf-8-sig: tolera un BOM si el json fue editado a mano desde Windows.
    try:
        data = json.loads(config_file().read_text(encoding="utf-8-sig"))
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def save_config(data: dict):
    try:
        path = config_file()
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(data, indent=2), encoding="utf-8")
    except OSError:
        pass


# --------------------------------------------------------------------------
# Widgets con estilo
# --------------------------------------------------------------------------

def card(parent, title: str):
    """Panel con borde de 1px, barra de acento y título. Devuelve (outer, body)."""
    outer = tk.Frame(parent, bg=C["border"])
    inner = tk.Frame(outer, bg=C["panel"])
    inner.pack(fill="both", expand=True, padx=1, pady=1)

    head = tk.Frame(inner, bg=C["panel"])
    head.pack(fill="x", padx=14, pady=(10, 0))
    bar = tk.Frame(head, bg=C["accent"], width=3, height=13)
    bar.pack(side="left", padx=(0, 9))
    bar.pack_propagate(False)
    tk.Label(head, text=title, bg=C["panel"], fg=C["fg"],
             font=(UI_FONT, 10, "bold")).pack(side="left")

    body = tk.Frame(inner, bg=C["panel"])
    body.pack(fill="both", expand=True, padx=14, pady=(8, 11))
    return outer, body


def label(parent, text, **kw):
    return tk.Label(parent, text=text, bg=kw.pop("bg", C["panel"]),
                    fg=kw.pop("fg", C["muted"]),
                    font=(UI_FONT, 9), anchor="w", **kw)


class LabelButton(tk.Label):
    """Botón dibujado sobre un Label. En macOS (Aqua) tk.Button ignora bg y
    queda blanco con el texto claro del tema: ilegible. Responde a lo mismo
    que usa la app de tk.Button: state, bg, invoke()."""

    def __init__(self, parent, command, **kw):
        super().__init__(parent, bd=0, highlightthickness=0,
                         cursor="pointinghand", **kw)
        self._command = command
        self.bind("<ButtonRelease-1>", self._on_release)

    def _on_release(self, e):
        if 0 <= e.x < self.winfo_width() and 0 <= e.y < self.winfo_height():
            self.invoke()

    def invoke(self):
        if str(self["state"]) != "disabled":
            return self._command()


def button(parent, text, command, *, kind="ghost"):
    accent = kind == "accent"
    bg = C["accent"] if accent else C["ghost"]
    hover = C["accent_h"] if accent else C["ghost_h"]
    fg = "#ffffff" if accent else C["fg"]
    font = (UI_FONT, 9, "bold" if accent else "normal")
    padx, pady = (16, 7) if accent else (12, 4)
    if IS_MAC:
        b = LabelButton(parent, command, text=text, bg=bg, fg=fg, font=font,
                        padx=padx, pady=pady,
                        disabledforeground="#f0b3c2" if accent else C["muted"])
    else:
        b = tk.Button(parent, text=text, command=command,
                      bg=bg, fg=fg, activebackground=hover, activeforeground=fg,
                      relief="flat", bd=0, highlightthickness=0, cursor="hand2",
                      font=font, padx=padx, pady=pady)
    b.bind("<Enter>", lambda _e: b.configure(bg=hover)
           if str(b["state"]) != "disabled" else None)
    b.bind("<Leave>", lambda _e: b.configure(bg=bg)
           if str(b["state"]) != "disabled" else None)
    return b


def setup_theme(root: tk.Tk):
    st = ttk.Style(root)
    st.theme_use("clam")

    st.configure("R.TEntry", fieldbackground=C["field"], foreground=C["fg"],
                 insertcolor=C["fg"], bordercolor=C["border"],
                 lightcolor=C["border"], darkcolor=C["border"],
                 selectbackground=C["accent"], selectforeground="#ffffff",
                 padding=5)
    st.map("R.TEntry", bordercolor=[("focus", C["accent"])])

    st.configure("R.TCombobox", fieldbackground=C["field"], background=C["ghost"],
                 foreground=C["fg"], arrowcolor=C["muted"],
                 bordercolor=C["border"], lightcolor=C["border"],
                 darkcolor=C["border"], selectbackground=C["field"],
                 selectforeground=C["fg"], padding=4)
    st.map("R.TCombobox",
           fieldbackground=[("disabled", C["panel"]), ("readonly", C["field"])],
           foreground=[("disabled", C["border"])],
           bordercolor=[("focus", C["accent"])],
           arrowcolor=[("disabled", C["border"]), ("active", C["fg"])])
    root.option_add("*TCombobox*Listbox.background", C["field"])
    root.option_add("*TCombobox*Listbox.foreground", C["fg"])
    root.option_add("*TCombobox*Listbox.selectBackground", C["accent"])
    root.option_add("*TCombobox*Listbox.selectForeground", "#ffffff")

    st.configure("R.TCheckbutton", background=C["panel"], foreground=C["fg"],
                 indicatorbackground=C["field"], indicatorforeground=C["accent"],
                 bordercolor=C["border"], focuscolor=C["panel"],
                 font=(UI_FONT, 9))
    st.map("R.TCheckbutton",
           background=[("active", C["panel"])],
           indicatorbackground=[("selected", C["accent"]), ("active", C["ghost"])],
           indicatorforeground=[("selected", "#ffffff")])

    st.configure("R.Horizontal.TScale", background=C["panel"],
                 troughcolor=C["field"], bordercolor=C["border"],
                 lightcolor=C["accent"], darkcolor=C["accent"])

    st.configure("R.Horizontal.TProgressbar", background=C["accent"],
                 troughcolor=C["field"], bordercolor=C["border"],
                 lightcolor=C["accent"], darkcolor=C["accent"], thickness=8)

    st.configure("R.Vertical.TScrollbar", background=C["ghost"],
                 troughcolor=C["field"], bordercolor=C["border"],
                 arrowcolor=C["muted"], lightcolor=C["ghost"], darkcolor=C["ghost"])
    st.map("R.Vertical.TScrollbar", background=[("active", C["ghost_h"])])

    st.configure("R.TSpinbox", fieldbackground=C["field"], foreground=C["fg"],
                 background=C["ghost"], arrowcolor=C["muted"],
                 bordercolor=C["border"], lightcolor=C["border"],
                 darkcolor=C["border"], insertcolor=C["fg"], padding=4)
    st.map("R.TSpinbox",
           fieldbackground=[("disabled", C["panel"])],
           foreground=[("disabled", C["border"])],
           arrowcolor=[("disabled", C["border"])],
           bordercolor=[("focus", C["accent"])])

    if IS_MAC:
        # El fondo por defecto de clam (gris claro) asomaba en las esquinas.
        st.configure("R.TEntry", background=C["panel"])


def mac_fonts(root: tk.Tk):
    """En Mac, Tk dibuja 1 pt = 1 px (Windows, a 96 dpi, 1,33 px) y 'tk scaling'
    no cambia las fuentes: todo se veía chico. Sube 2 pt cada fuente ya
    construida, y los campos y listas, que traían la del sistema (13 pt),
    pasan a la misma de las etiquetas."""
    def bigger(spec):
        family, size, *rest = root.tk.splitlist(spec)
        return (family, int(size) + 2, *rest)

    def walk(w):
        for c in w.winfo_children():
            try:
                spec = str(c.cget("font"))
            except tk.TclError:                         # frames, widgets ttk
                spec = ""
            if spec and not spec.startswith("Tk"):
                c.configure(font=bigger(spec))
            walk(c)

    walk(root)
    st = ttk.Style(root)
    st.configure("R.TCheckbutton", font=bigger(st.lookup("R.TCheckbutton", "font")))
    for name in ("TkDefaultFont", "TkTextFont"):
        root.tk.call("font", "configure", name, "-family", UI_FONT, "-size", 11)


# --------------------------------------------------------------------------
# App
# --------------------------------------------------------------------------

class App:
    def __init__(self, root: tk.Tk):
        self.root = root
        self.cfg = load_config()
        # Nombres puestos a mano, por nombre de archivo de origen. Se recuerdan
        # entre sesiones y también se respetan en modo carpeta.
        ov = self.cfg.get("name_overrides")
        self.cfg["name_overrides"] = ({str(k): str(v) for k, v in ov.items()}
                                      if isinstance(ov, dict) else {})
        self.q: queue.Queue = queue.Queue()
        self.renderer: Renderer | None = None
        self.running = False
        self.downloader: FFmpegDownloader | None = None
        self._start_after_dl = False           # Extraer quedó esperando la descarga
        self._probe_job = None
        self._name_src: str | None = None      # video al que corresponde v_name
        self._auto_name = ""                   # nombre automático de ese video

        self.ffmpeg_dir = tk.StringVar(value=self.cfg.get("ffmpeg_dir", ""))
        self.ffmpeg = find_tool("ffmpeg", self.ffmpeg_dir.get())
        self.ffprobe = find_tool("ffprobe", self.ffmpeg_dir.get())

        root.title(f"{APP_NAME}  -  random clip extractor")
        root.configure(bg=C["bg"])
        setup_theme(root)
        self._set_icon()

        self._vars()
        self._build()
        if IS_MAC:
            mac_fonts(root)
        self._sync_dist()
        self._sync_codec()
        self._sync_salida()
        if not self._is_batch():
            self.frm_batch.grid_remove()
        else:
            self.lbl_input.configure(text="Carpeta con videos")
            self.v_probe.set("sin carpeta")
        self._sync_name_widgets()
        self._refresh_tools_label()
        self._log("randomcutt listo. Elige un video y una carpeta de salida.", "muted")
        self._tick()

        # Dimensionar según lo que pide el layout: funciona con cualquier DPI/fuente.
        # El tope evita que en una pantalla de 1080 el footer quede debajo de la
        # barra de tareas; la consola absorbe el recorte porque es la fila que
        # tiene weight.
        root.update_idletasks()
        sw, sh = root.winfo_screenwidth(), root.winfo_screenheight()
        w = max(950, root.winfo_reqwidth())
        h = min(root.winfo_reqheight(), int(sh * 0.90))
        # Posición explícita: si Windows la cascadea, el footer puede caer
        # debajo de la barra de tareas. ~40 px de marco y ~48 de barra.
        x = max(0, (sw - w) // 2)
        y = max(0, (sh - 48 - (h + 40)) // 2)
        if IS_MAC:
            # wm maxsize ya descuenta barra de menú, Dock y título. Tk mide la y
            # desde el borde de la pantalla y la barra de menú mide hasta 38 pt:
            # arrancar bajo ella y ceder ese alto, esté donde esté el Dock.
            max_h = min(root.wm_maxsize()[1], sh - 28)
            avail = max_h - 38
            h = min(root.winfo_reqheight(), avail)
            y = max(0, min(sh - max_h - 28, 38) + (avail - h) // 2)
        root.geometry(f"{w}x{h}+{x}+{y}")
        root.minsize(900, min(h, 700))

        root.protocol("WM_DELETE_WINDOW", self._on_close)
        if IS_MAC:
            # Sin esto, Cmd+Q sale con exit: no guarda el config ni pregunta
            # por una exportación en curso.
            root.createcommand("::tk::mac::Quit", self._on_close)

    # -- estado ------------------------------------------------------------
    def _vars(self):
        c = self.cfg
        self.v_input = tk.StringVar()
        self.v_mode = tk.StringVar(value=c.get("mode", "Archivo"))
        self.v_recursive = tk.BooleanVar(value=bool(c.get("recursive", False)))
        self.v_skip_clips = tk.BooleanVar(value=bool(c.get("skip_clips", True)))
        self.v_skip_done = tk.BooleanVar(value=bool(c.get("skip_done", True)))
        self.v_salida_modo = tk.StringVar(value=c.get("salida_modo", SALIDAS[0]))
        self.v_outdir = tk.StringVar(value=c.get("outdir", ""))
        self.v_clips = tk.StringVar(value=str(c.get("clips", 20)))
        self.v_dur = tk.StringVar(value=str(c.get("duration", 5)))
        self.v_codec = tk.StringVar(value=c.get("codec", "hap"))
        self.v_audio = tk.BooleanVar(value=bool(c.get("audio", False)))
        self.v_dist = tk.StringVar(value=c.get("distribucion", "Random puro"))
        self.v_spread = tk.DoubleVar(value=float(c.get("spread", 1.0)))
        self.v_workers = tk.StringVar(value=str(c.get("workers", 3)))
        self.v_hapfmt = tk.StringVar(value=c.get("hap_format", "hap"))
        self.v_chunks = tk.StringVar(value=str(c.get("hap_chunks", 4)))
        self.v_align = tk.StringVar(value=c.get("align", "crop"))
        self.v_suffix = tk.StringVar(value=c.get("suffix", ""))
        self.v_seed = tk.StringVar(value=c.get("seed", ""))
        nombres = c.get("nombres", NOMBRES[0])
        self.v_nombres = tk.StringVar(value=nombres if nombres in NOMBRES else NOMBRES[0])
        self.v_keep_year = tk.BooleanVar(value=bool(c.get("conservar_anio", False)))
        self.v_name = tk.StringVar()
        self.v_probe = tk.StringVar(value="sin archivo")
        self.v_status = tk.StringVar(value="listo")

    def _set_icon(self):
        for folder in (resource_dir(), app_dir()):
            p = folder / "randomcutt.ico"
            if p.is_file():
                try:
                    self.root.iconbitmap(default=str(p))
                    return
                except tk.TclError:
                    pass

    # -- layout ------------------------------------------------------------
    def _build(self):
        root = self.root
        root.columnconfigure(0, weight=1)
        root.rowconfigure(2, weight=1)

        # ---- header -----------------------------------------------------
        head = tk.Frame(root, bg=C["bg"])
        head.grid(row=0, column=0, sticky="ew", padx=18, pady=(12, 8))
        head.columnconfigure(1, weight=1)

        tit = tk.Frame(head, bg=C["bg"])
        tit.grid(row=0, column=0, sticky="w")
        tk.Label(tit, text=APP_NAME, bg=C["bg"], fg=C["fg"],
                 font=(UI_FONT, 19, "bold")).pack(side="left")
        tk.Label(tit, text=f"v{APP_VERSION}", bg=C["bg"], fg=C["accent"],
                 font=(UI_FONT, 9, "bold")).pack(side="left", padx=(8, 0), pady=(9, 0))

        self.lbl_tools = tk.Label(head, text="", bg=C["bg"], fg=C["muted"],
                                  font=(MONO_FONT, 8), anchor="e", justify="right")
        self.lbl_tools.grid(row=0, column=1, sticky="e")
        self.btn_dl = button(head, "Descargar ffmpeg" if IS_WIN else "Cómo instalar ffmpeg",
                             self._download_ffmpeg)
        self.btn_dl.grid(row=0, column=2, sticky="e", padx=(10, 0))

        # ---- columnas ---------------------------------------------------
        cols = tk.Frame(root, bg=C["bg"])
        cols.grid(row=1, column=0, sticky="ew", padx=18)
        cols.columnconfigure(0, weight=1, uniform="c")
        cols.columnconfigure(1, weight=1, uniform="c")

        self._card_origen(cols).grid(row=0, column=0, columnspan=2, sticky="ew")
        self._card_cortes(cols).grid(row=1, column=0, sticky="nsew",
                                     padx=(0, 7), pady=(9, 0))
        self._card_salida(cols).grid(row=1, column=1, sticky="nsew",
                                     padx=(7, 0), pady=(9, 0))
        self._card_avanzado(cols).grid(row=2, column=0, columnspan=2,
                                       sticky="ew", pady=(9, 0))

        # ---- consola ----------------------------------------------------
        logc, logb = card(root, "CONSOLA")
        logc.grid(row=2, column=0, sticky="nsew", padx=18, pady=(9, 0))
        logb.columnconfigure(0, weight=1)
        logb.rowconfigure(0, weight=1)

        self.log = tk.Text(logb, height=6, bg=C["field"], fg=C["fg"],
                           insertbackground=C["fg"], relief="flat", bd=0,
                           highlightthickness=1, highlightbackground=C["border"],
                           font=(MONO_FONT, 9), wrap="none", padx=8, pady=6,
                           state="disabled")
        self.log.grid(row=0, column=0, sticky="nsew")
        sb = ttk.Scrollbar(logb, orient="vertical", command=self.log.yview,
                           style="R.Vertical.TScrollbar")
        sb.grid(row=0, column=1, sticky="ns")
        self.log.configure(yscrollcommand=sb.set)
        for tag, col in (("info", C["fg"]), ("muted", C["muted"]),
                         ("ok", C["ok"]), ("warn", C["warn"]), ("err", C["err"])):
            self.log.tag_configure(tag, foreground=col)

        # ---- footer -----------------------------------------------------
        foot = tk.Frame(root, bg=C["bg"])
        foot.grid(row=3, column=0, sticky="ew", padx=18, pady=12)
        foot.columnconfigure(0, weight=1)

        prog = tk.Frame(foot, bg=C["bg"])
        prog.grid(row=0, column=0, sticky="ew", padx=(0, 16))
        prog.columnconfigure(0, weight=1)
        self.bar = ttk.Progressbar(prog, orient="horizontal", mode="determinate",
                                   style="R.Horizontal.TProgressbar", maximum=100)
        self.bar.grid(row=0, column=0, sticky="ew")
        tk.Label(prog, textvariable=self.v_status, bg=C["bg"], fg=C["muted"],
                 font=(MONO_FONT, 8), anchor="w").grid(row=1, column=0, sticky="w",
                                                       pady=(6, 0))

        self.btn_cancel = button(foot, "Cancelar", self._cancel)
        self.btn_cancel.grid(row=0, column=1, rowspan=2, padx=(0, 8))
        self.btn_cancel.configure(state="disabled")
        self.btn_go = button(foot, "EXTRAER CLIPS", self._start, kind="accent")
        self.btn_go.grid(row=0, column=2, rowspan=2)

    def _card_origen(self, parent):
        outer, b = card(parent, "1 - ORIGEN")
        b.columnconfigure(0, weight=1)

        self.lbl_input = label(b, "Archivo de video")
        self.lbl_input.grid(row=0, column=0, sticky="w")
        cb = ttk.Combobox(b, textvariable=self.v_mode, values=MODOS,
                          state="readonly", style="R.TCombobox", width=9)
        cb.grid(row=0, column=1, sticky="e", padx=(6, 0))
        cb.bind("<<ComboboxSelected>>", lambda _e: self._sync_mode())

        ttk.Entry(b, textvariable=self.v_input, style="R.TEntry").grid(
            row=1, column=0, sticky="ew", pady=(3, 0))
        button(b, "Buscar", self._pick_input).grid(row=1, column=1, sticky="ew",
                                                   padx=(6, 0), pady=(3, 0))

        self.frm_batch = tk.Frame(b, bg=C["panel"])
        self.frm_batch.grid(row=2, column=0, columnspan=2, sticky="w", pady=(9, 0))
        for i, (txt, var) in enumerate((
                ("Incluir subcarpetas", self.v_recursive),
                ("Ignorar archivos *_clip_*", self.v_skip_clips),
                ("Omitir videos ya procesados", self.v_skip_done))):
            ttk.Checkbutton(self.frm_batch, text=txt, variable=var,
                            style="R.TCheckbutton",
                            command=self._probe_later).grid(
                row=0, column=i, sticky="w", padx=(0, 18))

        # -- nombre de los clips ---------------------------------------------
        # Etiquetas en línea, no encima: esta tarjeta ocupa todo el ancho y
        # cada fila que suma se la quita a la consola en pantallas de 1080.
        nm = tk.Frame(b, bg=C["panel"])
        nm.grid(row=3, column=0, columnspan=2, sticky="ew", pady=(10, 0))
        nm.columnconfigure(1, weight=1)
        label(nm, "Nombre de los clips").grid(row=0, column=0, sticky="w", padx=(0, 8))
        self.ent_name = ttk.Entry(nm, textvariable=self.v_name, style="R.TEntry")
        self.ent_name.grid(row=0, column=1, sticky="ew")
        self.btn_auto = button(nm, "Auto", lambda: self.v_name.set(self._auto_name))
        self.btn_auto.grid(row=0, column=2, padx=(6, 0))
        # En modo carpeta ocupa el lugar del campo de texto.
        self.btn_names = button(nm, "Ver nombres en la consola", self._show_names)
        self.btn_names.grid(row=0, column=1, sticky="w")
        label(nm, "Estilo").grid(row=0, column=3, sticky="w", padx=(16, 8))
        cb = ttk.Combobox(nm, textvariable=self.v_nombres, values=NOMBRES,
                          state="readonly", style="R.TCombobox", width=18)
        cb.grid(row=0, column=4, sticky="w")
        cb.bind("<<ComboboxSelected>>", lambda _e: self._on_naming())
        ttk.Checkbutton(nm, text="Conservar año", variable=self.v_keep_year,
                        style="R.TCheckbutton", command=self._on_naming).grid(
            row=0, column=5, sticky="w", padx=(12, 0))

        info = tk.Frame(b, bg=C["panel"])
        info.grid(row=4, column=0, columnspan=2, sticky="ew", pady=(9, 0))
        info.columnconfigure(0, weight=1)
        tk.Label(info, textvariable=self.v_probe, bg=C["panel"], fg=C["muted"],
                 font=(MONO_FONT, 8), anchor="w", justify="left", wraplength=440
                 ).grid(row=0, column=0, sticky="nw")
        self.lbl_name = tk.Label(info, text="", bg=C["panel"], fg=C["muted"],
                                 font=(UI_FONT, 8), anchor="e", justify="right",
                                 wraplength=440)
        self.lbl_name.grid(row=0, column=1, sticky="ne", padx=(12, 0))
        self.v_input.trace_add("write", lambda *_: self._probe_later())
        # El estimado "N clips a generar" depende del número de clips.
        self.v_clips.trace_add("write", lambda *_: self._probe_later())
        # El ejemplo de nombre depende de lo escrito, del sufijo y del códec.
        self.v_name.trace_add("write", lambda *_: self._update_name_hint())
        self.v_suffix.trace_add("write", lambda *_: self._update_name_hint())
        return outer

    def _card_cortes(self, parent):
        outer, b = card(parent, "2 - CORTES")
        b.columnconfigure(0, weight=1)
        b.columnconfigure(1, weight=1)

        label(b, "Número de clips").grid(row=0, column=0, sticky="w")
        label(b, "Duración (seg)").grid(row=0, column=1, sticky="w", padx=(8, 0))
        ttk.Spinbox(b, from_=1, to=9999, textvariable=self.v_clips,
                    style="R.TSpinbox").grid(row=1, column=0, sticky="ew", pady=(3, 0))
        ttk.Spinbox(b, from_=0.1, to=3600, increment=0.5, textvariable=self.v_dur,
                    style="R.TSpinbox").grid(row=1, column=1, sticky="ew",
                                             padx=(8, 0), pady=(3, 0))

        label(b, "Distribución").grid(row=2, column=0, columnspan=2, sticky="w",
                                      pady=(12, 0))
        cb = ttk.Combobox(b, textvariable=self.v_dist, values=DISTRIBUCIONES,
                          state="readonly", style="R.TCombobox")
        cb.grid(row=3, column=0, columnspan=2, sticky="ew", pady=(3, 0))
        cb.bind("<<ComboboxSelected>>", lambda _e: self._sync_dist())

        self.row_spread = tk.Frame(b, bg=C["panel"])
        self.row_spread.grid(row=4, column=0, columnspan=2, sticky="ew", pady=(10, 0))
        self.row_spread.columnconfigure(0, weight=1)
        self.lbl_spread = label(self.row_spread, "Jitter  1.00")
        self.lbl_spread.grid(row=0, column=0, sticky="w")
        ttk.Scale(self.row_spread, from_=0.0, to=1.0, variable=self.v_spread,
                  orient="horizontal", style="R.Horizontal.TScale",
                  command=lambda v: self.lbl_spread.configure(
                      text=f"Jitter  {float(v):.2f}")
                  ).grid(row=1, column=0, sticky="ew", pady=(3, 0))

        self.lbl_hint = tk.Label(b, text="", bg=C["panel"], fg=C["muted"],
                                 font=(UI_FONT, 8), anchor="w", justify="left",
                                 wraplength=380)
        self.lbl_hint.grid(row=5, column=0, columnspan=2, sticky="w", pady=(9, 0))
        return outer

    def _card_salida(self, parent):
        outer, b = card(parent, "3 - SALIDA")
        b.columnconfigure(0, weight=1)

        self.lbl_outdir = label(b, "Carpeta de salida")
        self.lbl_outdir.grid(row=0, column=0, columnspan=3, sticky="w")
        self.ent_outdir = ttk.Entry(b, textvariable=self.v_outdir, style="R.TEntry")
        self.ent_outdir.grid(row=1, column=0, sticky="ew", pady=(3, 0))
        button(b, "Buscar", self._pick_outdir).grid(row=1, column=1, padx=(6, 0),
                                                    pady=(3, 0))
        button(b, "Abrir", self._open_outdir).grid(row=1, column=2, padx=(6, 0),
                                                   pady=(3, 0))

        label(b, "Organizar salida").grid(row=2, column=0, columnspan=3,
                                          sticky="w", pady=(12, 0))
        cbs = ttk.Combobox(b, textvariable=self.v_salida_modo, values=SALIDAS,
                           state="readonly", style="R.TCombobox")
        cbs.grid(row=3, column=0, columnspan=3, sticky="ew", pady=(3, 0))
        cbs.bind("<<ComboboxSelected>>", lambda _e: self._sync_salida())

        self.lbl_salida = tk.Label(b, text="", bg=C["panel"], fg=C["muted"],
                                   font=(UI_FONT, 8), anchor="w", justify="left",
                                   wraplength=380)
        self.lbl_salida.grid(row=4, column=0, columnspan=3, sticky="w", pady=(7, 0))

        label(b, "Códec").grid(row=5, column=0, columnspan=3, sticky="w", pady=(12, 0))
        # En la línea de "Códec" y no en una fila propia: ahorra alto de ventana.
        ttk.Checkbutton(b, text="Incluir audio", variable=self.v_audio,
                        style="R.TCheckbutton").grid(row=5, column=0, columnspan=3,
                                                     sticky="e", pady=(12, 0))
        cb = ttk.Combobox(b, textvariable=self.v_codec, values=CODECS,
                          state="readonly", style="R.TCombobox")
        cb.grid(row=6, column=0, columnspan=3, sticky="ew", pady=(3, 0))
        cb.bind("<<ComboboxSelected>>", lambda _e: self._sync_codec())

        self.lbl_codec = tk.Label(b, text="", bg=C["panel"], fg=C["muted"],
                                  font=(UI_FONT, 8), anchor="w", justify="left",
                                  wraplength=380)
        self.lbl_codec.grid(row=7, column=0, columnspan=3, sticky="w", pady=(8, 0))
        return outer

    def _card_avanzado(self, parent):
        outer, b = card(parent, "4 - AVANZADO")
        for col in range(4):
            b.columnconfigure(col, weight=1, uniform="a")
        pad = (10, 0)

        # -- fila 1: opciones HAP + paralelismo -------------------------------
        self.lbl_hapfmt = label(b, "Variante HAP")
        self.lbl_hapfmt.grid(row=0, column=0, sticky="w")
        self.lbl_chunks = label(b, "Chunks HAP")
        self.lbl_chunks.grid(row=0, column=1, sticky="w", padx=pad)
        self.lbl_align = label(b, "Ajuste a múltiplo de 4")
        self.lbl_align.grid(row=0, column=2, sticky="w", padx=pad)
        label(b, "Procesos en paralelo").grid(row=0, column=3, sticky="w", padx=pad)

        self.w_hapfmt = ttk.Combobox(b, textvariable=self.v_hapfmt,
                                     values=HAP_FORMATS, state="readonly",
                                     style="R.TCombobox")
        self.w_hapfmt.grid(row=1, column=0, sticky="ew", pady=(3, 0))
        self.w_chunks = ttk.Spinbox(b, from_=1, to=64, textvariable=self.v_chunks,
                                    style="R.TSpinbox")
        self.w_chunks.grid(row=1, column=1, sticky="ew", padx=pad, pady=(3, 0))
        self.w_align = ttk.Combobox(b, textvariable=self.v_align,
                                    values=["crop", "pad"], state="readonly",
                                    style="R.TCombobox")
        self.w_align.grid(row=1, column=2, sticky="ew", padx=pad, pady=(3, 0))
        ttk.Spinbox(b, from_=1, to=16, textvariable=self.v_workers,
                    style="R.TSpinbox").grid(row=1, column=3, sticky="ew",
                                             padx=pad, pady=(3, 0))

        tk.Frame(b, bg=C["border"], height=1).grid(row=2, column=0, columnspan=4,
                                                   sticky="ew", pady=13)

        # -- fila 2: nombres, seed, ffmpeg -------------------------------------
        label(b, "Seed (vacío = random)").grid(row=3, column=0, sticky="w")
        label(b, "Sufijo en el nombre").grid(row=3, column=1, sticky="w", padx=pad)
        label(b, "Carpeta bin de ffmpeg").grid(row=3, column=2, columnspan=2,
                                               sticky="w", padx=pad)
        ttk.Entry(b, textvariable=self.v_seed, style="R.TEntry").grid(
            row=4, column=0, sticky="ew", pady=(3, 0))
        ttk.Entry(b, textvariable=self.v_suffix, style="R.TEntry").grid(
            row=4, column=1, sticky="ew", padx=pad, pady=(3, 0))
        ffrow = tk.Frame(b, bg=C["panel"])
        ffrow.grid(row=4, column=2, columnspan=2, sticky="ew", padx=pad, pady=(3, 0))
        ffrow.columnconfigure(0, weight=1)
        ttk.Entry(ffrow, textvariable=self.ffmpeg_dir, style="R.TEntry").grid(
            row=0, column=0, sticky="ew")
        button(ffrow, "...", self._pick_ffmpeg).grid(row=0, column=1, padx=(5, 0))
        return outer

    # -- sincronización de UI ---------------------------------------------
    def _is_batch(self) -> bool:
        return self.v_mode.get() == "Carpeta"

    def _sync_mode(self):
        batch = self._is_batch()
        self.lbl_input.configure(text="Carpeta con videos" if batch
                                 else "Archivo de video")
        if batch:
            self.frm_batch.grid()
        else:
            self.frm_batch.grid_remove()
        self.v_input.set("")
        self.v_probe.set("sin carpeta" if batch else "sin archivo")
        self._sync_name_widgets()
        self._sync_salida()

    def _sync_name_widgets(self):
        """Archivo: campo editable + Auto. Carpeta: un nombre por video, se
        revisan en la consola."""
        if self._is_batch():
            self.ent_name.grid_remove()
            self.btn_auto.grid_remove()
            self.btn_names.grid()
        else:
            self.btn_names.grid_remove()
            self.ent_name.grid()
            self.btn_auto.grid()
        self._update_name_hint()

    def _sync_salida(self):
        modo = self.v_salida_modo.get()
        hints = {
            "Subcarpeta por video": f"Cada video a <salida>{os.sep}<nombre del video>{os.sep}",
            "Todo en una carpeta": "Todos los clips sueltos en la carpeta de salida.",
            "Junto al video de origen": "Al lado de cada video fuente.",
        }
        self.lbl_salida.configure(text=hints.get(modo, ""))
        libre = modo == "Junto al video de origen"
        self.ent_outdir.configure(state="disabled" if libre else "normal")
        self.lbl_outdir.configure(fg=C["border"] if libre else C["muted"])

    def _sync_dist(self):
        mode = self.v_dist.get()
        if mode == "Estratificado":
            self.row_spread.grid()
            self.lbl_hint.configure(
                text="Divide el video en N tramos iguales y saca un clip de cada uno "
                     "con jitter. Cobertura pareja, sin zonas repetidas.")
        else:
            self.row_spread.grid_remove()
            self.lbl_hint.configure(
                text="Puntos de inicio totalmente al azar (como v006). Puede repetir "
                     "o solapar zonas del video."
                if mode == "Random puro" else
                "Cortes a intervalos exactos y regulares. Cero azar.")

    def _sync_codec(self):
        codec = self.v_codec.get()
        hints = {
            "hap": "HAP (.mov): decodifica en GPU, lo ideal para TouchDesigner.",
            "prores_ks": "ProRes HQ (.mov). Decodifica en CPU.",
            "libx264": "H.264 CRF 18 (.mp4). Liviano, pero decodifica en CPU.",
            "h264_nvenc": "H.264 por NVENC (.mp4). El más rápido de exportar.",
            "h264_videotoolbox": "H.264 por hardware de Apple (.mp4). El más rápido "
                                 "de exportar.",
            "copy": "Sin recomprimir, misma extensión. Corta solo en keyframes.",
        }
        self.lbl_codec.configure(text=hints.get(codec, ""))
        # Las opciones HAP se desactivan en vez de ocultarse: el layout no salta.
        is_hap = codec == "hap"
        for w in (self.w_hapfmt, self.w_align):
            w.configure(state="readonly" if is_hap else "disabled")
        self.w_chunks.configure(state="normal" if is_hap else "disabled")
        for lb in (self.lbl_hapfmt, self.lbl_chunks, self.lbl_align):
            lb.configure(fg=C["muted"] if is_hap else C["border"])
        self._update_name_hint()                # la extensión del ejemplo cambia

    # -- nombres -----------------------------------------------------------
    def _on_naming(self):
        self._refresh_name()
        if self._is_batch():
            self._probe_later()                 # la muestra usa los nombres

    def _refresh_name(self):
        """Recalcula el nombre automático del video elegido (modo Archivo).

        Lo escrito a mano no se pisa al cambiar de estilo: solo se reemplaza
        si el campo sigue mostrando el automático anterior, o si cambia el
        video (entonces se carga su nombre guardado, si tiene uno)."""
        if self._is_batch():
            return
        path = self.v_input.get().strip().strip('"')
        if not path or not os.path.isfile(path):
            self._name_src, self._auto_name = None, ""
            self.v_name.set("")
            return
        src = Path(path)
        auto = clip_base_name(src.stem, self.v_nombres.get(), self.v_keep_year.get())
        cur = self.v_name.get().strip()
        if str(src) != self._name_src:
            new = self.cfg["name_overrides"].get(src.name) or auto
        elif not cur or cur == self._auto_name:
            new = auto
        else:
            new = cur
        self._name_src, self._auto_name = str(src), auto
        self.v_name.set(new)

    def _update_name_hint(self):
        if not hasattr(self, "lbl_name"):
            return
        if self._is_batch():
            self.lbl_name.configure(
                text="Un nombre por video, según el estilo.\n"
                     "Los corregidos a mano en modo Archivo se respetan.")
            return
        if self._name_src is None:
            self.lbl_name.configure(text="")
            return
        typed = sanitize_name(self.v_name.get())
        manual = bool(typed) and typed != self._auto_name
        name = style_manual(typed, self.v_nombres.get()) if manual else self._auto_name
        codec = self.v_codec.get()
        ext = Path(self._name_src).suffix if codec == "copy" else CODEC_EXT.get(codec, "")
        self.lbl_name.configure(
            text=f"{name}_clip_1{norm_suffix(self.v_suffix.get())}{ext}\n"
                 + ("nombre manual, se recuerda para este video" if manual
                    else "nombre automático"))

    def _show_names(self):
        """Modo carpeta: vuelca a la consola origen -> nombre de todo el lote."""
        if self.running:
            return
        path = self.v_input.get().strip().strip('"')
        if not path or not os.path.isdir(path):
            self._log("Elige primero una carpeta con videos.", "warn")
            return
        srcs = collect_sources(path, self.v_recursive.get(), self.v_skip_clips.get())
        if not srcs:
            self._log("Ningún video usable en esa carpeta.", "warn")
            return
        names = resolve_names(srcs, self.v_nombres.get(), self.v_keep_year.get(),
                              self.cfg["name_overrides"])
        self._log("")
        self._log(f"Nombres para {len(srcs)} videos (estilo {self.v_nombres.get()}"
                  + (", con año" if self.v_keep_year.get() else "") + "):")
        width = min(max(len(p.name) for p in srcs), 60)
        for p, (name, note) in zip(srcs, names):
            orig = p.name if len(p.name) <= 60 else p.name[:57] + "..."
            self._log(f"  {orig:<{width}}  ->  {name}" + (f"   [{note}]" if note else ""),
                      "warn" if note == "repetido" else
                      "muted" if name == p.stem else "info")

    def _refresh_tools_label(self):
        # Solo se muestra si falta algo: la ruta de ffmpeg no aporta en pantalla.
        if self.ffmpeg and self.ffprobe:
            self.lbl_tools.configure(text="")
            self.btn_dl.grid_remove()
        else:
            missing = ", ".join(n for n, v in (("ffmpeg", self.ffmpeg),
                                               ("ffprobe", self.ffprobe)) if not v)
            self.lbl_tools.configure(text=f"NO ENCONTRADO: {missing}", fg=C["err"])
            self.btn_dl.grid()

    # -- descarga de ffmpeg ------------------------------------------------
    def _download_ffmpeg(self, ask: bool = True, then_start: bool = False):
        if self.running or self.downloader:
            return
        if not IS_WIN:
            messagebox.showinfo(
                APP_NAME,
                "Instala ffmpeg y vuelve a abrir la app:\n\n"
                f"macOS:  {BREW_HAP_CMD}\n"
                "(el ffmpeg normal de Homebrew no trae HAP)\n\n"
                "Linux:  sudo apt install ffmpeg\n\n"
                "O indica su carpeta bin en AVANZADO.")
            return
        dest = managed_ffmpeg_dir()
        if ask and not messagebox.askyesno(
                APP_NAME,
                "Descargo ffmpeg y ffprobe (build completo de gyan.dev, con HAP, "
                f"unos 100 MB) en:\n\n{dest}\n\nNo toca el PATH ni el resto del "
                "sistema. ¿Continuar?"):
            return

        self._start_after_dl = then_start
        self.downloader = FFmpegDownloader(self.q)
        self.btn_go.configure(state="disabled", bg=C["accent_d"])
        self.btn_dl.configure(state="disabled")
        self.btn_cancel.configure(state="normal")
        self.bar.configure(value=0)
        self.v_status.set("descargando ffmpeg ...")
        threading.Thread(target=self.downloader.run, daemon=True).start()

    def _dl_finish(self, res: dict):
        self.downloader = None
        self.btn_go.configure(state="normal", bg=C["accent"])
        self.btn_dl.configure(state="normal")
        self.btn_cancel.configure(state="disabled")
        start, self._start_after_dl = self._start_after_dl, False

        if res.get("cancelled"):
            self.bar.configure(value=0)
            self.v_status.set("descarga cancelada")
            self._log("Descarga de ffmpeg cancelada.", "warn")
            return
        if not res.get("ok"):
            self.bar.configure(value=0)
            self.v_status.set("no se pudo descargar ffmpeg")
            self._log(f"No se pudo descargar ffmpeg: {res.get('error', '')}", "err")
            messagebox.showerror(
                APP_NAME,
                "No se pudo descargar ffmpeg. Revisa la conexión y el detalle en la "
                "consola.\n\nTambién puedes instalarlo a mano "
                "(winget install Gyan.FFmpeg) o indicar su carpeta bin en AVANZADO.")
            return

        self.ffmpeg = find_tool("ffmpeg", self.ffmpeg_dir.get())
        self.ffprobe = find_tool("ffprobe", self.ffmpeg_dir.get())
        self._refresh_tools_label()
        if not (self.ffmpeg and self.ffprobe):             # no debería pasar
            self._log(f"ffmpeg quedó en {res.get('dir')}, pero no lo encuentro. "
                      "Indica esa carpeta en AVANZADO.", "err")
            return
        self.bar.configure(value=100)
        self.v_status.set("ffmpeg listo")
        self._log("ffmpeg listo.", "ok")
        self._probe_later()                    # la info del video necesita ffprobe
        if start:
            self._start(clear_log=False)

    # -- acciones ----------------------------------------------------------
    def _pick_input(self):
        start = self.cfg.get("input_dir") or None
        if self._is_batch():
            path = filedialog.askdirectory(title="Carpeta con videos",
                                           initialdir=self.v_input.get() or start)
            if path:
                self.v_input.set(path)
                self.cfg["input_dir"] = path
            return
        path = filedialog.askopenfilename(title="Video de origen",
                                          filetypes=VIDEO_TYPES,
                                          initialdir=start)
        if path:
            self.v_input.set(path)
            self.cfg["input_dir"] = str(Path(path).parent)

    def _pick_outdir(self):
        path = filedialog.askdirectory(title="Carpeta de salida",
                                       initialdir=self.v_outdir.get() or None)
        if path:
            self.v_outdir.set(path)

    def _pick_ffmpeg(self):
        path = filedialog.askdirectory(title="Carpeta bin de ffmpeg")
        if path:
            self.ffmpeg_dir.set(path)
            self.ffmpeg = find_tool("ffmpeg", path)
            self.ffprobe = find_tool("ffprobe", path)
            self._refresh_tools_label()
            self._log(f"ffmpeg: {self.ffmpeg}" if self.ffmpeg
                      else "Ahí no hay ffmpeg.exe / ffprobe.exe" if IS_WIN
                      else "Ahí no hay ffmpeg / ffprobe",
                      "ok" if self.ffmpeg else "err")
            self._probe_later()

    def _open_outdir(self):
        d = self.v_outdir.get()
        if not d or not os.path.isdir(d):
            return
        try:
            if IS_WIN:
                os.startfile(d)                                  # noqa: S606
            else:
                subprocess.Popen(["open" if IS_MAC else "xdg-open", d])
        except OSError as exc:
            self._log(f"No se pudo abrir la carpeta: {exc}", "err")

    def _probe_later(self):
        if self._probe_job:
            self.root.after_cancel(self._probe_job)
        self._probe_job = self.root.after(350, self._probe_input)

    def _probe_input(self):
        self._probe_job = None
        path = self.v_input.get().strip().strip('"')
        self._refresh_name()

        if self._is_batch():
            if not path or not os.path.isdir(path):
                self.v_probe.set("sin carpeta")
                return
            srcs = collect_sources(path, self.v_recursive.get(),
                                   self.v_skip_clips.get())
            if not srcs:
                self.v_probe.set("ningún video usable en esa carpeta")
                return
            try:
                n = int(float(self.v_clips.get()))
            except ValueError:
                n = 0
            total = sum(p.stat().st_size for p in srcs)
            peso = (f"{total / (1 << 30):.1f} GB" if total >= (1 << 30)
                    else f"{total / (1 << 20):.0f} MB")
            names = resolve_names(srcs, self.v_nombres.get(), self.v_keep_year.get(),
                                  self.cfg["name_overrides"])
            muestra = ", ".join(nm for nm, _ in names[:3])
            if len(srcs) > 3:
                muestra += f", +{len(srcs) - 3} más"
            self.v_probe.set(
                f"{len(srcs)} videos  ·  {peso}"
                + (f"  ·  {len(srcs) * n} clips a generar" if n else "")
                + f"\n{muestra}")
            return

        if not path or not os.path.isfile(path):
            self.v_probe.set("sin archivo")
            return
        if not self.ffprobe:
            self.v_probe.set("ffprobe no encontrado")
            return
        try:
            info = probe(self.ffprobe, path)
        except (RuntimeError, ValueError, OSError) as exc:
            self.v_probe.set(f"no se pudo leer: {exc}")
            return
        warn = "   [!] no es múltiplo de 4" if (info["width"] % 4 or
                                                info["height"] % 4) else ""
        self.v_probe.set(
            f"{info['codec']}  {info['width']}x{info['height']}{warn}\n"
            f"{fmt_dur(info['duration'])}  ·  {info['fps']:.3f} fps  ·  "
            f"audio: {'sí' if info['has_audio'] else 'no'}")

    def _collect(self) -> dict | None:
        if not self.ffmpeg or not self.ffprobe:
            if IS_WIN:
                if messagebox.askyesno(
                        APP_NAME,
                        "No encontré ffmpeg / ffprobe.\n\n¿Los descargo ahora? Son "
                        "unos 100 MB y la extracción parte sola al terminar.\n\n"
                        "Si ya los tienes en otra carpeta, responde No e indica su "
                        "carpeta bin en AVANZADO."):
                    self._download_ffmpeg(ask=False, then_start=True)
            else:
                self._download_ffmpeg()        # instrucciones para macOS / Linux
            return None
        # Fuera de Windows no hay descarga que garantice HAP: sin este aviso,
        # cada clip fallaría con "Unknown encoder".
        if not IS_WIN and self.v_codec.get() == "hap" and not has_hap_encoder(self.ffmpeg):
            messagebox.showerror(
                APP_NAME,
                f"Este ffmpeg no trae el encoder HAP:\n{self.ffmpeg}\n\n"
                "Elige otro códec, o en macOS instala uno con HAP:\n\n"
                f"brew uninstall ffmpeg\n{BREW_HAP_CMD}")
            return None

        batch = self._is_batch()
        src = self.v_input.get().strip().strip('"')
        if batch:
            if not os.path.isdir(src):
                messagebox.showerror(APP_NAME, "Elige una carpeta válida.")
                return None
            if not collect_sources(src, self.v_recursive.get(),
                                   self.v_skip_clips.get()):
                messagebox.showerror(
                    APP_NAME,
                    "No hay videos usables en esa carpeta.\n\nSi los archivos "
                    "tienen '_clip_' en el nombre, desmarca la opción que los "
                    "ignora, o activa 'Incluir subcarpetas'.")
                return None
        elif not os.path.isfile(src):
            messagebox.showerror(APP_NAME, "Elige un archivo de video válido.")
            return None

        salida_modo = self.v_salida_modo.get()
        outdir = self.v_outdir.get().strip().strip('"')
        if salida_modo == "Junto al video de origen":
            outdir = src if batch else str(Path(src).parent)
        else:
            if not outdir:
                messagebox.showerror(APP_NAME, "Elige una carpeta de salida.")
                return None
            if not os.path.isdir(outdir):
                if not messagebox.askyesno(
                        APP_NAME, f"La carpeta no existe:\n{outdir}\n\n¿La creo?"):
                    return None
                try:
                    Path(outdir).mkdir(parents=True, exist_ok=True)
                except OSError as exc:
                    messagebox.showerror(APP_NAME,
                                         f"No pude crear la carpeta:\n{exc}")
                    return None

        try:
            clips = int(float(self.v_clips.get()))
            duration = float(self.v_dur.get().replace(",", "."))
            workers = int(float(self.v_workers.get()))
            chunks = int(float(self.v_chunks.get()))
        except ValueError:
            messagebox.showerror(
                APP_NAME,
                "Número de clips, duración, procesos y chunks tienen que ser números.")
            return None
        if clips < 1 or duration <= 0:
            messagebox.showerror(APP_NAME, "Número de clips >= 1 y duración > 0.")
            return None

        seed_raw = self.v_seed.get().strip()
        seed = None
        if seed_raw:
            try:
                seed = int(seed_raw)
            except ValueError:
                seed = seed_raw

        # Nombre: en modo Archivo, lo escrito a mano se guarda para ese video
        # (y deja de guardarse si vuelve a coincidir con el automático).
        overrides = self.cfg["name_overrides"]
        nombres, keep_year = self.v_nombres.get(), bool(self.v_keep_year.get())
        name = ""
        if not batch:
            srcp = Path(src)
            auto = clip_base_name(srcp.stem, nombres, keep_year)
            typed = sanitize_name(self.v_name.get())
            name = auto
            if typed and typed != auto:
                overrides[srcp.name] = typed
                name = style_manual(typed, nombres)
            else:
                overrides.pop(srcp.name, None)

        return {
            "ffmpeg": self.ffmpeg, "ffprobe": self.ffprobe,
            "input": src, "outdir": outdir,
            "batch": batch,
            "recursive": bool(self.v_recursive.get()),
            "skip_clips": bool(self.v_skip_clips.get()),
            "skip_done": bool(self.v_skip_done.get()),
            "salida_modo": salida_modo,
            "clips": clips, "duration": duration,
            "codec": self.v_codec.get(), "audio": bool(self.v_audio.get()),
            "distribucion": self.v_dist.get(), "spread": float(self.v_spread.get()),
            "workers": max(1, min(workers, 16)),
            "hap_format": self.v_hapfmt.get(),
            "hap_chunks": max(1, min(chunks, 64)),
            "align": self.v_align.get(), "suffix": norm_suffix(self.v_suffix.get()),
            "seed": seed,
            "nombres": nombres, "keep_year": keep_year, "name": name,
            "overrides": dict(overrides),
        }

    def _start(self, clear_log: bool = True):
        if self.running or self.downloader:
            return
        cfg = self._collect()
        if cfg is None:
            return
        # Guardar ya, no solo al cerrar: un nombre corregido a mano no se
        # pierde si la app se cierra mal a mitad de un lote.
        self._store_cfg()
        save_config(self.cfg)

        if clear_log:
            self._clear_log()
        else:
            self._log("")
        self.running = True
        self.btn_go.configure(state="disabled", bg=C["accent_d"])
        self.btn_cancel.configure(state="normal")
        self.bar.configure(value=0)
        self.v_status.set("procesando ...")

        self.renderer = Renderer(self.q)
        threading.Thread(target=self.renderer.run, args=(cfg,), daemon=True).start()

    def _cancel(self):
        if self.downloader:
            self.v_status.set("cancelando descarga ...")
            self.downloader.cancel.set()
            return
        if self.renderer and self.running:
            self.v_status.set("cancelando ...")
            self._log("Cancelando ...", "warn")
            self.renderer.abort()

    def _finish(self, res: dict):
        self.running = False
        self.renderer = None
        self.btn_go.configure(state="normal", bg=C["accent"])
        self.btn_cancel.configure(state="disabled")
        ok = res.get("ok", 0)
        fail = res.get("fail", 0)
        skipped = res.get("skipped", 0)
        if res.get("aborted") and not ok and not fail:
            self.v_status.set("cancelado")
            return
        extra = ([f"{fail} con error"] if fail else []) + \
                ([f"{skipped} omitidos"] if skipped else [])
        self.v_status.set(f"{ok} clips exportados"
                          + ("  -  " + "  -  ".join(extra) if extra else ""))
        cola = ("\n" + ", ".join(extra)) if extra else ""
        if fail:
            messagebox.showwarning(
                APP_NAME, f"{ok} clips exportados.{cola}\n\n"
                          "Mira la consola para el detalle de ffmpeg.")
        elif ok or skipped:
            messagebox.showinfo(
                APP_NAME, f"{ok} clips exportados en:\n{res.get('outdir', '')}{cola}")

    # -- cola / log --------------------------------------------------------
    def _tick(self):
        try:
            while True:
                msg = self.q.get_nowait()
                kind = msg[0]
                if kind == "log":
                    self._log(msg[1], msg[2])
                elif kind == "progress":
                    p = msg[1]
                    self.bar.configure(
                        value=(p["done"] / p["total"] * 100) if p["total"] else 0)
                    eta = p.get("eta")
                    tail = (f"  -  quedan ~{fmt_dur(eta)}"
                            if eta and p["done"] < p["total"] else "")
                    self.v_status.set(f"{p['done']}/{p['total']} clips{tail}")
                elif kind == "done":
                    self._finish(msg[1] or {})
                elif kind == "dl_progress":
                    p = msg[1]
                    mb = 1 << 20
                    if p["total"]:
                        self.bar.configure(value=p["done"] / p["total"] * 100)
                        self.v_status.set(f"descargando ffmpeg  {p['done'] / mb:.0f} "
                                          f"/ {p['total'] / mb:.0f} MB")
                    else:
                        self.v_status.set(f"descargando ffmpeg  {p['done'] / mb:.0f} MB")
                elif kind == "dl_done":
                    self._dl_finish(msg[1] or {})
        except queue.Empty:
            pass
        self.root.after(80, self._tick)

    def _log(self, text: str, tag: str = "info"):
        self.log.configure(state="normal")
        self.log.insert("end", text + "\n", tag)
        self.log.see("end")
        self.log.configure(state="disabled")

    def _clear_log(self):
        self.log.configure(state="normal")
        self.log.delete("1.0", "end")
        self.log.configure(state="disabled")

    # -- cierre ------------------------------------------------------------
    def _on_close(self):
        if self.running and not messagebox.askyesno(
                APP_NAME, "Hay una exportación en curso. ¿Salir igual?"):
            return
        if self.downloader and not messagebox.askyesno(
                APP_NAME, "Hay una descarga de ffmpeg en curso. ¿Salir igual?"):
            return
        if self.renderer:
            self.renderer.abort()
        if self.downloader:
            self.downloader.cancel.set()
        self._store_cfg()
        save_config(self.cfg)
        self.root.destroy()

    def _store_cfg(self):
        self.cfg.update({
            "ffmpeg_dir": self.ffmpeg_dir.get(),
            "mode": self.v_mode.get(),
            "recursive": bool(self.v_recursive.get()),
            "skip_clips": bool(self.v_skip_clips.get()),
            "skip_done": bool(self.v_skip_done.get()),
            "salida_modo": self.v_salida_modo.get(),
            "outdir": self.v_outdir.get(),
            "clips": self.v_clips.get(),
            "duration": self.v_dur.get(),
            "codec": self.v_codec.get(),
            "audio": bool(self.v_audio.get()),
            "distribucion": self.v_dist.get(),
            "spread": float(self.v_spread.get()),
            "workers": self.v_workers.get(),
            "hap_format": self.v_hapfmt.get(),
            "hap_chunks": self.v_chunks.get(),
            "align": self.v_align.get(),
            "suffix": self.v_suffix.get(),
            "seed": self.v_seed.get(),
            "nombres": self.v_nombres.get(),
            "conservar_anio": bool(self.v_keep_year.get()),
        })


def main():
    if IS_WIN:
        try:
            import ctypes
            ctypes.windll.shcore.SetProcessDpiAwareness(1)
        except Exception:                                        # noqa: BLE001
            pass
    root = tk.Tk()
    App(root)
    root.mainloop()


if __name__ == "__main__":
    main()
