#!/usr/bin/env python3
import json
import unittest
from pathlib import Path

import compact_video
import generate_srt


class CompactVideoPlanningTests(unittest.TestCase):
    def test_build_speech_intervals_pads_and_limits_cut_count(self):
        remove = [
            {"start": 1.0, "end": 1.4},
            {"start": 2.0, "end": 2.4},
            {"start": 3.0, "end": 3.4},
            {"start": 4.0, "end": 4.4},
        ]

        speeches = compact_video.build_speech_intervals(
            remove,
            total_duration=5.0,
            pre_roll=0.1,
            post_roll=0.2,
            merge_gap=0.0,
            max_segments=3,
        )

        self.assertLessEqual(len(speeches), 3)
        self.assertEqual(speeches[0]["start"], 0.0)
        self.assertAlmostEqual(speeches[-1]["end"], 5.0)

    def test_verify_timeline_rejects_large_duration_drift(self):
        probe = {
            "streams": [
                {"codec_type": "video", "start_time": "0.000000", "duration": "12.0"},
                {"codec_type": "audio", "start_time": "0.000000", "duration": "10.0"},
            ],
            "format": {"duration": "12.0"},
        }

        report = compact_video.analyze_timeline(probe, expected_duration=10.0, tolerance=0.5)

        self.assertFalse(report["ok"])
        self.assertIn("av_duration_delta", report["failures"])


class SubtitleGenerationTests(unittest.TestCase):
    def test_constrained_entries_use_word_start_and_max_two_lines(self):
        data = {
            "segments": [
                {
                    "start": 0.0,
                    "end": 5.0,
                    "text": "这是一个需要被拆成多条字幕的长句子",
                    "words": [
                        {"start": i * 0.4, "end": i * 0.4 + 0.25, "word": ch}
                        for i, ch in enumerate("这是一个需要被拆成多条字幕的长句子")
                    ],
                }
            ]
        }

        entries = generate_srt.generate_constrained_entries(
            data,
            fixes=[],
            max_chars=10,
            max_line_chars=6,
            max_duration=1.6,
            min_duration=0.4,
            start_offset=0.02,
            end_padding=0.08,
        )

        self.assertGreater(len(entries), 1)
        self.assertAlmostEqual(entries[0]["begin"], 0.02)
        for entry in entries:
            self.assertLessEqual(len(entry["text"].split("\n")), 2)
        for left, right in zip(entries, entries[1:]):
            self.assertLessEqual(left["end"], right["begin"])


if __name__ == "__main__":
    unittest.main()
