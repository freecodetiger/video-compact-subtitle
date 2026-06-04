---
name: video-compact-subtitle
description: >
  Compact a video by removing silences, filler words, and pauses, then add subtitles.
  Use this skill whenever the user wants to: make a video more concise/tight, remove
  dead air or pauses from a video, add subtitles/captions to a video, transcribe a
  video with timestamps, or create a "jump cut" style edit. Also use when the user
  mentions removing filler words (嗯/啊/呃/um/uh/like) from audio or video.
---

# Video Compact + Subtitle Skill

Compact a video by removing all silences and filler words, then generate and burn subtitles.

## Prerequisites

- `ffmpeg` and `ffprobe` installed
- `faster-whisper` Python package (`pip install faster-whisper`)
- DashScope API key (optional, for Paraformer ASR)

## Workflow Overview

```
Input Video → Extract Audio → Detect Silences → Detect Filler Words (tiny model)
    → Cut & Concat Segments → Whisper Transcribe (medium model)
    → AI Fix Text (Claude API) → Generate SRT → Burn Subtitles
```

## Step 1: Extract Audio

```bash
ffmpeg -i INPUT.mp4 -vn -acodec pcm_s16le -ar 16000 -ac 1 audio.wav -y
```

## Step 2: Detect Silences

Use ffmpeg's `silencedetect` to find all silent intervals:

```bash
ffmpeg -i INPUT.mp4 -af silencedetect=noise=-30dB:d=0.3 -f null - 2>&1 | grep "silence_"
```

Parameters:
- `noise=-30dB`: Threshold for silence detection. Lower = more sensitive.
- `d=0.3`: Minimum silence duration in seconds. 0.3s catches natural pauses.

Parse output to get silence intervals: `{start, end}` pairs.

## Step 3: Detect Filler Words (via ASR)

Use Whisper **`tiny` model** for filler detection — it only needs to identify single-character fillers, so accuracy isn't critical and speed matters more:

```python
model = WhisperModel("tiny", device="cpu", compute_type="int8")
```

Get **word-level timestamps**, then match against filler word list:

```
Fillers: 嗯 啊 呃 额 哦 噢 哎 唉 哈 呵 呢 吧 呀 嘛 喂 嗨 嘿 哇 Ok ok
```

Each matched filler becomes a `{start, end}` interval to remove.

> **Why tiny?** The `medium` model takes ~20min for filler detection on CPU. `tiny` does the same job in ~3min since filler words are simple and repetitive — no need for high ASR accuracy here. Reserve `medium` for the final transcription (Step 6).

## Step 4: Build Speech Intervals

1. Merge silence intervals + filler intervals into a single "remove" list
2. Sort by start time, merge overlapping intervals
3. Invert to get "speech" intervals (the parts to keep)
4. Merge speech intervals that are < 150ms apart to avoid choppy cuts

## Step 5: Cut & Concat Segments

Use the segment-based approach (not `select` filter, which has expression length limits):

```bash
# For each speech segment:
ffmpeg -y -ss START -i INPUT.mp4 -t DURATION -c:v libx264 -preset fast -crf 18 \
  -c:a aac -b:a 128k -avoid_negative_ts make_zero segment_NNNN.ts

# Concat all segments:
ffmpeg -y -f concat -safe 0 -i concat.txt -c copy compacted.mp4
```

Run cuts in parallel batches of 8 for speed. Temporary segment files can be deleted after concat.

## Step 6: Transcribe with Whisper

Use `faster-whisper` for best accuracy with Chinese + technical terms:

```python
from faster_whisper import WhisperModel

model = WhisperModel("medium", device="cpu", compute_type="int8")
segments, info = model.transcribe(
    "compacted.mp4",
    language="zh",
    beam_size=5,
    word_timestamps=True,
    vad_filter=True,
    initial_prompt="技术名词提示，如 Claude Code, CCSwitch, DeepSeek, API Key"
)
```

Key parameters:
- `language="zh"`: Set language for better accuracy
- `initial_prompt`: Comma-separated technical terms to guide recognition
- `word_timestamps=True`: Get precise word-level timing
- `vad_filter=True`: Skip remaining silences

## Step 7: Fix Technical Terms (via Claude API)

Send the transcript to Claude API in batches of ~20 segments for intelligent correction. Process is automatic — no human confirmation needed.

