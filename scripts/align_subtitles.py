#!/usr/bin/env python3
"""
Force-align subtitles using whisperX for precise timestamps.
Takes a compacted video + SRT file, outputs a new SRT with aligned timestamps.

Usage: python align_subtitles.py input.mp4 subtitles.srt output.srt [--language zh] [--whisper-model medium]
"""
import json
import re
import sys
import argparse
import whisperx
import tempfile
from pathlib import Path

def extract_text_from_srt(srt_file):
    """Parse SRT file and extract text entries."""
    entries = []
    with open(srt_file, "r", encoding="utf-8") as f:
        content = f.read()

    blocks = re.split(r'\n\s*\n', content.strip())
    for block in blocks:
        lines = block.strip().split('\n')
        if len(lines) >= 3:
            # lines[0] = index, lines[1] = timestamp, lines[2:] = text
            text = ' '.join(lines[2:]).strip()
            if text:
                entries.append({"text": text})
    return entries

def force_align(audio_file, srt_file, output_srt, language="zh", whisper_model="medium", device="cpu"):
    """
    Use whisperX forced alignment to get precise word-level timestamps.

    Process:
    1. Read existing SRT to get the correct text (already fixed)
    2. Run whisperX alignment on the audio using the known text
    3. Generate new SRT with aligned timestamps
    """
    print("Loading whisperX model...", file=sys.stderr)

    # Step 1: Load audio
    print("Loading audio...", file=sys.stderr)
    audio = whisperx.load_audio(audio_file)

    # Step 2: Run whisperX to get segments with word-level timestamps
    print("Running whisperX transcription...", file=sys.stderr)
    model = whisperx.load_model(whisper_model, device, compute_type="int8")
    result = model.transcribe(audio, language=language, batch_size=16)

    print(f"Got {len(result['segments'])} segments", file=sys.stderr)

    # Step 3: Run forced alignment for precise timestamps
    print("Running forced alignment...", file=sys.stderr)
    model_a, metadata = whisperx.load_align_model(language_code=language, device=device)
    aligned = whisperx.align(result["segments"], model_a, metadata, audio, device, return_char_alignments=False)

    print(f"Aligned {len(aligned['segments'])} segments", file=sys.stderr)

    # Step 4: Read existing SRT text (already corrected)
    srt_entries = extract_text_from_srt(srt_file)

    # Step 5: Build aligned SRT using whisperX timestamps + corrected text
    # Map aligned segments to SRT entries by order
    aligned_entries = []
    aligned_idx = 0

    for i, srt_entry in enumerate(srt_entries):
        srt_text = srt_entry["text"]

        # Find the best matching aligned segment
        best_match = None
        best_score = 0

        for j in range(aligned_idx, len(aligned["segments"])):
            seg = aligned["segments"][j]
            seg_text = seg.get("text", "").strip()

            # Calculate similarity (simple character overlap)
            common = sum(1 for c in srt_text if c in seg_text)
            score = common / max(len(srt_text), len(seg_text), 1)

            if score > best_score:
                best_score = score
                best_match = j

        if best_match is not None and best_score > 0.3:
            seg = aligned["segments"][best_match]
            # Use word-level timestamps for precision
            words = seg.get("words", [])
            if words:
                begin = words[0].get("start", seg.get("start", 0))
                end = words[-1].get("end", seg.get("end", 0))
            else:
                begin = seg.get("start", 0)
                end = seg.get("end", 0)

            aligned_entries.append({
                "begin": begin,
                "end": end,
                "text": srt_text,
            })
            aligned_idx = best_match + 1
        else:
            # Fallback: estimate from previous entry
            if aligned_entries:
                prev_end = aligned_entries[-1]["end"]
                char_count = len(srt_text)
                est_duration = char_count * 0.15  # ~150ms per character
                aligned_entries.append({
                    "begin": prev_end + 0.1,
                    "end": prev_end + 0.1 + est_duration,
                    "text": srt_text,
                })
            else:
                aligned_entries.append({
                    "begin": 0,
                    "end": len(srt_text) * 0.15,
                    "text": srt_text,
                })

    # Step 6: Write aligned SRT
    def format_srt_time(seconds):
        h = int(seconds // 3600)
        m = int((seconds % 3600) // 60)
        s = int(seconds % 60)
        ms = int(round((seconds % 1) * 1000))
        return f"{h:02d}:{m:02d}:{s:02d},{ms:03d}"

    with open(output_srt, "w", encoding="utf-8") as f:
        for i, entry in enumerate(aligned_entries, 1):
            f.write(f"{i}\n")
            f.write(f"{format_srt_time(entry['begin'])} --> {format_srt_time(entry['end'])}\n")
            f.write(f"{entry['text']}\n\n")

    print(f"Generated {len(aligned_entries)} aligned entries -> {output_srt}", file=sys.stderr)
    return aligned_entries

def force_align_with_existing_text(audio_file, srt_file, output_srt, language="zh", device="cpu"):
    """
    Alternative: Use existing SRT text directly for alignment (no re-transcription).
    This is more accurate when the text is already correct.
    """
    print("Loading audio and align model...", file=sys.stderr)
    audio = whisperx.load_audio(audio_file)
    model_a, metadata = whisperx.load_align_model(language_code=language, device=device)

    # Read SRT entries
    srt_entries = extract_text_from_srt(srt_file)

    # Create segments from SRT text (no timestamps needed for alignment)
    segments = [{"text": e["text"], "start": 0, "end": 0} for e in srt_entries]

    print(f"Aligning {len(segments)} segments...", file=sys.stderr)
    aligned = whisperx.align(segments, model_a, metadata, audio, device, return_char_alignments=False)

    # Build aligned SRT
    aligned_entries = []
    for i, seg in enumerate(aligned["segments"]):
        words = seg.get("words", [])
        if words:
            begin = words[0].get("start", 0)
            end = words[-1].get("end", 0)
        else:
            begin = seg.get("start", 0)
            end = seg.get("end", 0)

        aligned_entries.append({
            "begin": begin,
            "end": end,
            "text": srt_entries[i]["text"] if i < len(srt_entries) else seg.get("text", ""),
        })

    # Write aligned SRT
    def format_srt_time(seconds):
        h = int(seconds // 3600)
        m = int((seconds % 3600) // 60)
        s = int(seconds % 60)
        ms = int(round((seconds % 1) * 1000))
        return f"{h:02d}:{m:02d}:{s:02d},{ms:03d}"

    with open(output_srt, "w", encoding="utf-8") as f:
        for i, entry in enumerate(aligned_entries, 1):
            f.write(f"{i}\n")
            f.write(f"{format_srt_time(entry['begin'])} --> {format_srt_time(entry['end'])}\n")
            f.write(f"{entry['text']}\n\n")

    print(f"Generated {len(aligned_entries)} aligned entries -> {output_srt}", file=sys.stderr)
    return aligned_entries

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Force-align subtitles using whisperX")
    parser.add_argument("input", help="Input audio/video file")
    parser.add_argument("srt", help="Input SRT file (with corrected text)")
    parser.add_argument("output", help="Output aligned SRT file")
    parser.add_argument("--language", default="zh", help="Language code")
    parser.add_argument("--whisper-model", default="medium", help="Whisper model for transcription")
    parser.add_argument("--device", default="cpu", help="Device (cpu/cuda)")
    parser.add_argument("--mode", choices=["transcribe", "direct"], default="direct",
                        help="transcribe: re-transcribe then align; direct: align existing text directly")
    args = parser.parse_args()

    if args.mode == "direct":
        force_align_with_existing_text(args.input, args.srt, args.output, args.language, args.device)
    else:
        force_align(args.input, args.srt, args.output, args.language, args.whisper_model, args.device)
