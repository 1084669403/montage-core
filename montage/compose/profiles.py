"""profiles — 平台渲染参数（原创，借鉴"平台档案"思路）。

每个 profile 描述目标平台的画面尺寸/帧率/编码参数，供合成端按交付目标
输出（scale+pad 保比例、统一 H.264/AAC、码率与 crf）。Agent 在 proposal
阶段把交付平台写进 proposal_packet，compose 阶段按 profile 出片。

用法：
    from montage.compose.profiles import get_profile, list_profiles
    p = get_profile("douyin_vertical")   # MediaProfile | None
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class MediaProfile:
    """一个命名渲染档案（目标平台/格式）。"""

    name: str
    width: int
    height: int
    fps: int
    crf: int = 18
    audio_bitrate: str = "192k"
    video_bitrate: str = ""  # 空 = 交给 crf 控制
    max_duration_seconds: float = 0.0  # 0 = 不限
    notes: str = ""

    def scale_filter(self) -> str:
        """保比例缩放 + 补边到目标尺寸的 ffmpeg -vf 表达式。"""
        return (
            f"scale={self.width}:{self.height}:force_original_aspect_ratio=decrease,"
            f"pad={self.width}:{self.height}:(ow-iw)/2:(oh-ih)/2"
        )


PROFILES: dict[str, MediaProfile] = {
    "youtube_landscape": MediaProfile(
        name="youtube_landscape", width=1920, height=1080, fps=30, crf=18,
        notes="YouTube / B 站横屏标准高清",
    ),
    "youtube_4k": MediaProfile(
        name="youtube_4k", width=3840, height=2160, fps=30, crf=18,
        video_bitrate="45M", notes="YouTube 4K 上传",
    ),
    "bilibili_horizontal": MediaProfile(
        name="bilibili_horizontal", width=1920, height=1080, fps=30, crf=18,
        notes="B 站横屏（大会员可传 4K，普通 1080P 即可）",
    ),
    "douyin_vertical": MediaProfile(
        name="douyin_vertical", width=1080, height=1920, fps=30, crf=20,
        max_duration_seconds=600, notes="抖音竖屏 9:16",
    ),
    "wechat_vertical": MediaProfile(
        name="wechat_vertical", width=1080, height=1920, fps=30, crf=20,
        max_duration_seconds=300, notes="微信视频号竖屏",
    ),
    "cinematic_21_9": MediaProfile(
        name="cinematic_21_9", width=2560, height=1080, fps=24, crf=17,
        notes="影院宽银幕 2.39:1 效果（裁切式，非补边）",
    ),
}


def get_profile(name: str) -> MediaProfile | None:
    return PROFILES.get(name)


def list_profiles() -> list[dict[str, Any]]:
    return [
        {
            "name": p.name,
            "resolution": f"{p.width}x{p.height}",
            "fps": p.fps,
            "crf": p.crf,
            "max_duration_seconds": p.max_duration_seconds or None,
            "notes": p.notes,
        }
        for p in PROFILES.values()
    ]