```python
import anthropic

client = anthropic.Anthropic()

SYSTEM_PROMPT = """你是字幕修正助手。请修正以下字幕中的问题：

1. 技术名词错误：如 Claw Code→Claude Code, DeepSig→DeepSeek, Ancetropic→Anthropic
2. 不通顺的表达：让文字更自然流畅
3. 过滤残留的填充词（嗯、啊、呃等）

规则：
- 不要改变语义，只修正明显的错误
- 不要添加原文没有的内容
- 不要改变时间戳，只输出修正后的文本
- 保持原始的段落结构（每段之间用空行分隔）
- 输出格式：每段一行，保持原来的顺序"""

def fix_transcript(segments, batch_size=20):
    """Send transcript to Claude API in batches for correction."""
    fixed = []
    for i in range(0, len(segments), batch_size):
        batch = segments[i:i+batch_size]
        # Build input: numbered lines with timestamps for reference
        lines = []
        for j, seg in enumerate(batch):
            lines.append(f"[{i+j+1}] {seg['text']}")
        input_text = "\n".join(lines)

        response = client.messages.create(
            model="claude-haiku-4-5-20251001",
            max_tokens=4096,
            system=SYSTEM_PROMPT,
            messages=[{"role": "user", "content": input_text}]
        )

        # Parse response
        result = response.content[0].text.strip().split("\n")
        for k, seg in enumerate(batch):
            if k < len(result):
                # Strip numbering prefix like [1] or 1.
                corrected = result[k].strip()
                corrected = re.sub(r'^\[\d+\]\s*', '', corrected)
                corrected = re.sub(r'^\d+\.\s*', '', corrected)
                seg['text'] = corrected
            fixed.append(seg)

    return fixed
```

Use `claude-haiku-4-5-20251001` for speed and cost efficiency — it's sufficient for text correction tasks. The pipeline will automatically detect the `ANTHROPIC_API_KEY` environment variable.

## Step 8: Generate SRT Subtitles

Split long sentences into display-friendly chunks:

**Rules:**
- Split by commas (，) and periods (。)
- Merge every 2 clauses into one subtitle entry
- Distribute time proportionally by character count
- Max ~20-25 characters per subtitle line

```javascript
function splitToChunks(text) {
  const parts = text.split(/(?<=[，。？！])/g).filter(s => s.trim());
  const chunks = [];
  for (let i = 0; i < parts.length; i += 2) {
    chunks.push((parts[i] + (parts[i+1] || '')).trim());
  }
  return chunks.filter(s => s.length > 0);
}
```

SRT format:
```
1
00:00:00,000 --> 00:00:04,500
第一句字幕文字

2
00:00:04,500 --> 00:00:08,200
第二句字幕文字
```

## Step 9: Burn Subtitles

```bash
python scripts/burn_subtitles.py compacted.mp4 subtitles.srt output.mp4 --style box --font-size 18
```

**Subtitle style options:**
- `box`: Semi-transparent black background, no outline (recommended)
- `outline`: White text with black outline
- `minimal`: White text only, no background or outline

## Timestamp Precision

The pipeline uses **Whisper word-level timestamps** for precise subtitle timing:
- Each word in the ASR output has its own `start` and `end` time
- Subtitle boundaries are mapped to exact word positions
- Achieves ±50ms precision (sufficient for subtitle synchronization)

For even higher precision (±10ms), use whisperX forced alignment:
```bash
python scripts/align_subtitles.py compacted.mp4 subtitles.srt subtitles_aligned.srt --language zh
```
Note: whisperX alignment may fail on hard-cut audio (concatenated segments). Use on continuous audio only.

```bash
ffmpeg -i compacted.mp4 -vf "subtitles=subtitles.srt:force_style='
  FontSize=18,
  PrimaryColour=&H00FFFFFF,
  BackColour=&H80000000,
  BorderStyle=4,
  Outline=0,
  Shadow=0,
  MarginV=30,
  Alignment=2
'" -c:v libx264 -preset fast -crf 18 -c:a copy -y output.mp4
```

Style options:
- `BorderStyle=4`: Semi-transparent black background (no outline)
- `BackColour=&H80000000`: 50% transparent black
- `Outline=0`: No text outline
- `MarginV=30`: Distance from bottom edge

## Output Files

| File | Description |
|------|-------------|
| `compacted.mp4` | Video with silences/fillers removed |
| `subtitles.srt` | Standalone subtitle file |
| `output.mp4` | Final video with burned-in subtitles |

## Tuning Guide

| Parameter | Effect | Recommended |
|-----------|--------|-------------|
| Silence threshold (`noise`) | Lower catches more quiet sounds | -30dB |
| Min silence duration (`d`) | Shorter = more aggressive cutting | 0.3s |
| Merge gap | Merge speech segments closer than this | 150ms |
| Whisper model (filler detection) | Fast enough for filler detection | tiny |
| Whisper model (transcription) | More accurate for final subtitles | medium |
| CRF | Lower = better quality, larger file | 18 |
| Font size | Subtitle readability | 18-22 |

## Common Issues

- **Choppy audio after concat**: Increase merge gap to 200-300ms
- **ASR misrecognizes terms**: Add to `initial_prompt` and rely on Claude API post-correction (Step 7)
- **Subtitle timing drift**: Re-transcribe compacted video (don't map from original)
- **ffmpeg select expression too long**: Use segment concat approach instead
- **Slow filler detection**: Ensure Step 3 uses `tiny` model, not `medium`
