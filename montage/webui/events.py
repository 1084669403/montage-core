"""events — 看板实时刷新的事件源（P0-7c）。

只做一件事：盯项目目录里**会变的东西**（artifacts/*.json、decisions.jsonl、cost.jsonl、
成片 mp4、auto_edit/plan.json …）的 mtime+size 指纹，变了就推一条轻量事件。

设计约束（都是踩过的坑，别改）：

- 纯逻辑、**不 import fastapi**：本仓看板数据层的测试就不依赖 FastAPI，事件源同理。
- 只看 mtime/size，**不在轮询里解析产物 JSON**：1s 一轮，解析整个项目等于把
  ``GET /api/projects/{id}`` 的开销挪成后台常驻。真变了才读一眼 progress（很小）。
- 只推「变了」，不推内容：客户端收到照旧走既有 GET，避免两套序列化口径。
- 事件流**只刷视图**，不放行任何写操作（看板不出片、不 retry）。
"""

from __future__ import annotations

import hashlib
import json
import time
from collections.abc import Iterator
from pathlib import Path
from typing import Any

#: 盯哪些文件。故意写死：多盯一个文件就是多一份 stat，且会推出看板并不显示的刷新。
WATCH_PATTERNS: tuple[str, ...] = (
    "artifacts/*.json",
    "artifacts/*/*.json",
    "decisions.jsonl",
    "cost.jsonl",
    "renders/final.mp4",
    "renders/season.mp4",
    "auto_edit/final.mp4",
    "auto_edit/plan.json",
    "auto_edit/report.json",
    "tmp_autoedit/plan.json",
    "tmp_autoedit/beat_map.json",
    "exports/*.zip",
)

#: 空闲多久自动收线（秒）。EventSource 会自己重连，所以这里宁短不长，别攒僵尸连接。
DEFAULT_MAX_IDLE = 3600.0
#: 轮询下限：再快也只是把风控烧掉，SSE 的体感提升在 1s 这一档就到顶了。
MIN_INTERVAL = 0.2


def discover_targets(root: str | Path) -> list[tuple[str, Path]]:
    """列出 (project_id, 项目目录)。项目 id 与看板口径一致（子集是 ``parent::eid``）。"""
    projects_dir = Path(root) / "projects"
    if not projects_dir.is_dir():
        return []
    targets: list[tuple[str, Path]] = []
    for meta in sorted(projects_dir.glob("*/project.json")):
        targets.append((meta.parent.name, meta.parent))
    for meta in sorted(projects_dir.glob("*/episodes/*/project.json")):
        episode_id = meta.parent.name
        parent_id = meta.parent.parent.parent.name
        targets.append((f"{parent_id}::{episode_id}", meta.parent))
    return targets


def fingerprint(project_dir: str | Path) -> str:
    """项目目录的「变了没」指纹（sha1 前 12 位）。不存在/空目录 → 空串。"""
    base = Path(project_dir)
    digest = hashlib.sha1()
    seen = 0
    for pattern in WATCH_PATTERNS:
        try:
            paths = sorted(base.glob(pattern))
        except OSError:
            continue
        for path in paths:
            try:
                stat = path.stat()
            except OSError:
                continue
            digest.update(f"{path.relative_to(base).as_posix()}:{stat.st_mtime_ns}:{stat.st_size}".encode())
            seen += 1
    return digest.hexdigest()[:12] if seen else ""


def progress_status(project_dir: str | Path) -> str:
    """读 ``artifacts/produce_progress.json`` 的 status（只在真变了时调用）。"""
    path = Path(project_dir) / "artifacts" / "produce_progress.json"
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return ""
    return str(data.get("status") or "") if isinstance(data, dict) else ""


def sse_format(event: dict[str, Any], *, retry_ms: int | None = None) -> str:
    """事件 → SSE 报文（一行 retry + 一行 data）。"""
    payload = json.dumps(event, ensure_ascii=False)
    if retry_ms is not None:
        return f"retry: {int(retry_ms)}\n\ndata: {payload}\n\n"
    return f"data: {payload}\n\n"


def watch_events(
    root: str | Path,
    *,
    project_id: str = "",
    interval: float = 1.0,
    max_events: int = 0,
    max_idle: float = 0.0,
    sleep_fn: Any = time.sleep,
    clock: Any = time.monotonic,
) -> Iterator[dict[str, Any]]:
    """事件生成器：``hello`` → 若干 ``changed`` →（可选）``bye``。

    - ``project_id``：只盯一个（看板详情页用），空=盯全部（列表页用）
    - ``max_events``：推满即止（测试 / 一次性客户端用）
    - ``max_idle``：静默超时即收线（防连接泄漏）；0=不收线
    - ``sleep_fn`` / ``clock``：注入用，测试不必真睡
    """
    base = Path(root)
    raw_interval = float(interval) if interval is not None else 1.0
    step = max(MIN_INTERVAL, raw_interval)
    wanted = str(project_id or "")

    def _snapshot() -> dict[str, tuple[Path, str]]:
        out: dict[str, tuple[Path, str]] = {}
        for pid, project_dir in discover_targets(base):
            if wanted and pid != wanted:
                continue
            fp = fingerprint(project_dir)
            # 空指纹（没有 watched 文件）≠ 「没见过这个项目」：用哨兵区分，否则
            # 新建项目的首次变化推不出来（'' == ''），流会静默死循环。
            out[pid] = (project_dir, fp if fp else "<empty>")
        return out

    current = _snapshot()
    retry_ms = int(step * 1000 * 2)
    yield {"type": "hello", "retry_ms": retry_ms, "interval_ms": int(step * 1000), "projects": sorted(current)}
    emitted = 1
    if max_events and emitted >= max_events:
        return
    idle_since = clock()
    while True:
        sleep_fn(step)
        snapshot = _snapshot()

        def _known(pid: str) -> str:
            entry = current.get(pid)
            return entry[1] if entry else ""

        changed = sorted(pid for pid, (_dir, fp) in snapshot.items() if _known(pid) != fp)
        changed += sorted(pid for pid in current if pid not in snapshot)
        if changed:
            idle_since = clock()
            for pid in dict.fromkeys(changed):
                entry = snapshot.get(pid)
                if entry is not None:
                    current[pid] = entry
                else:
                    current.pop(pid, None)
                    entry = (Path(), "")
                yield {
                    "type": "changed",
                    "project_id": pid,
                    "status": progress_status(entry[0]) if entry[1] else "",
                }
                emitted += 1
                if max_events and emitted >= max_events:
                    return
        elif max_idle and clock() - idle_since >= float(max_idle):
            yield {"type": "bye", "reason": "idle"}
            return
