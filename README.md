# Video Compact + Subtitle

通过自动检测并移除静音和填充词来压缩视频，然后生成并烧录字幕。

## 特性

- **自动静音检测** — 基于 ffmpeg silencedetect，可调节阈值和最小持续时间
- **填充词检测** — 使用 Whisper tiny 模型快速识别"嗯、啊、呃"等填充词
- **智能字幕修正** — 通过 Claude API 自动修正技术名词和不通顺的表达
- **中文优化** — 针对中文语音和技术术语优化的 ASR 配置
- **高性能** — ffmpeg 多线程切割，Whisper 分模型策略（tiny 检测填充词 + medium 精准转录）

## 工作流程

```
输入视频 → 提取音频 → 检测静音 → 检测填充词（tiny 模型）
    → 切割拼接 → Whisper 转录（medium 模型）
    → Claude API 智能修正 → 生成 SRT → 烧录字幕
```

## 依赖

- `ffmpeg` 和 `ffprobe`
- `faster-whisper` Python 包
- `anthropic` Python 包（用于字幕修正）
- `ANTHROPIC_API_KEY` 环境变量

## 安装

```bash
pip install faster-whisper anthropic
```

## 使用

作为 Claude Code Skill 使用，直接对视频文件调用即可：

```
/video-compact-subtitle /path/to/video.mp4
```

## 输出文件

| 文件 | 说明 |
|------|------|
| `compacted.mp4` | 移除静音/填充词后的视频 |
| `subtitles.srt` | 独立字幕文件 |
| `output.mp4` | 最终带字幕的视频 |

## 配置参数

| 参数 | 说明 | 默认值 |
|------|------|--------|
| 静音阈值 | 越低越敏感 | -30dB |
| 最小静音时长 | 越短切割越激进 | 0.3s |
| 合并间隔 | 小于此值的语音段合并 | 150ms |
| 填充词检测模型 | 需要快速 | tiny |
| 转录模型 | 需要精准 | medium |

## 许可证

[MIT](LICENSE)
