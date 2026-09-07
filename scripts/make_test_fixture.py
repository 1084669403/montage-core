"""make_test_fixture — 合成 480p 短测试视频到 testdata/。

零新依赖：调用系统 ffmpeg（lavfi testsrc + sine）。生成物不入库（见 .gitignore testdata/）。
门控：必须设置 MONTAGE_REAL_FFMPEG=1，避免 CI/沙箱误跑子进程。

用法：
    set MONTAGE_REAL_FFMPEG=1
    python scripts/make_test_fixture.py
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "testdata" / "sample_480p.mp4"


def main() -> int:
    if os.environ.get("MONTAGE_REAL_FFMPEG") != "1":
        print("跳过：设置 MONTAGE_REAL_FFMPEG=1 后再生成 fixture")
        return 0
    ffmpeg = shutil.which("ffmpeg")
    if not ffmpeg:
        print("缺少 ffmpeg", file=sys.stderr)
        return 2
    OUT.parent.mkdir(parents=True, exist_ok=True)
    if OUT.exists() and OUT.stat().st_size > 1000:
        print(f"已存在: {OUT}")
        return 0
    cmd = [
        ffmpeg, "-y",
        "-f", "lavfi", "-i", "testsrc=duration=4:size=854x480:rate=24",
        "-f", "lavfi", "-i", "sine=frequency=440:duration=4",
        "-c:v", "libx264", "-pix_fmt", "yuv420p", "-preset", "veryfast",
        "-c:a", "aac", "-shortest",
        str(OUT),
    ]
    subprocess.run(cmd, check=True)
    print(f"已生成: {OUT}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
