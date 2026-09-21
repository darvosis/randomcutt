#!/usr/bin/env python3
"""
randomcutt - extractor de clips random para VJ / TouchDesigner
==============================================================

Saca N clips de duración fija desde un video largo y los exporta en HAP
(u otro códec) para alimentar videocutter / moviefilein en TouchDesigner.

v0.8.0 - GUI, modo carpeta (lote) y detección automática de ffmpeg.
Un solo archivo, sin dependencias externas (solo stdlib + ffmpeg/ffprobe).
"""

from __future__ import annotations

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
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from tkinter import filedialog, messagebox, ttk

APP_NAME = "randomcutt"
APP_VERSION = "0.8.0"

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
UI_FONT = "Segoe UI" if IS_WIN else "Helvetica"
MONO_FONT = "Consolas" if IS_WIN else "Menlo"

CODECS = ["hap", "prores_ks", "libx264", "h264_nvenc", "copy"]
CODEC_EXT = {
    "hap": ".mov",
    "prores_ks": ".mov",
    "libx264": ".mp4",
    "h264_nvenc": ".mp4",
}
HAP_FORMATS = ["hap", "hap_alpha", "hap_q"]
DISTRIBUCIONES = ["Random puro", "Estratificado", "Lineal"]

MODOS = ["Archivo", "Carpeta"]
SALIDAS = ["Subcarpeta por video", "Todo en una carpeta", "Junto al video de origen"]

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


