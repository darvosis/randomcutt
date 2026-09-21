import tkinter as tk
from tkinter import filedialog
from tkinter import messagebox
import moviepy.editor as mp
import random
import os

def extract_random_clips(input_file, num_clips, clip_duration, output_directory):
    video = mp.VideoFileClip(input_file)
    total_duration = video.duration
    clips = []

    file_name, file_extension = os.path.splitext(os.path.basename(input_file))

    for i in range(num_clips):
        random_start = random.uniform(0, total_duration - clip_duration)
        random_clip = video.subclip(random_start, random_start + clip_duration)
        clip_name = f"{file_name}_clip_{i + 1}.mp4"
        clip_path = os.path.join(output_directory, clip_name)
        clips.append(clip_path)
        random_clip.write_videofile(clip_path, codec="libx264")

    return clips

def select_input_file():
    input_file = filedialog.askopenfilename(filetypes=[("Video Files", "*.mp4 *.mkv")])
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

    if not os.path.isfile(input_file):
        messagebox.showerror("Error", "Please select a valid input file.")
        return

    if not os.path.isdir(output_directory):
        messagebox.showerror("Error", "Please select a valid output directory.")
        return

    clips = extract_random_clips(input_file, num_clips, clip_duration, output_directory)
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

extract_button = tk.Button(root, text="Extract Clips", command=extract_clips)
extract_button.pack()

root.mainloop()
