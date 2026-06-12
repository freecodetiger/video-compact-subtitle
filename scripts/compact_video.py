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
import tempfile
from pathlib import Path

def get_duration(input_file):
    probe = subprocess.run(
        ["ffprobe", "-v", "quiet", "-print_format", "json", "-show_format", input_file],
        capture_output=True, text=True
    )
    return float(json.loads(probe.stdout)["format"]["duration"])

def get_media_probe(input_file):
    probe = subprocess.run(
        [
            "ffprobe", "-v", "error",
            "-show_entries",
            "format=start_time,duration:stream=index,codec_type,codec_name,start_time,duration,r_frame_rate,avg_frame_rate",
            "-of", "json", input_file,
        ],
        capture_output=True, text=True, check=True
    )
    return json.loads(probe.stdout)

def parse_rate(rate):
    if not rate or rate == "0/0":
        return 30.0
    if "/" in rate:
        num, den = rate.split("/", 1)
        den_f = float(den)
        return float(num) / den_f if den_f else 30.0
    return float(rate)

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

def coalesce_to_max_segments(intervals, max_segments):
    """Merge adjacent kept intervals across the smallest gaps until the cut count is safe."""
    if not max_segments or len(intervals) <= max_segments:
        return intervals
    merged = [iv.copy() for iv in intervals]
    while len(merged) > max_segments:
        best_i = min(
            range(len(merged) - 1),
            key=lambda i: merged[i + 1]["start"] - merged[i]["end"],
        )
        merged[best_i]["end"] = merged[best_i + 1]["end"]
        del merged[best_i + 1]
    return merged

def build_speech_intervals(remove_intervals, total_duration, pre_roll=0.08, post_roll=0.12,
                           merge_gap=0.5, max_segments=80):
    """Build a conservative edit decision list from removal intervals."""
    remove_intervals = merge_intervals([
        {"start": max(0.0, float(iv["start"])), "end": min(total_duration, float(iv["end"]))}
        for iv in remove_intervals
        if float(iv.get("end", 0)) > float(iv.get("start", 0))
    ])

    speeches = invert_intervals(remove_intervals, total_duration)
    speeches = [
        {
            "start": max(0.0, s["start"] - pre_roll),
            "end": min(total_duration, s["end"] + post_roll),
        }
        for s in speeches
        if s["end"] > s["start"]
    ]
    speeches = merge_intervals(speeches, gap=merge_gap)
    speeches = coalesce_to_max_segments(speeches, max_segments)
    for s in speeches:
        s["duration"] = s["end"] - s["start"]
    return speeches

def build_select_filter_script(speeches, fps=30.0):
    expr = "+".join(
        f"between(t\\,{s['start']:.6f}\\,{s['end']:.6f})"
        for s in speeches
    )
    return (
        f"[0:v]select='{expr}',setpts=N/{fps:.6f}/TB[v];"
        f"[0:a]aselect='{expr}',asetpts=N/SR/TB,aresample=async=1:first_pts=0[a]\n"
    )

def analyze_timeline(probe, expected_duration=None, tolerance=0.75):
    streams = probe.get("streams", [])
    video = next((s for s in streams if s.get("codec_type") == "video"), None)
    audio = next((s for s in streams if s.get("codec_type") == "audio"), None)
    failures = []

    def as_float(obj, key):
        try:
            return float(obj.get(key))
        except Exception:
            return None

    video_start = as_float(video or {}, "start_time")
    audio_start = as_float(audio or {}, "start_time")
    video_duration = as_float(video or {}, "duration")
    audio_duration = as_float(audio or {}, "duration")
    format_duration = as_float(probe.get("format", {}), "duration")

    if video_start is None or abs(video_start) > 0.05:
        failures.append("video_start_time")
    if audio_start is None or abs(audio_start) > 0.05:
        failures.append("audio_start_time")
    if video_duration is not None and audio_duration is not None:
        if abs(video_duration - audio_duration) > tolerance:
            failures.append("av_duration_delta")
    if expected_duration is not None:
        actual = audio_duration or format_duration
        if actual is None or abs(actual - expected_duration) > tolerance:
            failures.append("expected_duration_delta")

    return {
        "ok": not failures,
        "failures": failures,
        "expected_duration": expected_duration,
        "format_duration": format_duration,
        "video_start": video_start,
        "audio_start": audio_start,
        "video_duration": video_duration,
        "audio_duration": audio_duration,
    }

