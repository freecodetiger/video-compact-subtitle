#!/usr/bin/env python3
"""
Burn SRT subtitles into a video with configurable style.

Usage: python burn_subtitles.py input.mp4 subtitles.srt output.mp4 [--font-size 18] [--style box]
"""
import subprocess
import sys
import argparse

STYLES = {
    "box": {
        "desc": "Semi-transparent black background, no outline",
        "force_style": (
            "FontSize={font_size},"
            "PrimaryColour=&H00FFFFFF,"
            "BackColour=&H80000000,"
            "BorderStyle=4,"
            "Outline=0,"
            "Shadow=0,"
            "MarginV=30,"
            "Alignment=2"
        ),
    },
    "outline": {
        "desc": "White text with black outline",
        "force_style": (
            "FontSize={font_size},"
            "PrimaryColour=&H00FFFFFF,"
            "OutlineColour=&H00000000,"
            "Outline=2,"
            "Shadow=1,"
            "MarginV=30,"
            "Alignment=2"
        ),
    },
    "minimal": {
        "desc": "White text, no outline or background",
        "force_style": (
            "FontSize={font_size},"
            "PrimaryColour=&H00FFFFFF,"
            "Outline=0,"
            "Shadow=0,"
            "MarginV=30,"
            "Alignment=2"
        ),
    },
}

def burn_subtitles(input_video, srt_file, output_video, style="box", font_size=18, crf=18):
    style_config = STYLES.get(style, STYLES["box"])
    force_style = style_config["force_style"].format(font_size=font_size)

    vf = f"subtitles={srt_file}:force_style='{force_style}'"

    cmd = [
        "ffmpeg", "-i", input_video,
        "-vf", vf,
        "-c:v", "libx264", "-preset", "fast", "-crf", str(crf),
        "-c:a", "copy",
        "-y", output_video
    ]

    print(f"Burning subtitles with style '{style}'...", file=sys.stderr)
    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        print(f"Error: {result.stderr}", file=sys.stderr)
        sys.exit(1)
    print(f"Done! Output: {output_video}", file=sys.stderr)

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Burn subtitles into video")
    parser.add_argument("input", help="Input video file")
    parser.add_argument("srt", help="SRT subtitle file")
    parser.add_argument("output", help="Output video file")
    parser.add_argument("--style", choices=["box", "outline", "minimal"], default="box", help="Subtitle style")
    parser.add_argument("--font-size", type=int, default=18, help="Font size")
    parser.add_argument("--crf", type=int, default=18, help="Video quality (lower = better)")
    args = parser.parse_args()

    burn_subtitles(args.input, args.srt, args.output, args.style, args.font_size, args.crf)
