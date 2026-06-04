#!/usr/bin/env python3
"""
Full pipeline: Compact video + Add subtitles.

Usage: python run_pipeline.py INPUT.mp4 [--output OUTPUT.mp4] [--language zh] [--prompt "Claude Code, DeepSeek"]
"""
import subprocess
import json
import os
import sys
import argparse
import tempfile
from pathlib import Path

SCRIPT_DIR = Path(__file__).parent

def run_step(name, cmd, **kwargs):
    print(f"\n{'='*50}", file=sys.stderr)
    print(f"Step: {name}", file=sys.stderr)
    print(f"{'='*50}", file=sys.stderr)
    result = subprocess.run(cmd, **kwargs)
    if result.returncode != 0:
        print(f"Failed: {name}", file=sys.stderr)
        sys.exit(1)
    return result

def main():
    parser = argparse.ArgumentParser(description="Video compact + subtitle pipeline")
    parser.add_argument("input", help="Input video file")
    parser.add_argument("--output", help="Output file (default: input_compact.mp4)")
    parser.add_argument("--language", default="zh", help="Language for ASR (default: zh)")
    parser.add_argument("--prompt", default="", help="Comma-separated technical terms for ASR")
    parser.add_argument("--whisper-model", default="medium", help="Whisper model size")
    parser.add_argument("--silence-threshold", default="-30dB", help="Silence detection threshold")
    parser.add_argument("--min-silence", type=float, default=0.3, help="Min silence duration")
    parser.add_argument("--font-size", type=int, default=18, help="Subtitle font size")
    parser.add_argument("--style", default="box", choices=["box", "outline", "minimal"], help="Subtitle style")
    parser.add_argument("--skip-compact", action="store_true", help="Skip compacting, only add subtitles")
    parser.add_argument("--compacted", help="Use existing compacted video")
    args = parser.parse_args()

    input_path = Path(args.input).resolve()
    work_dir = input_path.parent
    output_path = Path(args.output).resolve() if args.output else work_dir / f"{input_path.stem}_compact.mp4"

    # Temp files
    audio_wav = work_dir / "_audio.wav"
    compacted = Path(args.compacted).resolve() if args.compacted else work_dir / "_compacted.mp4"
    whisper_json = work_dir / "_whisper_result.json"
    srt_file = work_dir / "_subtitles.srt"

    try:
        # Step 1: Extract audio
        if not args.skip_compact:
            run_step("Extract Audio", [
                "ffmpeg", "-i", str(input_path), "-vn", "-acodec", "pcm_s16le",
                "-ar", "16000", "-ac", "1", str(audio_wav), "-y"
            ], capture_output=True)

            # Step 2: Detect silences
            print("\nDetecting silences...", file=sys.stderr)
            result = subprocess.run(
                [sys.executable, str(SCRIPT_DIR / "detect_silences.py"), str(input_path)],
                capture_output=True, text=True
            )
            silences = json.loads(result.stdout)
            print(f"Found {len(silences)} silence intervals", file=sys.stderr)

            # Step 3: Detect fillers (via DashScope ASR first for filler detection)
            print("\nDetecting filler words...", file=sys.stderr)
            # Use a simple approach: transcribe with Whisper and detect fillers
            run_step("Whisper Transcribe (for filler detection)", [
                sys.executable, "-c", f"""
import json
from faster_whisper import WhisperModel
model = WhisperModel("{args.whisper_model}", device="cpu", compute_type="int8")
segments, info = model.transcribe("{audio_wav}", language="{args.language}", word_timestamps=True, vad_filter=True)
result = {{"segments": []}}
for seg in segments:
    s = {{"start": seg.start, "end": seg.end, "text": seg.text, "words": []}}
    for w in seg.words or []:
        s["words"].append({{"start": w.start, "end": w.end, "word": w.word}})
    result["segments"].append(s)
with open("{whisper_json}", "w") as f:
    json.dump(result, f, ensure_ascii=False, indent=2)
"""
            ], capture_output=True)

            # Detect fillers from Whisper output
            result = subprocess.run(
                [sys.executable, str(SCRIPT_DIR / "detect_fillers.py"), str(whisper_json), "whisper"],
                capture_output=True, text=True
            )
            fillers = json.loads(result.stdout) if result.stdout.strip() else []
            print(f"Found {len(fillers)} filler words", file=sys.stderr)

            # Save fillers for compact script
            fillers_json = work_dir / "_fillers.json"
            with open(fillers_json, "w") as f:
                json.dump(fillers, f, ensure_ascii=False)

            # Step 4: Compact video
            run_step("Compact Video", [
                sys.executable, str(SCRIPT_DIR / "compact_video.py"),
                str(input_path), str(compacted),
                f"--silence-threshold={args.silence_threshold}",
                f"--min-silence={args.min_silence}",
                "--fillers-json", str(fillers_json),
            ])

        # Step 5: Re-transcribe compacted video with Whisper
        print("\nTranscribing compacted video...", file=sys.stderr)
        prompt = args.prompt or "Claude Code, CCSwitch, DeepSeek, VS Code, API Key, Kimi, MiniMax"
        run_step("Whisper Transcribe (compacted)", [
            sys.executable, "-c", f"""
import json
from faster_whisper import WhisperModel
model = WhisperModel("{args.whisper_model}", device="cpu", compute_type="int8")
segments, info = model.transcribe(
    "{compacted}", language="{args.language}", beam_size=5,
    word_timestamps=True, vad_filter=True,
    initial_prompt="{prompt}"
)
result = {{"segments": []}}
for seg in segments:
    s = {{"start": seg.start, "end": seg.end, "text": seg.text, "words": []}}
    for w in seg.words or []:
        s["words"].append({{"start": w.start, "end": w.end, "word": w.word}})
    result["segments"].append(s)
with open("{whisper_json}", "w") as f:
    json.dump(result, f, ensure_ascii=False, indent=2)
print(f"Segments: {{len(result['segments'])}}")
"""
        ], capture_output=True)

        # Step 6: Generate SRT (using word-level timestamps for precision)
        run_step("Generate SRT", [
            sys.executable, str(SCRIPT_DIR / "generate_srt.py"),
            str(whisper_json), str(srt_file),
            "--source", "whisper",
        ])

        # Step 7: Burn subtitles
        run_step("Burn Subtitles", [
            sys.executable, str(SCRIPT_DIR / "burn_subtitles.py"),
            str(compacted), str(srt_file), str(output_path),
            "--style", args.style,
            "--font-size", str(args.font_size),
        ])

        print(f"\n{'='*50}", file=sys.stderr)
        print(f"Done! Output: {output_path}", file=sys.stderr)
        print(f"{'='*50}", file=sys.stderr)

    finally:
        # Cleanup temp files
        for f in [audio_wav, whisper_json, srt_file]:
            if f.exists() and not args.compacted:
                f.unlink()
        # Keep compacted video if it was provided
        if not args.compacted and compacted.exists():
            compacted.unlink()
        # Cleanup fillers json
        fillers_json = work_dir / "_fillers.json"
        if fillers_json.exists():
            fillers_json.unlink()

if __name__ == "__main__":
    main()
