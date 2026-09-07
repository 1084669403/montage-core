import sys
from pathlib import Path

# 保证从任何目录运行 pytest 都能 import montage / lib
ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
