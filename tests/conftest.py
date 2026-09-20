import os
import shutil
import subprocess
import sys
import tempfile
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


# --- 真实小视频夹具（B 类测试修复用）---------------------------------------
# 素材契约（material_contract）与 clips_compose_ready 会真的 ffprobe 每个
# kind=video 的产物；老夹具直接写 b"fake"/b"vid" 必然被判"坏镜"。这里给一个
# **进程内缓存**的真实 1s 短视频模板，测试用 copy 复制（毫秒级），既让契约
# 通过，又不用每个用例都跑一次 ffmpeg。
_TINY_VIDEO_TEMPLATES: dict[float, Path] = {}


def _tiny_video_template(seconds: float = 1.0) -> Path | None:
    """按秒数缓存真实小视频模板（同秒数只跑一次 ffmpeg）。"""
    key = round(float(seconds), 2)
    cached = _TINY_VIDEO_TEMPLATES.get(key)
    if cached is not None and cached.is_file():
        return cached
    ffmpeg = shutil.which("ffmpeg")
    if not ffmpeg:
        return None
    cache = Path(tempfile.gettempdir()) / "montage_tests_media"
    cache.mkdir(parents=True, exist_ok=True)
    dest = cache / f"tiny_{key:g}s_24fps.mp4"
    if not dest.is_file() or dest.stat().st_size <= 0:
        try:
            subprocess.run(
                [
                    ffmpeg, "-y",
                    "-f", "lavfi", "-i", f"testsrc=duration={key:g}:size=320x240:rate=24",
                    "-f", "lavfi", "-i", f"sine=frequency=440:duration={key:g}",
                    "-c:v", "libx264", "-pix_fmt", "yuv420p", "-preset", "ultrafast",
                    "-c:a", "aac", "-shortest", str(dest),
                ],
                check=True, capture_output=True,
            )
        except (OSError, subprocess.CalledProcessError):
            return None
    _TINY_VIDEO_TEMPLATES[key] = dest
    return dest


def write_tiny_video(
    dest: Path,
    *,
    seconds: float = 1.0,
    fallback: bytes = b"vid",
) -> Path:
    """把真实短视频写到 ``dest``；无 ffmpeg 时退回假字节（老行为）。

    ``seconds`` 用于让假片段与镜头请求时长一致——时长不符会被
    retry 策略判成 "grow"（走 extend 而不是 edit），也会在素材契约里报
    duration 不一致的 warning。
    """
    dest = Path(dest)
    dest.parent.mkdir(parents=True, exist_ok=True)
    template = _tiny_video_template(seconds)
    if template is None:
        dest.write_bytes(fallback)
        return dest
    shutil.copy2(template, dest)
    return dest
