import os
import shutil
import sys
from pathlib import Path

import pytest

# 保证从任何目录运行 pytest 都能 import montage / lib
ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

# 真 ffmpeg 用例不再"默认静默跳过"：本机有 ffmpeg 就自动开门，让
# ``@pytest.mark.ffmpeg`` / 函数内 ``MONTAGE_REAL_FFMPEG=1`` 门控真的执行。
# 显式设 ``MONTAGE_REAL_FFMPEG=0`` 可关闭（CI 无 ffmpeg 时自然跳过）。
if "MONTAGE_REAL_FFMPEG" not in os.environ:
    if shutil.which("ffmpeg"):
        os.environ["MONTAGE_REAL_FFMPEG"] = "1"


@pytest.fixture
def real_ffmpeg() -> str:
    """需要真实 ffmpeg 的用例统一门控：无 ffmpeg 则 skip。"""
    ff = shutil.which("ffmpeg")
    if not ff:
        pytest.skip("缺少 ffmpeg")
    return ff
