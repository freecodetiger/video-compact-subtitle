#!/usr/bin/env python3
"""Detect silence intervals in a video/audio file using ffmpeg."""
import subprocess
import re
import json
import sys

def detect_silences(input_file, noise_threshold="-30dB", min_duration=0.3):
    """Run ffmpeg silencedetect and return silence intervals."""
    cmd = [
        "ffmpeg", "-i", input_file,
        "-af", f"silencedetect=noise={noise_threshold}:d={min_duration}",
        "-f", "null", "-"
    ]
    result = subprocess.run(cmd, capture_output=True, text=True)
    output = result.stderr

    silences = []
    cur_start = None
    for line in output.split("\n"):
        start_match = re.search(r"silence_start:\s*([\d.]+)", line)
        end_match = re.search(r"silence_end:\s*([\d.]+)", line)
        if start_match:
            cur_start = float(start_match.group(1))
        if end_match and cur_start is not None:
            silences.append({"start": cur_start, "end": float(end_match.group(1))})
            cur_start = None

    # Handle trailing silence
    if cur_start is not None:
        probe = subprocess.run(
            ["ffprobe", "-v", "quiet", "-print_format", "json", "-show_format", input_file],
            capture_output=True, text=True
        )
        duration = float(json.loads(probe.stdout)["format"]["duration"])
        silences.append({"start": cur_start, "end": duration})

    return silences

if __name__ == "__main__":
    input_file = sys.argv[1] if len(sys.argv) > 1 else "input.mp4"
    silences = detect_silences(input_file)
    print(json.dumps(silences, indent=2))
    print(f"\nTotal: {len(silences)} silence intervals", file=sys.stderr)
