#!/usr/bin/env python3
"""
Generate SRT subtitles from Whisper or DashScope ASR output.
Uses word-level timestamps for precise timing.
Supports technical term fixes and semantic splitting (max 2 clauses per subtitle).

Usage: python generate_srt.py whisper_result.json subtitles.srt [--fixes fixes.json]
"""
import json
import re
import sys
import argparse
import os

DEFAULT_FIXES = [
    ["Claw Code", "Claude Code"],
    ["Claw Mini", "cc命令"],
    ["Claw", "Claude"],
    ["DeepSig", "DeepSeek"],
    ["Ancetropic", "Anthropic"],
    ["GBT5.4", "Claude 3.5 Sonnet"],
    ["GBT5.2", "GPT-4o"],
    ["Drip", "智谱"],
    ["Mini Key", "API Key"],
    ["FindSkills", "find skill"],
    ["AskAgainForSkills", "don't ask again for skill"],
    ["Dangerously-Scape-Permissions", "dangerously-skip-permissions"],
]

FILLERS = ["嗯","啊","呃","额","哦","噢","哎","唉","哈","呵","呢","吧","呀","嘛","喂","嗨","嘿","哇"]

def fix_text(text, fixes):
    t = text
    for frm, to in fixes:
        t = t.replace(frm, to)
    t = re.sub(r',+', '，', t)
    t = re.sub(r'，{2,}', '，', t)
    return t.strip()

def filter_fillers(text):
    t = text
    for f in FILLERS:
        t = re.sub(f'(^|[，。、？！\\s]){f}[，。、？！\\s]*', r'\1', t)
        t = re.sub(f'^[{f}]+[，。、？！\\s]*', '', t)
    t = re.sub(r'^[，、\s]+', '', t)
    t = re.sub(r'[，、\s]+$', '', t)
    return t.strip()

def split_to_chunks(text):
    """Split by commas/periods, merge every 2 clauses."""
    parts = [s for s in re.split(r'(?<=[，。？！])', text) if s.strip()]
    chunks = []
    i = 0
    while i < len(parts):
        if i + 1 < len(parts):
            chunks.append((parts[i] + parts[i+1]).strip())
            i += 2
        else:
            chunks.append(parts[i].strip())
            i += 1
    return [c for c in chunks if c]

def visual_len(text):
    """Approximate rendered width: Chinese chars are full width, ASCII is narrower."""
    total = 0
    for ch in text:
        if ch == " ":
            total += 0.5
        elif ord(ch) < 128:
            total += 0.6
        else:
            total += 1
    return total

def split_two_lines(text, max_line_chars=18):
    text = text.strip()
    if visual_len(text) <= max_line_chars:
        return text

    best = None
    for i in range(1, len(text)):
        left, right = text[:i], text[i:]
        overflow = max(visual_len(left) - max_line_chars, 0) + max(visual_len(right) - max_line_chars, 0)
        score = overflow * 100 + abs(visual_len(left) - visual_len(right))
        if left[-1] in "的了在和与及是":
            score += 2
        if best is None or score < best[0]:
            best = (score, i)
    i = best[1]
    return text[:i] + "\n" + text[i:]

def collect_whisper_words(data, fixes):
    words = []
    for seg in data.get("segments", []):
        for w in seg.get("words", []) or []:
            text = fix_text(w.get("word", ""), fixes).strip()
            if not text:
                continue
            start = w.get("start")
            end = w.get("end")
            if start is None or end is None or end < start:
                continue
            words.append({"start": float(start), "end": float(end), "text": text})
    return words

