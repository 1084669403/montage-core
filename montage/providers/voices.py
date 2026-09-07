"""voices — 中文音色表（数据，零依赖）。

voice_director 按 speaker / 人物卡 voice_id / 性别 解析到具体供应商音色。
不调用 TTS；真正合成走 tts_selector。
"""

from __future__ import annotations

from typing import Any

# providers 键：doubao 用 voice_id；edge_tts 用 voice；piper 用 model
# 豆包列默认 2.0 音色（2026-09-04 实测当前账号仅授权 seed-tts-2.0；
# 1.0 moon/mars/saturn 系音色会报 45000030，保留在注释里便于已购 1.0 的账号切换）。
VOICES: list[dict[str, Any]] = [
    {
        "id": "female_soft",
        "gender": "female",
        "use": ("narrator", "protagonist", "supporting"),
        "label": "女声·软（旁白/女主）",
        "providers": {
            # 1.0 备选：zh_female_wanwanxiaohe_moon_bigtts
            "doubao": "zh_female_xiaohe_uranus_bigtts",
            "edge_tts": "zh-CN-XiaoxiaoNeural",
            "piper": "zh_CN-haru-medium",
        },
    },
    {
        "id": "male_low",
        "gender": "male",
        "use": ("protagonist", "antagonist", "supporting"),
        "label": "男声·低（男主/对手）",
        "providers": {
            # 1.0 备选：zh_male_yunzhou_mars_bigtts
            "doubao": "zh_male_m191_uranus_bigtts",
            "edge_tts": "zh-CN-YunxiNeural",
            "piper": "zh_CN-huayan-medium",
        },
    },
    {
        "id": "male_broadcast",
        "gender": "male",
        "use": ("narrator",),
        "label": "男声·播音",
        "providers": {
            # 1.0 备选：zh_male_chunhou_saturn_bigtts
            "doubao": "zh_male_liufei_uranus_bigtts",
            "edge_tts": "zh-CN-YunyangNeural",
            "piper": "zh_CN-huayan-medium",
        },
    },
    {
        "id": "female_bright",
        "gender": "female",
        "use": ("supporting", "antagonist"),
        "label": "女声·亮",
        "providers": {
            # 1.0 备选：zh_female_shuangkuaisisi_moon_bigtts
            "doubao": "zh_female_cancan_uranus_bigtts",
            "edge_tts": "zh-CN-XiaoyiNeural",
            "piper": "zh_CN-haru-medium",
        },
    },
    {
        "id": "female_vivid",
        "gender": "female",
        "use": ("narrator", "protagonist", "antagonist", "supporting"),
        "label": "女声·灵动（2.0 高表现力，支持自然语言情感）",
        "providers": {
            "doubao": "zh_female_vv_uranus_bigtts",
            "edge_tts": "zh-CN-XiaoxiaoNeural",
            "piper": "zh_CN-haru-medium",
        },
    },
    {
        "id": "male_calm",
        "gender": "male",
        "use": ("narrator", "protagonist", "supporting"),
        "label": "男声·沉稳（2.0 高表现力，支持自然语言情感）",
        "providers": {
            "doubao": "zh_male_liufei_uranus_bigtts",
            "edge_tts": "zh-CN-YunyangNeural",
            "piper": "zh_CN-huayan-medium",
        },
    },
]

_DEFAULT_ID = "female_soft"
_PROVIDER_DEFAULT = "doubao"


def list_voices(provider: str | None = None) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for row in VOICES:
        item = {
            "id": row["id"],
            "gender": row["gender"],
            "use": list(row["use"]),
            "label": row["label"],
            "providers": dict(row["providers"]),
        }
        if provider:
            native = (row["providers"] or {}).get(provider)
            if not native:
                continue
            item["native"] = native
        out.append(item)
    return out


def _native(row: dict[str, Any], provider: str) -> str:
    return str((row.get("providers") or {}).get(provider) or "")


def _all_natives() -> dict[str, dict[str, Any]]:
    """native voice 字符串 → 表行（任意供应商）。"""
    found: dict[str, dict[str, Any]] = {}
    for row in VOICES:
        found[row["id"]] = row
        for native in (row.get("providers") or {}).values():
            if native:
                found[str(native)] = row
    return found


def resolve_voice(
    *,
    voice_id: str = "",
    speaker_id: str = "",
    gender: str = "",
    role: str = "",
    provider: str = "",
) -> dict[str, Any]:
    """解析音色。优先级：显式 voice_id → 性别/角色 → 默认女声软。"""
    prov = (provider or _PROVIDER_DEFAULT).strip() or _PROVIDER_DEFAULT
    table = _all_natives()
    vid = (voice_id or "").strip()
    row: dict[str, Any] | None = table.get(vid) if vid else None
    if row is None and vid:
        # 未知 id 当作该供应商的原生音色名直接用
        return {
            "id": vid,
            "provider": prov,
            "native": vid,
            "gender": gender,
            "matched": "passthrough",
        }

    if row is None:
        g = (gender or "").strip().lower()
        r = (role or speaker_id or "").strip().lower()
        if r in ("narrator", "旁白"):
            row = next((x for x in VOICES if "narrator" in x["use"] and (not g or x["gender"] == g)), None)
        if row is None and g:
            row = next((x for x in VOICES if x["gender"] == g), None)
        if row is None and r:
            row = next((x for x in VOICES if r in x["use"]), None)
        if row is None:
            row = next(x for x in VOICES if x["id"] == _DEFAULT_ID)

    native = _native(row, prov) or _native(row, _PROVIDER_DEFAULT)
    return {
        "id": row["id"],
        "provider": prov,
        "native": native,
        "gender": row["gender"],
        "matched": "table",
    }
