import tkinter as tk
from tkinter import filedialog, messagebox
import subprocess
import random
import os

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
randomness_var = None

# Función para extraer clips
def extract_random_clips(input_file, num_clips, clip_duration, output_directory, codec="hap", audio=False, randomness=1.0):
    # Obtener duración del video usando ffprobe
    cmd_duration = [
        ffprobe_path, "-v", "error", "-select_streams", "v:0",
        "-show_entries", "format=duration", "-of", "default=noprint_wrappers=1:nokey=1",
        input_file
    ]
    result = subprocess.run(cmd_duration, capture_output=True, text=True)
    try:
        total_duration = float(result.stdout.strip())
    except ValueError:
        messagebox.showerror("Error", "No se pudo leer la duración del video.")
        return []

    file_name, _ = os.path.splitext(os.path.basename(input_file))
    clips = []

    # Intervalo base para clips lineales
    linear_interval = (total_duration - clip_duration) / max(num_clips - 1, 1)

    for i in range(num_clips):
        # Calcular el inicio del clip según el grado de aleatoriedad
        linear_start = i * linear_interval
        if randomness == 0:
            random_start = linear_start
        else:
            # Mezcla lineal + aleatorio según el slider
            max_offset = linear_interval * randomness
            offset = random.uniform(-max_offset / 2, max_offset / 2)
            random_start = max(0, min(linear_start + offset, total_duration - clip_duration))

        clip_name = f"{file_name}_clip_{i+1}.mov" if codec in ["hap", "prores_ks"] else f"{file_name}_clip_{i+1}.mp4"
        clip_path = os.path.join(output_directory, clip_name)
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

        # Audio
        if audio:
            cmd += ["-c:a", "aac"]
        else:
            cmd += ["-an"]

        cmd.append(clip_path)

        # Ejecutar ffmpeg
        subprocess.run(cmd, text=True, check=True)

    return clips

# Funciones GUI
def select_input_file():
    input_file = filedialog.askopenfilename(filetypes=[("Video Files", "*.mp4 *.mov *.mkv")])
    input_file_entry.delete(0, tk.END)
    input_file_entry.insert(0, input_file)

def select_output_directory():
    output_directory = filedialog.askdirectory()
    output_directory_entry.delete(0, tk.END)
    output_directory_entry.insert(0, output_directory)

def extract_clips():
    input_file = input_file_entry.get()
    num_clips = int(num_clips_entry.get())
    clip_duration = int(clip_duration_entry.get())
    output_directory = output_directory_entry.get()
    codec = codec_var.get()
    audio = audio_var.get()
    randomness = float(randomness_var.get())

    if not os.path.isfile(input_file):
        messagebox.showerror("Error", "Por favor seleccione un archivo válido.")
        return
    if not os.path.isdir(output_directory):
        messagebox.showerror("Error", "Por favor seleccione una carpeta de salida válida.")
        return

    clips = extract_random_clips(input_file, num_clips, clip_duration, output_directory, codec=codec, audio=audio, randomness=randomness)
    messagebox.showinfo("Listo", f"{len(clips)} clips extraídos en {output_directory}")

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

tk.Label(root, text="Aleatoriedad de cortes (0 = lineal, 1 = random):").pack()
randomness_var = tk.DoubleVar()
randomness_var.set(1.0)
tk.Scale(root, variable=randomness_var, from_=0, to=1, resolution=0.01, orient=tk.HORIZONTAL, length=300).pack()

tk.Button(root, text="Extraer clips", command=extract_clips).pack()

root.geometry("650x550")
root.mainloop()
