"""artifacts — 规范产物存取与可选校验。

产物是管线阶段之间唯一的契约：每个阶段写一个 artifacts/<name>.json，
下一阶段读取并（可选）按 JSON Schema 校验。

校验是公开方法（``validate``），供 gates 层（montage/engine/gates.py）在
checkpoint 完成时做产物完整性门禁复用；写入为**原子写**（同目录临时文件 +
``os.replace``），避免中断写坏产物。

本文件为全新原创代码。
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any


class ArtifactStore:
    def __init__(self, project_dir: str | Path) -> None:
        self.dir = Path(project_dir) / "artifacts"
        self.dir.mkdir(parents=True, exist_ok=True)

    def _path(self, name: str) -> Path:
        if not name.endswith(".json"):
            name += ".json"
        return self.dir / name

    def write(
        self,
        name: str,
        data: dict[str, Any],
        schema: dict[str, Any] | None = None,
    ) -> Path:
        """写入产物（原子写）；提供 schema 时校验，不通过则抛 ValueError。"""
        if schema is not None:
            errors = self.validate(data, schema)
            if errors:
                raise ValueError(f"产物 {name} 校验失败: {errors}")
        path = self._path(name)
        tmp = path.with_suffix(path.suffix + ".tmp")
        tmp.write_text(
            json.dumps(data, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        os.replace(tmp, path)
        return path

    def read(self, name: str) -> dict[str, Any] | None:
        path = self._path(name)
        if not path.exists():
            return None
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            return None

    def exists(self, name: str) -> bool:
        return self._path(name).exists()

    def list(self) -> list[str]:
        return sorted(p.stem for p in self.dir.glob("*.json"))

    @staticmethod
    def validate(data: dict[str, Any], schema: dict[str, Any]) -> list[str]:
        """按 JSON Schema 校验数据。jsonschema 为核心依赖，缺失视为环境损坏。"""
        try:
            import jsonschema  # type: ignore
        except ImportError as exc:
            raise RuntimeError("缺少 jsonschema：pip install -e .") from exc
        errors: list[str] = []
        for err in jsonschema.Draft7Validator(schema).iter_errors(data):
            errors.append(f"{'.'.join(str(p) for p in err.path)}: {err.message}")
        return errors