def generate_constrained_entries(data, fixes, max_chars=30, max_line_chars=18,
                                 max_duration=3.5, min_duration=0.7,
                                 start_offset=0.02, end_padding=0.12,
                                 gap_break=0.55):
    """
    Generate display-constrained SRT entries directly from word timestamps.
    Starts are never moved earlier than the first word unless start_offset is negative.
    """
    words = collect_whisper_words(data, fixes)
    entries = []
    cur = []
    last_end = None

    for w in words:
        cur_text = "".join(x["text"] for x in cur)
        candidate = cur_text + w["text"]
        gap = 0 if last_end is None else w["start"] - last_end
        duration = 0 if not cur else w["end"] - cur[0]["start"]
        should_break = False
        if cur:
            if visual_len(candidate) > max_chars:
                should_break = True
            elif duration > max_duration:
                should_break = True
            elif gap > gap_break and visual_len(cur_text) >= 7:
                should_break = True

        if should_break:
            entries.append(cur)
            cur = []

        cur.append(w)
        last_end = w["end"]

    if cur:
        entries.append(cur)

    timed = []
    for group in entries:
        begin = group[0]["start"] + start_offset
        end = group[-1]["end"] + end_padding
        text = filter_fillers(fix_text("".join(w["text"] for w in group), fixes))
        if not text:
            continue
        if end - begin < min_duration:
            end = begin + min_duration
        timed.append({"begin": begin, "end": end, "text": split_two_lines(text, max_line_chars)})

    for i in range(len(timed) - 1):
        next_begin = timed[i + 1]["begin"]
        if timed[i]["end"] > next_begin - 0.04:
            timed[i]["end"] = max(timed[i]["begin"] + 0.25, next_begin - 0.04)

    return timed