def render_with_select_filter(input_file, output_file, speeches):
    probe = get_media_probe(input_file)
    video = next((s for s in probe.get("streams", []) if s.get("codec_type") == "video"), {})
    fps = parse_rate(video.get("avg_frame_rate") or video.get("r_frame_rate"))
    script = build_select_filter_script(speeches, fps=fps)

    with tempfile.NamedTemporaryFile("w", suffix=".ffscript", delete=False) as f:
        f.write(script)
        script_path = f.name
    try:
        cmd = [
            "ffmpeg", "-hide_banner", "-y", "-i", input_file,
            "-filter_complex_script", script_path,
            "-map", "[v]", "-map", "[a]",
            "-c:v", "libx264", "-preset", "fast", "-crf", "18",
            "-pix_fmt", "yuv420p",
            "-c:a", "aac", "-b:a", "160k",
            "-movflags", "+faststart",
            output_file,
        ]
        subprocess.run(cmd, check=True)
    finally:
        try:
            os.unlink(script_path)
        except OSError:
            pass

def render_with_segment_concat(input_file, output_file, speeches):
    """Cut and concat video segments."""
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

def compact_video(input_file, output_file, silences, fillers, merge_gap=0.5, max_segments=80,
                  pre_roll=0.08, post_roll=0.12, strategy="auto", tolerance=0.75):
    """Compact video while keeping a single trustworthy output timeline."""
    all_remove = silences + [{"start": f["start"], "end": f["end"]} for f in fillers]
    duration = get_duration(input_file)
    speeches = build_speech_intervals(
        all_remove,
        duration,
        pre_roll=pre_roll,
        post_roll=post_roll,
        merge_gap=merge_gap,
        max_segments=max_segments if strategy != "segments" else None,
    )

    total_speech = sum(s["end"] - s["start"] for s in speeches)
    print(f"Speech segments: {len(speeches)}, Duration: {total_speech:.1f}s", file=sys.stderr)

    if strategy in ("auto", "filter"):
        render_with_select_filter(input_file, output_file, speeches)
    elif strategy == "segments":
        print("Warning: segment concat is legacy and can drift on many cuts.", file=sys.stderr)
        render_with_segment_concat(input_file, output_file, speeches)
    else:
        raise ValueError(f"Unknown strategy: {strategy}")

    probe = get_media_probe(output_file)
    report = analyze_timeline(probe, expected_duration=total_speech, tolerance=tolerance)
    report_file = Path(output_file).with_suffix(".timeline.json")
    report_file.write_text(json.dumps(report, ensure_ascii=False, indent=2))
    if not report["ok"]:
        raise RuntimeError(f"Timeline verification failed: {report['failures']} (see {report_file})")

    return speeches, total_speech

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Compact video by removing silences and fillers")
    parser.add_argument("input", help="Input video file")
    parser.add_argument("output", help="Output compacted video file")
    parser.add_argument("--silence-threshold", default="-30dB", help="Silence detection threshold")
    parser.add_argument("--min-silence", type=float, default=0.3, help="Min silence duration in seconds")
    parser.add_argument("--merge-gap", type=float, default=0.5, help="Merge nearby kept speech gaps to reduce risky cuts")
    parser.add_argument("--max-segments", type=int, default=80, help="Maximum kept segments before coalescing smallest gaps")
    parser.add_argument("--pre-roll", type=float, default=0.08, help="Seconds to keep before each speech interval")
    parser.add_argument("--post-roll", type=float, default=0.12, help="Seconds to keep after each speech interval")
    parser.add_argument("--strategy", choices=["auto", "filter", "segments"], default="auto",
                        help="auto/filter uses one re-encode filter graph; segments keeps the legacy TS concat path")
    parser.add_argument("--timeline-tolerance", type=float, default=0.75, help="Allowed timeline duration drift in seconds")
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
    speeches, duration = compact_video(
        args.input, args.output, silences, fillers,
        merge_gap=args.merge_gap,
        max_segments=args.max_segments,
        pre_roll=args.pre_roll,
        post_roll=args.post_roll,
        strategy=args.strategy,
        tolerance=args.timeline_tolerance,
    )
    print(f"Done! Output: {args.output} ({duration:.1f}s)", file=sys.stderr)
