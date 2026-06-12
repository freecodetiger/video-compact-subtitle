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

Compact a video by removing silences and filler words, then generate and burn subtitles whose timing is aligned to the final audio timeline.

## Prerequisites

运行前先验证依赖：

```bash
ffmpeg -version && ffprobe -version
python3 -c "from faster_whisper import WhisperModel; print('faster-whisper OK')"
```

Required:

- `ffmpeg` and `ffprobe`
- `faster-whisper` Python package (`python3 -m pip install faster-whisper`)

## Workflow Overview

```text
Input Video
  -> Extract source audio
  -> Detect silences
  -> Detect filler words with Whisper tiny
  -> Build conservative speech intervals
  -> Render one verified compacted master
  -> Extract compacted audio
  -> Transcribe compacted audio with Whisper medium and vad_filter=False
  -> Generate constrained SRT from word timestamps
  -> Burn subtitles
```

The core reliability rule: subtitles must be generated from the audio timeline of the final compacted master, not from the source video timeline and not from an intermediate ASR timeline that VAD has silently remapped.

## Quick Run

```bash
SKILL_DIR="$HOME/.claude/skills/video-compact-subtitle"
python3 "$SKILL_DIR/scripts/run_pipeline.py" input.mp4 \
  --output output.mp4 \
  --language zh \
  --prompt "Claude Code, DeepSeek, API Key" \
  --whisper-model medium \
  --silence-threshold -30dB \
  --min-silence 0.3 \
  --max-segments 80 \
  --merge-gap 0.5 \
  --pre-roll 0.08 \
  --post-roll 0.12 \
  --font-size 18 \
  --style box \
  --keep-artifacts
```

Outputs:

- `output.mp4`: final video with burned subtitles
- `compact_work/compacted.mp4`: compacted master when `--keep-artifacts` is used
- `compact_work/compacted.timeline.json`: timeline verification report when `--keep-artifacts` is used
- `compact_work/compacted_audio.wav` and `compact_work/whisper_result.json`: final ASR inputs/results when `--keep-artifacts` is used
- `subtitles.srt`: standalone subtitle file next to the input video

For timing investigations, always use `--keep-artifacts`. Do not debug drift only from the burned final video.

## Step 1: Extract Audio

```bash
ffmpeg -i INPUT.mp4 -vn -acodec pcm_s16le -ar 16000 -ac 1 compact_work/audio.wav -y
```

## Step 2: Detect Silences

Use ffmpeg `silencedetect`. Its stderr can be long, so write it to a file before parsing instead of piping through `grep`:

```bash
ffmpeg -i INPUT.mp4 -af silencedetect=noise=-30dB:d=0.3 -f null - 2>compact_work/silence_raw.txt
```

Parameters:

- `noise=-30dB`: silence threshold; lower catches quieter sound
- `d=0.3`: minimum silence duration

## Step 3: Detect Filler Words

Use Whisper `tiny` for filler detection. It only needs word-level timestamps for short filler words, so speed matters more than full transcript quality:

```python
model = WhisperModel("tiny", device="cpu", compute_type="int8")
segments, info = model.transcribe(audio_wav, language="zh", word_timestamps=True, vad_filter=False)
```

Fillers include:

```text
嗯 啊 呃 额 哦 噢 哎 唉 哈 呵 呢 吧 呀 嘛 喂 嗨 嘿 哇 Ok ok
```

## Step 4: Build Speech Intervals

1. Merge silence intervals and filler intervals into a single remove list.
2. Invert the remove list to speech intervals.
3. Add a small guard band around speech (`--pre-roll`, `--post-roll`) so syllable onsets are not clipped.
4. Merge nearby speech intervals (`--merge-gap`) to avoid choppy cuts.
5. Cap cut count (`--max-segments`) by merging across the smallest gaps when needed.

These defaults intentionally trade a little less compression for much better subtitle sync, especially on H.264/H.265 long-GOP sources.

## Step 5: Render Verified Compacted Master

Default rendering uses one ffmpeg filter graph with `select/aselect`, resets timestamps to zero, and re-encodes audio/video in a single pass:

```bash
python3 scripts/compact_video.py INPUT.mp4 compact_work/compacted.mp4 \
  --silence-threshold=-30dB \
  --min-silence 0.3 \
  --merge-gap 0.5 \
  --max-segments 80 \
  --pre-roll 0.08 \
  --post-roll 0.12
```

The script writes `compacted.timeline.json` and fails if stream-level checks do not pass. Verify:

- video and audio `start_time` are near `0`
- video/audio durations are close
- expected compacted duration and actual output duration are close
- `"ok": true`

Legacy segment concat is still available with `compact_video.py --strategy segments`, but it should not be the default. Many tiny TS segments can introduce cumulative audio/video timestamp error; this is the main failure mode behind subtitles getting progressively early or late in the second half.

## Step 6: Transcribe Final Audio Timeline

Extract audio from the compacted master first:

