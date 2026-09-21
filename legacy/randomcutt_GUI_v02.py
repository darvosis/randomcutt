import tkinter as tk
from tkinter import filedialog
from tkinter import messagebox
import subprocess
import moviepy.editor as mp
import random
import os

# Declare input_file_entry and other entry variables as global variables
input_file_entry = None
num_clips_entry = None
clip_duration_entry = None
output_directory_entry = None
codec_var = None
audio_var = None

def extract_random_clips(input_file, num_clips, clip_duration, output_directory, codec="hap", audio=False):
    video = mp.VideoFileClip(input_file)
    total_duration = video.duration
    clips = []

    file_name, file_extension = os.path.splitext(os.path.basename(input_file))

    for i in range(num_clips):
        random_start = random.uniform(0, total_duration - clip_duration)
        random_clip = video.subclip(random_start, random_start + clip_duration)

        # Skip audio if audio=False
        if not audio:
            random_clip = random_clip.set_audio(None)

        # Customize codec and container
        clip_name = f"{file_name}_clip_{i + 1}.mov" if codec == "prores_ks" else f"{file_name}_clip_{i + 1}.mp4"
        clip_path = os.path.join(output_directory, clip_name)
        clips.append(clip_path)
        
        # Specify the codec and format options based on the chosen codec
        if codec == "prores_ks":
            random_clip.write_videofile(clip_path, codec=codec, audio_codec='pcm_s16le', preset='ultrafast', ffmpeg_params=['-q:v', '0'], write_options=['-movflags', 'faststart'])
        else:
            random_clip.write_videofile(clip_path, codec=codec, audio=audio)

    return clips

def get_supported_codecs():
    # Run ffmpeg command to get supported codecs
    result = subprocess.run(['ffmpeg', '-codecs'], capture_output=True, text=True)
    output_lines = result.stdout.split('\n')

    # Extract video codecs from the output
    supported_codecs = [line.split()[1] for line in output_lines if line.startswith(' D') and 'V' in line]

    return supported_codecs

def select_input_file():
    global input_file_entry
    input_file = filedialog.askopenfilename(filetypes=[("Video Files", "*.mp4 *.mkv")])
    input_file_entry.delete(0, tk.END)
    input_file_entry.insert(0, input_file)

def select_output_directory():
    global output_directory_entry
    output_directory = filedialog.askdirectory()
    output_directory_entry.delete(0, tk.END)
    output_directory_entry.insert(0, output_directory)

def extract_clips():
    global input_file_entry, num_clips_entry, clip_duration_entry, output_directory_entry, codec_var, audio_var
    input_file = input_file_entry.get()
    num_clips = int(num_clips_entry.get())
    clip_duration = int(clip_duration_entry.get())
    output_directory = output_directory_entry.get()
    codec = codec_var.get()
    audio = audio_var.get()

    if not os.path.isfile(input_file):
        messagebox.showerror("Error", "Please select a valid input file.")
        return

    if not os.path.isdir(output_directory):
        messagebox.showerror("Error", "Please select a valid output directory.")
        return

    clips = extract_random_clips(input_file, num_clips, clip_duration, output_directory, codec=codec, audio=audio)
    messagebox.showinfo("Extraction Complete", f"{len(clips)} clips extracted to {output_directory}")

# Create the main window
root = tk.Tk()
root.title("randomcutt")

# Create and configure input widgets
input_file_label = tk.Label(root, text="Input Video File:")
input_file_label.pack()
input_file_entry = tk.Entry(root)
input_file_entry.pack()
input_file_button = tk.Button(root, text="Browse", command=select_input_file)
input_file_button.pack()

num_clips_label = tk.Label(root, text="Number of Clips:")
num_clips_label.pack()
num_clips_entry = tk.Entry(root)
num_clips_entry.pack()

clip_duration_label = tk.Label(root, text="Clip Duration (seconds):")
clip_duration_label.pack()
clip_duration_entry = tk.Entry(root)
clip_duration_entry.pack()

output_directory_label = tk.Label(root, text="Output Directory:")
output_directory_label.pack()
output_directory_entry = tk.Entry(root)
output_directory_entry.pack()
output_directory_button = tk.Button(root, text="Browse", command=select_output_directory)
output_directory_button.pack()

# Add a dropdown menu for codec selection
codec_label = tk.Label(root, text="Video Codec:")
codec_label.pack()
supported_codecs = get_supported_codecs()
codec_var = tk.StringVar(root)
codec_var.set("hap")  # Set default codec to hap
codec_menu = tk.OptionMenu(root, codec_var, *supported_codecs)
codec_menu.pack()

# Add a checkbox for audio
audio_var = tk.BooleanVar()
audio_checkbox = tk.Checkbutton(root, text="Include Audio", variable=audio_var)
audio_checkbox.pack()

extract_button = tk.Button(root, text="Extract Clips", command=extract_clips)
extract_button.pack()

root.geometry("600x600")  # Set the initial size of the window

root.mainloop()
