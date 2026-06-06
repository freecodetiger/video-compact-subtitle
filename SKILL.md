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

运行前必须先验证依赖，执行以下检查：

```bash
# 1. ffmpeg/ffprobe
ffmpeg -version && ffprobe -version

# 2. Python 包（缺少哪个 pip install 哪个）
python3 -c "from faster_whisper import WhisperModel; print('faster-whisper OK')"
```

- `ffmpeg` and `ffprobe` installed
- `faster-whisper` Python package (`pip install faster-whisper`)

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

Use ffmpeg's `silencedetect` to find all silent intervals. **注意：** 输出可能很大（数百行），不要用 `grep` 管道（会被截断），直接写入临时文件再解析：

```bash
# 输出写入文件，避免截断
ffmpeg -i INPUT.mp4 -af silencedetect=noise=-30dB:d=0.3 -f null - 2>compact_work/silence_raw.txt
```

然后用 Python 解析 `silence_raw.txt`：

```python
import re
silences = []
cur_start = None
for line in open("compact_work/silence_raw.txt"):
    m_start = re.search(r'silence_start:\s*([\d.]+)', line)
    m_end = re.search(r'silence_end:\s*([\d.]+)', line)
    if m_start:
        cur_start = float(m_start.group(1))
    if m_end and cur_start is not None:
        silences.append({"start": cur_start, "end": float(m_end.group(1))})
        cur_start = None
```

Parameters:
- `noise=-30dB`: Threshold for silence detection. Lower = more sensitive.
- `d=0.3`: Minimum silence duration in seconds. 0.3s catches natural pauses.

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

## Step 7: Fix Transcript (Claude 直接修正，无需 API)

> **核心变更：** 不再调用外部 LLM API。由当前 Claude Code 会话直接读取 `raw_segments.json`，根据会话上下文（视频主题、涉及的技术领域、用户提到的术语等）修正转录文本，然后写回文件。

执行流程：

1. **读取** `compact_work/raw_segments.json`
2. **根据上下文修正** 每段文本，修正规则：
   - 语音识别错误的技术名词（如 Claw Code→Claude Code, DeepSig→DeepSeek）
   - 不通顺的口语表达，改为自然书面语
   - 残留的填充词（嗯、啊、呃等）
   - 根据视频主题推断的领域术语（如 "夯到拉" 是评分体系：夯=高、到=中、拉=低）
3. **写回** 修正后的 JSON 到同一文件（保持 timestamps 不变）
4. 继续 Step 8 生成 SRT

**示例修正（实际执行时根据视频内容判断）：**

```
"出金率比较高" → "出现率比较高"        # 语音识别错误
"得个假"       → "得声明一下"          # 语音识别错误
"夯报了"       → "夯爆了"              # 口语缩读
"上下线"       → "上限和下限"          # 语音识别错误
"鸡蛋机"       → "计算机"              # 语音识别错误
"黄历和奔县"   → "理论和实践"          # 语音识别错误
"文法地归"     → "文法递归"            # 语音识别错误
```

> **优势：** 零依赖（不需要 anthropic 包）、零延迟（无需 API 调用）、上下文感知（能利用视频主题和用户描述做更准确的推断）。

## Step 8: Generate SRT Subtitles

### 8a. 重新转录（如果视频被裁剪过）

> **关键：** 如果 compacted.mp4 被裁剪过（如去掉开头），必须重新转录，不能沿用旧的 raw_segments，否则时间轴全部错位。

```python
# 裁剪后重新转录
ffmpeg -y -ss TRIM_START -i compacted.mp4 -c copy compacted_final.mp4

model = WhisperModel("medium", device="cpu", compute_type="int8")
segments, info = model.transcribe("compacted_final.mp4", ...)
```

### 8b. 语义切分 + 无间隔衔接

**切分规则（按优先级）：**
1. 按句号（。？！）切大段
2. 大段内按逗号（，、）切小段
3. 仍有超长段则按字数硬切兜底
4. 每条字幕 ≤ 28 字（约两行半）

**时间规则：**
- 起始时间 = Whisper 词级时间戳对齐
- **结束时间 = 下一条字幕的起始时间**（无间隔，说话人不停字幕不停）
- 最后一条加 200ms 缓冲
- 最小显示时间 0.25s

```python
def semantic_split(text, max_chars=28):
    """按语义标点切分，保持完整语义单位。"""
    # 先按句号切大段
    big_parts = re.split(r'(?<=[。？！])', text)
    chunks = []
    for part in big_parts:
        part = part.strip()
        if not part:
            continue
        if len(part) <= max_chars:
            chunks.append(part)
            continue
        # 大段内按逗号切
        clauses = re.split(r'(?<=[，、])', part)
        current = ""
        for c in clauses:
            c = c.strip()
            if not c:
                continue
            if len(current) + len(c) <= max_chars:
                current += c
            else:
                if current:
                    chunks.append(current)
                current = c
        if current:
            chunks.append(current)
    return chunks

# 生成条目后，无间隔衔接
for i in range(len(entries) - 1):
    entries[i]["end"] = entries[i + 1]["start"]  # 紧接下一条
entries[-1]["end"] += 0.2  # 最后一条加缓冲
```

SRT format:
```
1
00:00:00,000 --> 00:00:02,879
从夯到拉评价计算机专业课

2
00:00:02,879 --> 00:00:06,160
我整理的一些出现率比较高的专业课程
```

## Step 9: Burn Subtitles

使用 ffmpeg 直接烧录，无需额外脚本：

```bash
ffmpeg -y -i compacted.mp4 \
  -vf "subtitles=subtitles.srt:force_style='FontSize=20,PrimaryColour=&H00FFFFFF,BackColour=&H80000000,BorderStyle=4,Outline=0,Shadow=0,MarginV=30,Alignment=2'" \
  -c:v libx264 -preset fast -crf 18 -c:a copy output.mp4
```

Style options:
- `BorderStyle=4`: Semi-transparent black background (no outline)
- `BackColour=&H80000000`: 50% transparent black
- `Outline=0`: No text outline
- `MarginV=30`: Distance from bottom edge
- `FontSize`: 18-22 recommended

## Timestamp Precision

The pipeline uses **Whisper word-level timestamps** for precise subtitle timing:
- Each word in the ASR output has its own `start` and `end` time
- Subtitle boundaries are mapped to exact word positions
- Achieves ±50ms precision (sufficient for subtitle synchronization)

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
- **ASR misrecognizes terms**: Step 7 会根据上下文自动修正；也可手动编辑 `subtitles.srt` 后重新烧录
- **Subtitle timing drift**: 裁剪视频后必须重新转录（Step 8a），不能沿用旧的 raw_segments
- **字幕有间隔/话没说完字幕就消失**: 用无间隔衔接（`entries[i]["end"] = entries[i+1]["start"]`），不要用单词的 end 时间
- **字幕堆积超过两行**: 用语义切分（按。？！→，、层级），限制每条 ≤28 字
- **ffmpeg select expression too long**: Use segment concat approach instead
- **Slow filler detection**: Ensure Step 3 uses `tiny` model, not `medium`
- **silencedetect 输出截断**: 不要用 `grep` 管道，改用 `2>file.txt` 写入文件再 Python 解析
