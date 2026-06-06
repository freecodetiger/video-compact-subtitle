#!/usr/bin/env python3
"""
Full pipeline: Compact video + Add subtitles.

Usage: python run_pipeline.py INPUT.mp4 [--output OUTPUT.mp4] [--language zh] [--prompt "Claude Code, DeepSeek"]
"""
import subprocess
import json
import os
import re
import sys
import argparse
import tempfile
from pathlib import Path

SCRIPT_DIR = Path(__file__).parent


def resolve_model():
    """探测可用 API 模型，返回第一个能用的，或 None。"""
    try:
        import anthropic
    except ImportError:
        print("⚠️  anthropic 未安装，跳过 API 文本修正", file=sys.stderr)
        return None

    api_key = os.environ.get("ANTHROPIC_API_KEY") or os.environ.get("ANTHROPIC_AUTH_TOKEN")
    base_url = os.environ.get("ANTHROPIC_BASE_URL")
    if not api_key:
        return None

    client = anthropic.Anthropic(api_key=api_key, base_url=base_url)
    env_model = os.environ.get("ANTHROPIC_MODEL", "")
    cleaned = re.sub(r'\[.*?\]', '', env_model).strip() if env_model else ""
    candidates = [m for m in [cleaned, "claude-haiku-4-5-20251001", "claude-3-5-haiku-20241022"] if m]
    for model in candidates:
        try:
            client.messages.create(model=model, max_tokens=10,
                                   messages=[{"role": "user", "content": "hi"}])
            print(f"  API model: {model}", file=sys.stderr)
            return model
        except Exception:
            continue
    print("⚠️  API 模型不可用，跳过文本修正", file=sys.stderr)
    return None

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
    work_dir = input_path.parent / "compact_work"
    work_dir.mkdir(exist_ok=True)
    output_path = Path(args.output).resolve() if args.output else input_path.parent / f"{input_path.stem}_压缩字幕.mp4"

    # Temp files
    audio_wav = work_dir / "audio.wav"
    compacted = Path(args.compacted).resolve() if args.compacted else work_dir / "compacted.mp4"
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

        # Step 6: Fix transcript with API (optional, degrades gracefully)
        model_name = resolve_model()
        if model_name:
            print("\nFixing transcript with API...", file=sys.stderr)
            try:
                import anthropic
                client = anthropic.Anthropic(
                    api_key=os.environ.get("ANTHROPIC_API_KEY") or os.environ.get("ANTHROPIC_AUTH_TOKEN"),
                    base_url=os.environ.get("ANTHROPIC_BASE_URL"),
                )
                with open(whisper_json) as f:
                    whisper_data = json.load(f)
                segs = whisper_data["segments"]
                system = """你是字幕修正助手。修正技术名词错误、不通顺表达、残留填充词。
不要改变语义，不要添加内容，不要改变时间戳。每段一行，保持原顺序，不要加编号。"""
                batch_size = 20
                for i in range(0, len(segs), batch_size):
                    batch = segs[i:i+batch_size]
                    lines = [f"[{i+j+1}] {s['text']}" for j, s in enumerate(batch)]
                    try:
                        resp = client.messages.create(
                            model=model_name, max_tokens=4096, system=system,
                            messages=[{"role": "user", "content": "\n".join(lines)}]
                        )
                        result = resp.content[0].text.strip().split("\n")
                        for k, s in enumerate(batch):
                            if k < len(result):
                                corrected = re.sub(r'^\[\d+\]\s*', '', result[k].strip())
                                corrected = re.sub(r'^\d+\.\s*', '', corrected)
                                if corrected:
                                    s['text'] = corrected
                    except Exception as e:
                        print(f"  ⚠️  Batch {i//batch_size+1} failed: {e}", file=sys.stderr)
                with open(whisper_json, "w") as f:
                    json.dump(whisper_data, f, ensure_ascii=False, indent=2)
                print(f"  Fixed {len(segs)} segments", file=sys.stderr)
            except Exception as e:
                print(f"  ⚠️  API fix skipped: {e}", file=sys.stderr)

        # Step 7: Generate SRT (using word-level timestamps for precision)
        run_step("Generate SRT", [
            sys.executable, str(SCRIPT_DIR / "generate_srt.py"),
            str(whisper_json), str(srt_file),
            "--source", "whisper",
        ])

        # Step 8: Burn subtitles
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
        for f in [audio_wav, whisper_json]:
            if f.exists() and not args.compacted:
                f.unlink()
        filler_json = work_dir / "filler_result.json"
        if filler_json.exists():
            filler_json.unlink()
        fillers_json = work_dir / "_fillers.json"
        if fillers_json.exists():
            fillers_json.unlink()

if __name__ == "__main__":
    main()
