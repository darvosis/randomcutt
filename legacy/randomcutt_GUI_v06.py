import tkinter as tk
from tkinter import filedialog, messagebox, ttk
import subprocess
import random
import os
import threading

# Rutas absolutas a FFmpeg y FFprobe
ffmpeg_path = r"C:\Program Files\ffmpeg\bin\ffmpeg.exe"
ffprobe_path = r"C:\Program Files\ffmpeg\bin\ffprobe.exe"

# Variables globales de GUI
input_file_entry = None
num_clips_entry = None
clip_duration_entry = None
output_directory_entry = None
codec_var = None
audio_var = None
progress_bar = None  # Barra de progreso

# Función para generar un nombre de archivo único si ya existe
def unique_path(path):
    base, ext = os.path.splitext(path)
    counter = 1
    while os.path.exists(path):
        path = f"{base}_{counter}{ext}"
        counter += 1
    return path

# Función para extraer clips (se ejecutará en un hilo)
def extract_random_clips(input_file, num_clips, clip_duration, output_directory, codec="hap", audio=False):
    try:
        # Obtener duración del video usando ffprobe
        cmd_duration = [
            ffprobe_path, "-v", "error", "-select_streams", "v:0",
            "-show_entries", "format=duration", "-of", "default=noprint_wrappers=1:nokey=1",
            input_file
        ]
        result = subprocess.run(cmd_duration, capture_output=True, text=True)
        total_duration = float(result.stdout.strip())
    except ValueError:
        messagebox.showerror("Error", "No se pudo leer la duración del video.")
        return []

    file_name, _ = os.path.splitext(os.path.basename(input_file))
    clips = []

    for i in range(num_clips):
        random_start = random.uniform(0, max(0, total_duration - clip_duration))

        clip_name = f"{file_name}_clip_{i+1}.mov" if codec in ["hap", "prores_ks"] else f"{file_name}_clip_{i+1}.mp4"
        clip_path = os.path.join(output_directory, clip_name)
        clip_path = unique_path(clip_path)
        clips.append(clip_path)

        cmd = [
            ffmpeg_path, "-y",
            "-ss", str(random_start),
            "-i", input_file,
            "-t", str(clip_duration)
        ]

        # Configuración del codec
        if codec == "hap":
            cmd += ["-c:v", "hap"]
        elif codec == "prores_ks":
            cmd += ["-c:v", "prores_ks", "-profile:v", "3", "-qscale:v", "10"]
        else:
            cmd += ["-c:v", codec]

        if audio:
            cmd += ["-c:a", "aac"]
        else:
            cmd += ["-an"]

        cmd.append(clip_path)

        subprocess.run(cmd, text=True, check=True)

        # Actualizar barra de progreso en la GUI principal
        progress = (i + 1) / num_clips * 100
        root.after(0, lambda p=progress: progress_bar.config(value=p))

    # Mostrar mensaje al terminar
    root.after(0, lambda: messagebox.showinfo("Listo", f"{len(clips)} clips extraídos en {output_directory}"))

# Funciones GUI
def select_input_file():
    input_file = filedialog.askopenfilename(filetypes=[("Video Files", "*.mp4 *.mov *.mkv")])
    input_file_entry.delete(0, tk.END)
    input_file_entry.insert(0, input_file)

def select_output_directory():
    output_directory = filedialog.askdirectory()
    output_directory_entry.delete(0, tk.END)
    output_directory_entry.insert(0, output_directory)

def start_extraction():
    input_file = input_file_entry.get()
    num_clips = int(num_clips_entry.get())
    clip_duration = int(clip_duration_entry.get())
    output_directory = output_directory_entry.get()
    codec = codec_var.get()
    audio = audio_var.get()

    if not os.path.isfile(input_file):
        messagebox.showerror("Error", "Por favor seleccione un archivo válido.")
        return
    if not os.path.isdir(output_directory):
        messagebox.showerror("Error", "Por favor seleccione una carpeta de salida válida.")
        return

    # Reiniciar barra de progreso
    progress_bar['value'] = 0

    # Ejecutar extracción en un hilo separado
    threading.Thread(
        target=extract_random_clips,
        args=(input_file, num_clips, clip_duration, output_directory, codec, audio),
        daemon=True
    ).start()

# GUI
root = tk.Tk()
root.title("randomcutt")

tk.Label(root, text="Archivo de video:").pack()
input_file_entry = tk.Entry(root, width=50)
input_file_entry.pack()
tk.Button(root, text="Buscar", command=select_input_file).pack()

tk.Label(root, text="Número de clips:").pack()
num_clips_entry = tk.Entry(root)
num_clips_entry.pack()

tk.Label(root, text="Duración de cada clip (s):").pack()
clip_duration_entry = tk.Entry(root)
clip_duration_entry.pack()

tk.Label(root, text="Carpeta de salida:").pack()
output_directory_entry = tk.Entry(root, width=50)
output_directory_entry.pack()
tk.Button(root, text="Buscar", command=select_output_directory).pack()

tk.Label(root, text="Codec:").pack()
codec_var = tk.StringVar(root)
codec_var.set("hap")
codec_menu = tk.OptionMenu(root, codec_var, "hap", "prores_ks", "libx264", "copy")
codec_menu.pack()

audio_var = tk.BooleanVar()
tk.Checkbutton(root, text="Incluir audio", variable=audio_var).pack()

tk.Button(root, text="Extraer clips", command=start_extraction).pack()

# Barra de progreso
progress_bar = ttk.Progressbar(root, orient=tk.HORIZONTAL, length=500, mode='determinate')
progress_bar.pack(pady=10)

root.geometry("650x600")
root.mainloop()