```bash
ffmpeg -i compact_work/compacted.mp4 -vn -acodec pcm_s16le -ar 16000 -ac 1 compact_work/compacted_audio.wav -y
```

Then transcribe that audio with `vad_filter=False`:

```python
from faster_whisper import WhisperModel

model = WhisperModel("medium", device="cpu", compute_type="int8")
segments, info = model.transcribe(
    "compact_work/compacted_audio.wav",
    language="zh",
    beam_size=5,
    word_timestamps=True,
    vad_filter=False,
    initial_prompt="Claude Code, DeepSeek, API Key"
)
```

Do not use `vad_filter=True` for the final subtitle transcript. VAD can suppress or remap pauses internally, which creates timestamps that no longer correspond exactly to the compacted audio file.

## Step 7: Fix Transcript Text

Claude Code can directly read `compact_work/whisper_result.json`, fix recognition errors using the session context, and write corrected text back while preserving timestamps.

修正规则：

- Fix misrecognized technical terms, product names, and English terms.
- Remove residual filler words.
- Improve obvious口语/ASR errors without changing meaning.
- Preserve all `start`, `end`, and word timestamp fields.

Example fixes:

```text
"Claw Code" -> "Claude Code"
"DeepSig" -> "DeepSeek"
"Mini Key" -> "API Key"
```

## Step 8: Generate SRT Subtitles

Generate subtitles from word timestamps with display constraints:

```bash
python3 scripts/generate_srt.py compact_work/whisper_result.json subtitles.srt \
  --source whisper \
  --max-line-chars 18 \
  --max-duration 3.5 \
  --start-offset 0.02
```

Timing rules:

- cue start is aligned to the first word, with a non-negative default `--start-offset 0.02`
- cue start should not be moved earlier to make subtitles "feel faster"
- cue duration is capped by `--max-duration`
- overlaps are removed with a small gap
- each cue is split into at most two lines

If a subtitle appears before the speaker opens their mouth, first inspect whether the transcript was generated from the final compacted audio and whether `vad_filter=False` was used. Do not fix progressive drift with one global SRT offset unless the drift is proven constant.

## Step 9: Burn Subtitles

```bash
python3 scripts/burn_subtitles.py compact_work/compacted.mp4 subtitles.srt output.mp4 \
  --style box \
  --font-size 18
```

Styles:

- `box`: semi-transparent black background, recommended for screen recordings
- `outline`: white text with black outline
- `minimal`: white text only

## Tuning Guide

| Parameter | Effect | Recommended |
|-----------|--------|-------------|
| `--silence-threshold` | Lower catches more quiet sound | `-30dB` |
| `--min-silence` | Shorter cuts more aggressively | `0.3` |
| `--merge-gap` | Merge nearby kept speech gaps | `0.5` |
| `--max-segments` | Cap cut count by coalescing smallest gaps | `80` |
| `--pre-roll` | Keep audio before speech onset | `0.08` |
| `--post-roll` | Keep audio after speech end | `0.12` |
| `--whisper-model` | Final ASR quality/speed tradeoff | `medium` |
| `--font-size` | Subtitle readability | `18-22` |

For very drift-prone sources, especially H.264/H.265 long-GOP recordings, reduce the number of cuts first: increase `--merge-gap`, lower `--max-segments`, or create an all-I-frame mezzanine before compacting.

## Verification

After producing output, check stream timing:

```bash
ffprobe -v error \
  -show_entries format=start_time,duration:stream=index,codec_type,start_time,duration \
  -of json output.mp4
```

For high-quality subtitle delivery, inspect artifacts:

- `compact_work/compacted.timeline.json` should have `"ok": true`
- video/audio start times should be near zero
- video/audio duration delta should be small
- `subtitles.srt` should have no overlaps
- each cue should have at most two lines
- last cue should not exceed audio duration

## Common Issues

- **Subtitle timing gets progressively early/late**: treat the compacted master as suspect first. Check `compacted.timeline.json`; regenerate ASR from `compacted_audio.wav` with `vad_filter=False`.
- **Subtitles appear before speech starts**: keep `--start-offset` non-negative and verify word timestamps come from the final compacted audio.
- **Choppy audio**: increase `--merge-gap`, increase `--min-silence`, or reduce `--max-segments`.
- **Too many risky cuts on H.264/H.265**: lower `--max-segments` or use an all-I-frame mezzanine.
- **ASR misrecognizes terms**: improve `--prompt`, update fixes, or manually correct `whisper_result.json` while preserving timestamps.
- **More than two subtitle lines**: lower `--max-line-chars` or `--max-chars` in `generate_srt.py`.
- **ffmpeg select expression too long**: reduce `--max-segments`, increase `--merge-gap`, or use an all-I-frame mezzanine.
- **Slow filler detection**: ensure filler detection uses Whisper `tiny`, not `medium`.
- **silencedetect output truncated**: write stderr to a file and parse that file.
