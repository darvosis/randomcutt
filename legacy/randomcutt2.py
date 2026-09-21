import os
import moviepy.editor as mp
import random


def extract_random_clips(input_file, num_clips, clip_duration, output_directory):
    video = mp.VideoFileClip(input_file)
    total_duration = video.duration
    clips = []

    file_name, file_extension = os.path.splitext(os.path.basename(input_file))

    for i in range(num_clips):
        random_start = random.uniform(0, total_duration - clip_duration)
        random_clip = video.subclip(random_start, random_start + clip_duration)
        clip_name = f"{file_name}_clip_{i + 1}.mov"
        output_file = os.path.join(output_directory, clip_name)
        random_clip.write_videofile(output_file, codec="hap")
        print(f"Clip {clip_name} guardado en: {output_file}")

input_file = "D:/proyectos/visuales/soft/randomcutt/Dragons Heaven.mkv"
output_directory = "D:/proyectos/visuales/soft/randomcutt/out"  # Ruta donde deseas guardar los archivos

extract_random_clips(input_file, 5, 5, output_directory)
