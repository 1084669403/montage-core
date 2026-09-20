"""generation_cache — 生成缓存工具（重跑不重复付费）。

按 ``(prompt, provider, seed, params...)`` 的稳定哈希缓存生成产物：
- ``get``：命中返回缓存路径（hit=True），未命中返回 miss；
- ``put``：把新生成的文件复制进缓存目录并记录索引（index.json）；
- 索引落在 ``projects/<id>/assets/.cache/``，跨进程持久。

设计：缓存只做"文件复制 + 索引"，不校验内容（质量门禁由 asset_quality_gate
负责）；key 由参数 dict 的排序 JSON 哈希，顺序无关、内容敏感。
"""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import tempfile
import threading
from pathlib import Path
from typing import Any

from montage.toolbase import BaseTool, ToolResult, ToolRuntime, ToolStatus


def cache_key(**params: Any) -> str:
    """稳定缓存键：排序 JSON → sha1 前 16 位。"""
    blob = json.dumps(params, ensure_ascii=False, sort_keys=True, default=str)
    return hashlib.sha1(blob.encode("utf-8")).hexdigest()[:16]


class GenerationCache(BaseTool):
    """生成缓存：get / put / stats。"""

    name = "generation_cache"
    version = "0.1.0"
    capability = "cache"
    provider = "openmontage"
    runtime = ToolRuntime.LOCAL
    input_schema = {
        "type": "object",
        "required": ["operation"],
        "properties": {
            "operation": {"type": "string", "enum": ["get", "put", "stats"]},
            "cache_dir": {"type": "string", "description": "缓存目录（默认 projects/<id>/assets/.cache 由调用方给出）"},
            "key": {"type": "string", "description": "可选：显式缓存键"},
            "params": {"type": "object", "description": "可选：生成参数（prompt/provider/seed/...），自动算 key"},
            "path": {"type": "string", "description": "put 时的新生成文件路径"},
        },
    }

    def __init__(self, cache_dir: str | Path | None = None) -> None:
        self.cache_dir = Path(cache_dir) if cache_dir else Path("assets/.cache")
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        self._index_path = self.cache_dir / "index.json"
        self._index: dict[str, str] = {}
        self._index_lock = threading.RLock()
        if self._index_path.exists():
            try:
                self._index = json.loads(self._index_path.read_text(encoding="utf-8"))
            except (json.JSONDecodeError, OSError):
                self._index = {}

    def _save_index(self) -> None:
        """???????mkstemp ??????????? index.tmp?"""
        fd, tmp_name = tempfile.mkstemp(
            prefix=self._index_path.name + ".",
            suffix=".tmp",
            dir=str(self.cache_dir),
        )
        tmp = Path(tmp_name)
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as fh:
                json.dump(self._index, fh, ensure_ascii=False, indent=2)
            os.replace(tmp, self._index_path)
        finally:
            if tmp.exists():
                tmp.unlink()

    def _resolve_key(self, inputs: dict[str, Any]) -> tuple[str, str]:
        """返回 (key, 错误信息)；错误时 key 为空串。"""
        key = inputs.get("key") or ""
        params = inputs.get("params")
        if not key and isinstance(params, dict) and params:
            key = cache_key(**params)
        if not key:
            return "", "缺少 key 或 params（用于计算缓存键）"
        return key, ""

    def get(self, inputs: dict[str, Any]) -> ToolResult:
        key, err = self._resolve_key(inputs)
        if err:
            return ToolResult(success=False, error=err)
        path = self._index.get(key)
        if path and Path(path).exists():
            return ToolResult(success=True, data={"hit": True, "key": key, "path": path})
        return ToolResult(success=True, data={"hit": False, "key": key, "path": None})

    def put(self, inputs: dict[str, Any]) -> ToolResult:
        key, err = self._resolve_key(inputs)
        if err:
            return ToolResult(success=False, error=err)
        src = inputs.get("path")
        if not src or not Path(src).exists():
            return ToolResult(success=False, error="'path' 必填且文件需存在")
        suffix = Path(src).suffix or ".bin"
        dest = self.cache_dir / f"{key}{suffix}"
        with self._index_lock:
            shutil.copy2(src, dest)
            self._index[key] = str(dest)
            self._save_index()
        return ToolResult(success=True, data={"key": key, "cached_path": str(dest)})

    def stats(self) -> ToolResult:
        return ToolResult(
            success=True,
            data={"entries": len(self._index), "cache_dir": str(self.cache_dir)},
        )

    def execute(self, inputs: dict[str, Any]) -> ToolResult:
        operation = inputs.get("operation")
        if operation == "get":
            return self.get(inputs)
        if operation == "put":
            return self.put(inputs)
        if operation == "stats":
            return self.stats()
        return ToolResult(success=False, error=f"未知 operation: {operation}")

    def get_status(self) -> ToolStatus:
        return ToolStatus.AVAILABLE

    def estimate_cost(self, inputs: dict[str, Any]) -> float:
        return 0.0
