#!/usr/bin/env python3
"""Detect filler words from ASR word-level timestamps."""
import json
import sys

FILLER_WORDS = {
    "嗯", "啊", "呃", "额", "哦", "噢", "哎", "唉", "哈", "呵",
    "呢", "吧", "呀", "嘛", "喂", "嗨", "嘿", "哇", "Ok", "ok", "OK",
}

def detect_fillers(transcription_json):
    """Extract filler word intervals from ASR transcription with word-level timestamps."""
    fillers = []
    for ch in transcription_json.get("transcripts", []):
        for sent in ch.get("sentences", []):
            for w in sent.get("words", []):
                text = w.get("text", "").strip()
                if text in FILLER_WORDS:
                    fillers.append({
                        "start": w["begin_time"] / 1000,
                        "end": w["end_time"] / 1000,
                        "text": text,
                    })
    return fillers

def detect_fillers_whisper(whisper_json):
    """Extract filler word intervals from Whisper output."""
    fillers = []
    for seg in whisper_json.get("segments", []):
        for w in seg.get("words", []):
            text = w.get("word", "").strip().rstrip("，。、？！")
            if text in FILLER_WORDS:
                fillers.append({
                    "start": w["start"],
                    "end": w["end"],
                    "text": text,
                })
    return fillers

if __name__ == "__main__":
    input_file = sys.argv[1] if len(sys.argv) > 1 else "transcription.json"
    source = sys.argv[2] if len(sys.argv) > 2 else "dashscope"  # or "whisper"

    with open(input_file, "r", encoding="utf-8") as f:
        data = json.load(f)

    if source == "whisper":
        fillers = detect_fillers_whisper(data)
    else:
        fillers = detect_fillers(data)

    print(json.dumps(fillers, indent=2, ensure_ascii=False))
    print(f"\nTotal: {len(fillers)} filler words", file=sys.stderr)