def format_srt_time(seconds):
    h = int(seconds // 3600)
    m = int((seconds % 3600) // 60)
    s = int(seconds % 60)
    ms = int(round((seconds % 1) * 1000))
    return f"{h:02d}:{m:02d}:{s:02d},{ms:03d}"

def generate_from_whisper_words(data, fixes):
    """
    Generate subtitles using Whisper word-level timestamps for precise timing.
    Each word has its own start/end time, so we can build subtitles with exact boundaries.
    """
    entries = []

    for seg in data.get("segments", []):
        words = seg.get("words", [])
        if not words:
            # Fallback to segment-level timing
            text = filter_fillers(fix_text(seg["text"], fixes))
            if text:
                chunks = split_to_chunks(text)
                if chunks:
                    total_chars = sum(len(c) for c in chunks)
                    duration = seg["end"] - seg["start"]
                    t = seg["start"]
                    for chunk in chunks:
                        chunk_dur = (len(chunk) / total_chars) * duration
                        entries.append({"begin": t, "end": t + chunk_dur, "text": chunk})
                        t += chunk_dur
            continue

        # Build text from words, tracking character positions to word timestamps
        full_text = ""
        word_map = []  # (char_start, char_end, word_start_time, word_end_time)

        for w in words:
            word_text = w.get("word", "")
            char_start = len(full_text)
            full_text += word_text
            char_end = len(full_text)
            word_map.append({
                "char_start": char_start,
                "char_end": char_end,
                "start": w.get("start", seg["start"]),
                "end": w.get("end", seg["end"]),
            })

        # Fix and filter text. Prefer LLM-corrected text when the pipeline added it.
        fixed_text = seg.get("fixed_text") or fix_text(full_text, fixes)
        filtered_text = filter_fillers(fixed_text)

        if not filtered_text:
            continue

        # Split into chunks
        chunks = split_to_chunks(filtered_text)
        if not chunks:
            continue

        # Map each chunk to word-level timestamps
        pos = 0
        for chunk in chunks:
            # Find the chunk in the fixed text
            chunk_start_in_text = fixed_text.find(chunk, pos)
            if chunk_start_in_text == -1:
                chunk_start_in_text = pos

            chunk_end_in_text = chunk_start_in_text + len(chunk)

            # Find corresponding word timestamps
            chunk_begin = None
            chunk_end = None

            for wm in word_map:
                # Word overlaps with chunk
                if wm["char_end"] > chunk_start_in_text and wm["char_start"] < chunk_end_in_text:
                    if chunk_begin is None:
                        chunk_begin = wm["start"]
                    chunk_end = wm["end"]

            if chunk_begin is None:
                # Fallback: estimate from position
                chunk_begin = seg["start"] + (chunk_start_in_text / len(fixed_text)) * (seg["end"] - seg["start"])
                chunk_end = seg["start"] + (chunk_end_in_text / len(fixed_text)) * (seg["end"] - seg["start"])

            entries.append({"begin": chunk_begin, "end": chunk_end, "text": chunk})
            pos = chunk_end_in_text

    return entries

def generate_from_dashscope(data, fixes):
    entries = []
    for ch in data.get("transcripts", []):
        for sent in ch.get("sentences", []):
            text = filter_fillers(fix_text(sent["text"], fixes))
            if not text:
                continue
            begin = sent["begin_time"] / 1000
            end = sent["end_time"] / 1000
            chunks = split_to_chunks(text)
            if not chunks:
                continue
            if len(chunks) == 1:
                entries.append({"begin": begin, "end": end, "text": chunks[0]})
            else:
                total_chars = sum(len(c) for c in chunks)
                duration = end - begin
                t = begin
                for chunk in chunks:
                    chunk_dur = (len(chunk) / total_chars) * duration
                    entries.append({"begin": t, "end": t + chunk_dur, "text": chunk})
                    t += chunk_dur
    return entries

def write_srt(entries, output_file):
    with open(output_file, "w", encoding="utf-8") as f:
        for i, e in enumerate(entries, 1):
            f.write(f"{i}\n")
            f.write(f"{format_srt_time(e['begin'])} --> {format_srt_time(e['end'])}\n")
            f.write(f"{e['text']}\n\n")

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Generate SRT from ASR output")
    parser.add_argument("input", help="ASR result JSON (Whisper or DashScope)")
    parser.add_argument("output", help="Output SRT file")
    parser.add_argument("--source", choices=["whisper", "dashscope"], default="whisper", help="ASR source")
    parser.add_argument("--fixes", help="JSON file with text fix rules [[from, to], ...]")
    parser.add_argument("--use-word-timestamps", action="store_true", default=True,
                        help="Use word-level timestamps for precise timing (default: True)")
    parser.add_argument("--constrained", action="store_true", default=True,
                        help="Constrain cue length, duration, overlap, and line count (default: True)")
    parser.add_argument("--max-chars", type=int, default=30, help="Max approximate chars per subtitle cue")
    parser.add_argument("--max-line-chars", type=int, default=18, help="Max approximate chars per subtitle line")
    parser.add_argument("--max-duration", type=float, default=3.5, help="Max subtitle duration in seconds")
    parser.add_argument("--min-duration", type=float, default=0.7, help="Min subtitle duration in seconds")
    parser.add_argument("--start-offset", type=float, default=0.02,
                        help="Cue start offset from first word; keep non-negative to avoid early subtitles")
    parser.add_argument("--end-padding", type=float, default=0.12, help="Cue end padding after last word")
    args = parser.parse_args()

    with open(args.input, "r", encoding="utf-8") as f:
        data = json.load(f)

    fixes = DEFAULT_FIXES
    if args.fixes and os.path.exists(args.fixes):
        with open(args.fixes) as f:
            fixes = json.load(f)

    if args.source == "whisper" and args.constrained:
        entries = generate_constrained_entries(
            data,
            fixes,
            max_chars=args.max_chars,
            max_line_chars=args.max_line_chars,
            max_duration=args.max_duration,
            min_duration=args.min_duration,
            start_offset=args.start_offset,
            end_padding=args.end_padding,
        )
    elif args.source == "whisper":
        entries = generate_from_whisper_words(data, fixes)
    else:
        entries = generate_from_dashscope(data, fixes)

    write_srt(entries, args.output)
    print(f"Generated {len(entries)} subtitle entries -> {args.output}", file=sys.stderr)