def output_dir_for(src: Path, cfg: dict) -> Path:
    """Dónde caen los clips de este video, según cómo se quiera organizar."""
    modo = cfg["salida_modo"]
    if modo == "Junto al video de origen":
        return src.parent
    base = Path(cfg["outdir"])
    return base / src.stem if modo == "Subcarpeta por video" else base


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

        n = cfg["clips"]
        state = {"done": 0, "total": len(sources) * n, "t0": t0}
        self._emit_progress(state)

        tot = {"ok": 0, "fail": 0, "skipped": 0}
        for i, src in enumerate(sources):
            if self.cancel.is_set():
                break
            if len(sources) > 1:
                self.log("")
                self.log(f"[{i + 1}/{len(sources)}] {src.name}")
            else:
                self.log(f"Analizando {src.name} ...")
            res = self._run_source(src, cfg, state)
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

    def _run_source(self, src: Path, cfg: dict, state: dict) -> dict:
        n, dur = cfg["clips"], cfg["duration"]
        outdir = output_dir_for(src, cfg)

        if cfg["batch"] and cfg["skip_done"]:
            have = already_done(outdir, src.stem)
            if have:
                self.log(f"  omitido: {outdir.name} ya tiene {have} clips",
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
        paths = [unique_path(outdir / f"{src.stem}_clip_{i + 1}{sfx}{ext}", reserved)
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


def button(parent, text, command, *, kind="ghost"):
    accent = kind == "accent"
    bg = C["accent"] if accent else C["ghost"]
    hover = C["accent_h"] if accent else C["ghost_h"]
    b = tk.Button(parent, text=text, command=command,
                  bg=bg, fg="#ffffff" if accent else C["fg"],
                  activebackground=hover,
                  activeforeground="#ffffff" if accent else C["fg"],
                  relief="flat", bd=0, highlightthickness=0, cursor="hand2",
                  font=(UI_FONT, 9, "bold" if accent else "normal"),
                  padx=16 if accent else 12, pady=7 if accent else 4)
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


# --------------------------------------------------------------------------
# App
# --------------------------------------------------------------------------

class App:
    def __init__(self, root: tk.Tk):
        self.root = root
        self.cfg = load_config()
        self.q: queue.Queue = queue.Queue()
        self.renderer: Renderer | None = None
        self.running = False
        self._probe_job = None

        self.ffmpeg_dir = tk.StringVar(value=self.cfg.get("ffmpeg_dir", ""))
        self.ffmpeg = find_tool("ffmpeg", self.ffmpeg_dir.get())
        self.ffprobe = find_tool("ffprobe", self.ffmpeg_dir.get())

        root.title(f"{APP_NAME}  -  random clip extractor")
        root.configure(bg=C["bg"])
        setup_theme(root)
        self._set_icon()

        self._vars()
        self._build()
        self._sync_dist()
        self._sync_codec()
        self._sync_salida()
        if not self._is_batch():
            self.frm_batch.grid_remove()
        else:
            self.lbl_input.configure(text="Carpeta con videos")
            self.v_probe.set("sin carpeta")
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
        root.geometry(f"{w}x{h}+{x}+{y}")
        root.minsize(900, min(h, 700))

        root.protocol("WM_DELETE_WINDOW", self._on_close)

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

        tk.Label(b, textvariable=self.v_probe, bg=C["panel"], fg=C["muted"],
                 font=(MONO_FONT, 8), anchor="w", justify="left", wraplength=840
                 ).grid(row=3, column=0, columnspan=2, sticky="w", pady=(9, 0))
        self.v_input.trace_add("write", lambda *_: self._probe_later())
        # El estimado "N clips a generar" depende del número de clips.
        self.v_clips.trace_add("write", lambda *_: self._probe_later())
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
        cb = ttk.Combobox(b, textvariable=self.v_codec, values=CODECS,
                          state="readonly", style="R.TCombobox")
        cb.grid(row=6, column=0, columnspan=3, sticky="ew", pady=(3, 0))
        cb.bind("<<ComboboxSelected>>", lambda _e: self._sync_codec())

        self.lbl_codec = tk.Label(b, text="", bg=C["panel"], fg=C["muted"],
                                  font=(UI_FONT, 8), anchor="w", justify="left",
                                  wraplength=380)
        self.lbl_codec.grid(row=7, column=0, columnspan=3, sticky="w", pady=(8, 0))

        ttk.Checkbutton(b, text="Incluir audio", variable=self.v_audio,
                        style="R.TCheckbutton").grid(row=8, column=0, columnspan=3,
                                                     sticky="w", pady=(10, 0))
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
        self._sync_salida()

    def _sync_salida(self):
        modo = self.v_salida_modo.get()
        hints = {
            "Subcarpeta por video": "Cada video a <salida>\\<nombre del video>\\",
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

    def _refresh_tools_label(self):
        if self.ffmpeg and self.ffprobe:
            self.lbl_tools.configure(text=f"ffmpeg: {self.ffmpeg}", fg=C["muted"])
        else:
            missing = ", ".join(n for n, v in (("ffmpeg", self.ffmpeg),
                                               ("ffprobe", self.ffprobe)) if not v)
            self.lbl_tools.configure(text=f"NO ENCONTRADO: {missing}", fg=C["err"])

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
                      else "Ahí no hay ffmpeg.exe / ffprobe.exe",
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
                subprocess.Popen(["open" if sys.platform == "darwin" else "xdg-open", d])
        except OSError as exc:
            self._log(f"No se pudo abrir la carpeta: {exc}", "err")

    def _probe_later(self):
        if self._probe_job:
            self.root.after_cancel(self._probe_job)
        self._probe_job = self.root.after(350, self._probe_input)

    def _probe_input(self):
        self._probe_job = None
        path = self.v_input.get().strip().strip('"')

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
            muestra = ", ".join(p.stem for p in srcs[:3])
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
            messagebox.showerror(
                APP_NAME,
                "No encontré ffmpeg / ffprobe.\n\nInstálalos "
                "(winget install Gyan.FFmpeg) o indica la carpeta bin en AVANZADO.")
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

        sfx = self.v_suffix.get().strip()
        if sfx and not sfx.startswith(("_", "-", ".")):
            sfx = "_" + sfx

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
            "align": self.v_align.get(), "suffix": sfx, "seed": seed,
        }

    def _start(self):
        if self.running:
            return
        cfg = self._collect()
        if cfg is None:
            return

        self._clear_log()
        self.running = True
        self.btn_go.configure(state="disabled", bg=C["accent_d"])
        self.btn_cancel.configure(state="normal")
        self.bar.configure(value=0)
        self.v_status.set("procesando ...")

        self.renderer = Renderer(self.q)
        threading.Thread(target=self.renderer.run, args=(cfg,), daemon=True).start()

    def _cancel(self):
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
        if self.renderer:
            self.renderer.abort()
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
        })
        save_config(self.cfg)
        self.root.destroy()


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
