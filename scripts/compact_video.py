#!/usr/bin/env python3
"""
Compact a video by removing silences and filler words.
Usage: python compact_video.py INPUT.mp4 OUTPUT.mp4 [--silence-threshold -30dB] [--min-silence 0.3] [--merge-gap 0.15]
"""
import subprocess
import re
import json
import os
import sys
import argparse
from pathlib import Path

def get_duration(input_file):
    probe = subprocess.run(
        ["ffprobe", "-v", "quiet", "-print_format", "json", "-show_format", input_file],
        capture_output=True, text=True
    )
    return float(json.loads(probe.stdout)["format"]["duration"])

def detect_silences(input_file, noise="-30dB", min_dur=0.3):
    cmd = ["ffmpeg", "-i", input_file, "-af", f"silencedetect=noise={noise}:d={min_dur}", "-f", "null", "-"]
    result = subprocess.run(cmd, capture_output=True, text=True)
    silences = []
    cur = None
    for line in result.stderr.split("\n"):
        m = re.search(r"silence_start:\s*([\d.]+)", line)
        if m: cur = float(m.group(1))
        m = re.search(r"silence_end:\s*([\d.]+)", line)
        if m and cur is not None:
            silences.append({"start": cur, "end": float(m.group(1))})
            cur = None
    if cur is not None:
        silences.append({"start": cur, "end": get_duration(input_file)})
    return silences

def merge_intervals(intervals, gap=0.0):
    if not intervals: return []
    intervals.sort(key=lambda x: x["start"])
    merged = [intervals[0].copy()]
    for iv in intervals[1:]:
        if iv["start"] <= merged[-1]["end"] + gap:
            merged[-1]["end"] = max(merged[-1]["end"], iv["end"])
        else:
            merged.append(iv.copy())
    return merged

def invert_intervals(remove_list, total_duration):
    """Get speech intervals (inverse of remove intervals)."""
    speeches = []
    pos = 0
    for iv in remove_list:
        if iv["start"] > pos + 0.01:
            speeches.append({"start": pos, "end": iv["start"]})
        pos = iv["end"]
    if pos < total_duration - 0.01:
        speeches.append({"start": pos, "end": total_duration})
    return speeches

def compact_video(input_file, output_file, silences, fillers, merge_gap=0.15):
    """Cut and concat video segments."""
    # Merge all remove intervals
    all_remove = silences + [{"start": f["start"], "end": f["end"]} for f in fillers]
    all_remove = merge_intervals(all_remove)

    duration = get_duration(input_file)
    speeches = invert_intervals(all_remove, duration)
    speeches = merge_intervals(speeches, gap=merge_gap)

    total_speech = sum(s["end"] - s["start"] for s in speeches)
    print(f"Speech segments: {len(speeches)}, Duration: {total_speech:.1f}s", file=sys.stderr)

    # Create temp directory for segments
    tmp_dir = Path(output_file).parent / "_segments"
    tmp_dir.mkdir(exist_ok=True)

    # Generate concat list
    concat_list = tmp_dir / "concat.txt"
    with open(concat_list, "w") as f:
        for i in range(len(speeches)):
            f.write(f"file '{tmp_dir}/seg_{i:04d}.ts'\n")

    # Cut segments in parallel batches
    batch_size = 8
    for batch_start in range(0, len(speeches), batch_size):
        procs = []
        for i in range(batch_start, min(batch_start + batch_size, len(speeches))):
            s = speeches[i]
            dur = s["end"] - s["start"]
            out = tmp_dir / f"seg_{i:04d}.ts"
            cmd = [
                "ffmpeg", "-y", "-ss", f"{s['start']:.3f}", "-i", input_file,
                "-t", f"{dur:.3f}", "-c:v", "libx264", "-preset", "fast", "-crf", "18",
                "-c:a", "aac", "-b:a", "128k", "-avoid_negative_ts", "make_zero",
                str(out)
            ]
            procs.append(subprocess.Popen(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL))
        for p in procs:
            p.wait()

    # Concat
    subprocess.run(
        ["ffmpeg", "-y", "-f", "concat", "-safe", "0", "-i", str(concat_list), "-c", "copy", output_file],
        check=True, capture_output=True
    )

    # Cleanup
    import shutil
    shutil.rmtree(tmp_dir, ignore_errors=True)

    return speeches, total_speech

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Compact video by removing silences and fillers")
    parser.add_argument("input", help="Input video file")
    parser.add_argument("output", help="Output compacted video file")
    parser.add_argument("--silence-threshold", default="-30dB", help="Silence detection threshold")
    parser.add_argument("--min-silence", type=float, default=0.3, help="Min silence duration in seconds")
    parser.add_argument("--merge-gap", type=float, default=0.15, help="Merge gap for speech segments")
    parser.add_argument("--fillers-json", help="JSON file with filler word intervals")
    args = parser.parse_args()

    print("Detecting silences...", file=sys.stderr)
    silences = detect_silences(args.input, args.silence_threshold, args.min_silence)
    print(f"Found {len(silences)} silence intervals", file=sys.stderr)

    fillers = []
    if args.fillers_json and os.path.exists(args.fillers_json):
        with open(args.fillers_json) as f:
            fillers = json.load(f)
        print(f"Loaded {len(fillers)} filler intervals", file=sys.stderr)

    print("Compacting video...", file=sys.stderr)
    speeches, duration = compact_video(args.input, args.output, silences, fillers, args.merge_gap)
    print(f"Done! Output: {args.output} ({duration:.1f}s)", file=sys.stderr)
