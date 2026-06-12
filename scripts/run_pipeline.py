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
    parser.add_argument("--merge-gap", type=float, default=0.5, help="Merge nearby kept speech gaps to reduce risky cuts")
    parser.add_argument("--max-segments", type=int, default=80, help="Maximum kept speech segments before coalescing")
    parser.add_argument("--pre-roll", type=float, default=0.08, help="Seconds to retain before each speech interval")
    parser.add_argument("--post-roll", type=float, default=0.12, help="Seconds to retain after each speech interval")
    parser.add_argument("--font-size", type=int, default=18, help="Subtitle font size")
    parser.add_argument("--style", default="box", choices=["box", "outline", "minimal"], help="Subtitle style")
    parser.add_argument("--skip-compact", action="store_true", help="Skip compacting, only add subtitles")
    parser.add_argument("--compacted", help="Use existing compacted video")
    parser.add_argument("--keep-artifacts", action="store_true", help="Keep intermediate audio, transcript, SRT, and compacted master for debugging")
    args = parser.parse_args()

    input_path = Path(args.input).resolve()
    work_dir = input_path.parent / "compact_work"
    work_dir.mkdir(exist_ok=True)
    output_path = Path(args.output).resolve() if args.output else input_path.parent / f"{input_path.stem}_压缩字幕.mp4"

    # Temp files
    audio_wav = work_dir / "audio.wav"
    compacted_audio_wav = work_dir / "compacted_audio.wav"
    if args.compacted:
        compacted = Path(args.compacted).resolve()
        created_compacted = False
    elif args.skip_compact:
        compacted = input_path
        created_compacted = False
    else:
        compacted = work_dir / "compacted.mp4"
        created_compacted = True
    whisper_json = work_dir / "whisper_result.json"
    srt_file = input_path.parent / "subtitles.srt"  # SRT 保留在输入目录方便用户查看

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

            # Step 3: Detect fillers (用 tiny 模型，速度快，填充词识别够用)
            print("\nDetecting filler words (tiny model)...", file=sys.stderr)
            filler_json = work_dir / "filler_result.json"
            run_step("Whisper Transcribe (tiny, for filler detection)", [
                sys.executable, "-c", f"""
import json
from faster_whisper import WhisperModel
model = WhisperModel("tiny", device="cpu", compute_type="int8")
segments, info = model.transcribe("{audio_wav}", language="{args.language}", word_timestamps=True, vad_filter=False)
result = {{"segments": []}}
for seg in segments:
    s = {{"start": seg.start, "end": seg.end, "text": seg.text, "words": []}}
    for w in seg.words or []:
        s["words"].append({{"start": w.start, "end": w.end, "word": w.word}})
    result["segments"].append(s)
with open("{filler_json}", "w") as f:
    json.dump(result, f, ensure_ascii=False, indent=2)
"""
            ], capture_output=True)

            # Detect fillers from Whisper output
            result = subprocess.run(
                [sys.executable, str(SCRIPT_DIR / "detect_fillers.py"), str(filler_json), "whisper"],
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
                f"--merge-gap={args.merge_gap}",
                f"--max-segments={args.max_segments}",
                f"--pre-roll={args.pre_roll}",
                f"--post-roll={args.post_roll}",
            ])

        # Step 5: Re-transcribe compacted audio with Whisper.
        # Use the final compacted audio timeline and keep VAD off to avoid hidden remapping.
        run_step("Extract Compacted Audio", [
            "ffmpeg", "-i", str(compacted), "-vn", "-acodec", "pcm_s16le",
            "-ar", "16000", "-ac", "1", str(compacted_audio_wav), "-y"
        ], capture_output=True)

        print("\nTranscribing compacted audio...", file=sys.stderr)
        prompt = args.prompt or "Claude Code, CCSwitch, DeepSeek, VS Code, API Key, Kimi, MiniMax"
        run_step("Whisper Transcribe (compacted)", [
            sys.executable, "-c", f"""
import json
from faster_whisper import WhisperModel
model = WhisperModel("{args.whisper_model}", device="cpu", compute_type="int8")
segments, info = model.transcribe(
    "{compacted_audio_wav}", language="{args.language}", beam_size=5,
    word_timestamps=True, vad_filter=False,
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
        # 注意：通过 Claude Code 会话运行时，Step 7 (文本修正) 由 Claude 直接完成
        # 独立运行时，转录文本未经修正，可手动编辑 subtitles.srt 后重新烧录
        run_step("Generate SRT", [
            sys.executable, str(SCRIPT_DIR / "generate_srt.py"),
            str(whisper_json), str(srt_file),
            "--source", "whisper",
            "--max-line-chars", "18",
            "--max-duration", "3.5",
            "--start-offset", "0.02",
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
        # Cleanup temp files (保留 compacted.mp4 和 subtitles.srt 供用户复用)
        cleanup_files = [audio_wav, compacted_audio_wav, whisper_json]
        if created_compacted:
            cleanup_files.append(compacted.with_suffix(".timeline.json"))
        for f in cleanup_files:
            if f.exists() and not args.compacted and not args.keep_artifacts:
                f.unlink()
        filler_json = work_dir / "filler_result.json"
        if filler_json.exists():
            filler_json.unlink()
        fillers_json = work_dir / "_fillers.json"
        if fillers_json.exists():
            fillers_json.unlink()

if __name__ == "__main__":
    main()
