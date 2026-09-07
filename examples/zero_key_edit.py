"""零密钥剪辑演示：本地 fixture + AutoEditor（documentary），不进 7 阶段。

需要系统 ffmpeg，并设置 MONTAGE_REAL_FFMPEG=1。
输出落到 examples/output/（已 gitignore）。
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "examples" / "output" / "zero_key"
FIXTURE = ROOT / "testdata" / "sample_480p.mp4"


def main() -> int:
    if os.environ.get("MONTAGE_REAL_FFMPEG") != "1":
        print("跳过：设置 MONTAGE_REAL_FFMPEG=1 后重跑（需要本机 ffmpeg）")
        return 0
    rc = subprocess.call([sys.executable, str(ROOT / "scripts" / "make_test_fixture.py")])
    if rc != 0:
        return rc
    if not FIXTURE.is_file():
        print(f"缺少 fixture: {FIXTURE}", file=sys.stderr)
        return 2
    from montage.cli import main as cli_main

    OUT.mkdir(parents=True, exist_ok=True)
    return cli_main([
        "auto_edit",
        str(OUT),
        "--video",
        str(FIXTURE),
        "--style",
        "documentary",
    ])


if __name__ == "__main__":
    raise SystemExit(main())
