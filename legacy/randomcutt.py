import moviepy.editor as mp
import random
import os


def extract_random_clips(input_file, num_clips, clip_duration):
    video = mp.VideoFileClip(input_file)
    total_duration = video.duration
    clips = []
    
    file_name, file_extension = os.path.splitext(os.path.basename(input_file))

    for i in range(num_clips):
        random_start = random.uniform(0, total_duration - clip_duration)
        random_clip = video.subclip(random_start, random_start + clip_duration)
        clip_name = f"{file_name}_clip_{i + 1}.mp4"
        clips.append((clip_name, random_clip))

    return clips

input_file = "D:/proyectos/visuales/videos/pelis/DeadAlive/DeadAlive.mp4" #"D:/proyectos/visuales/videos/anime/NinjaScroll.mkv" #"D:/proyectos/visuales/videos/pelis/Suspiria/Suspiria.mp4"

clips = extract_random_clips(input_file, 90, 5)
output_directory = "D:/proyectos/visuales/videos/pelis/DeadAlive/" #"D:/proyectos/visuales/videos/anime/" #"D:/proyectos/visuales/videos/pelis/BlackSabbath/"

for i, (clip_name, clip) in enumerate(clips):
    output_file = os.path.join(output_directory, clip_name)
    clip.write_videofile(output_file, codec="libx264")
#libx264