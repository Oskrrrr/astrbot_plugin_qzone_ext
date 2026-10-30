"""QQ空间说说管家：定时发说说 + 自动回评，不依赖官方的 astrbot_plugin_qzone。

设计取向：
- 所有写入操作默认 dry-run，确认无误再把 dry_run 关掉；
- 机器人没连上 / Cookie 拿不到 时只记一条日志并跳过本轮，不报错、不补跑；
- 状态（今日计数、已回评论）落在 plugin_data，重启不重复动作。
"""

from __future__ import annotations

import asyncio
import json
import os
import random
import re
import time
from datetime import datetime
from pathlib import Path
from typing import Any

from astrbot.api import logger
from astrbot.api.event import filter
from astrbot.api.star import Context, Star
from astrbot.core import AstrBotConfig

try:
    from quart import jsonify as _jsonify
    from quart import request as _request
except Exception:  # pragma: no cover
    _jsonify = None
    _request = None

from .client import (
    QzoneClient,
    QzoneError,
    comment_id,
    comment_text,
    comment_time,
    comments_of,
)

PLUGIN_NAME = "astrbot_plugin_qzone_ext"

DEFAULT_POST_PROMPT = """写一条 QQ 空间说说，要求：
- 第一人称，像真人在手机上随手发的一条，40～80 字；
- 口语化，可以写此刻的心情、天气、刚发生的一件小事；
- 不要提 AI、机器人、模型；不要话题标签；不要用引号包住正文；
- 只输出正文本身。"""

DEFAULT_REPLY_PROMPT = """有人评论了你发的说说。写一句回复，要求：
- 15～40 字，口语、自然，像本人随口接一句；
- 不要客套，不要"感谢支持"这类话，不要用反问句收尾；
- 只输出回复正文。"""

DEFAULT_COMMENT_PROMPT = """给好友的说说写一句评论，要求：
- 10～25 字，口语、自然，像本人随手评论；
- 贴题，可以吐槽、关心、接梗，但不要阴阳怪气；
- 不要客套，不要用反问句收尾；不要提 AI、机器人；
- 只输出评论正文。"""

DEFAULT_STOCK_DIR = "/AstrBot/data/plugin_data/astrbot_plugin_qzone_ext/images"

DEFAULT_IMAGE_RULES = """配图要求：
- 只画场景与静物：街角、房间、桌面、窗台、天台、便利店、路边摊、走廊；
- 不要出现人物、人影、背影、手、脚和身体部位，可以有"刚有人离开"的痕迹（半杯饮料、亮着的台灯、搭在椅背上的外套）；
- 正方形构图，主体在中近景，留白干净，不要分镜、拼贴、对话框；
- 不要任何文字、字母、数字、招牌字样、书本和屏幕上的内容、水印、logo、边框；
- 水彩质感，柔和光影，色彩克制不刺眼。no people, no hands, no text, no letters, no watermark."""

DEFAULT_IMAGE_DIRECTOR_PROMPT = """你在给一条 QQ 空间说说配图。根据说说内容和给定的时间、天气，写一段画面描述，交给画图模型。

要求：
- 只写画面：场景、两三个具体物件、光线方向与色温、景别；60～110 字；
- 用景物的痕迹暗示情绪，不写心情词、不写评价、不写"表达""氛围"这类空话；
- 不画人：人物只能以痕迹出现（没喝完的杯子、搭在椅背的外套、亮着的台灯）；
- 不描写任何文字内容（书页、屏幕、招牌都不写）；
- 季节、天气、昼夜只能用给定的信息，不许自己编（没下雪就别写雪，白天别写星空）；
- 不写画风词（风格由后面的固定要求决定）；
- 只输出画面描述本身，不要解释、不要分点、不要引号。"""

WMO_WEATHER = {
    0: "晴", 1: "晴间多云", 2: "多云", 3: "阴",
    45: "雾", 48: "雾凇",
    51: "毛毛雨", 53: "小雨", 55: "中雨", 56: "冻雨", 57: "冻雨",
    61: "小雨", 63: "中雨", 65: "大雨", 66: "冻雨", 67: "冻雨",
    71: "小雪", 73: "中雪", 75: "大雪", 77: "雪粒",
    80: "阵雨", 81: "阵雨", 82: "强阵雨", 85: "阵雪", 86: "阵雪",
    95: "雷阵雨", 96: "雷阵雨伴冰雹", 99: "强雷暴伴冰雹",
}

SEASON_ALIASES = {
    "春天": ["春", "spring"],
    "夏天": ["夏", "summer"],
    "秋天": ["秋", "autumn", "fall"],
    "冬天": ["冬", "winter"],
}

PERIOD_ALIASES = {
    "清晨": ["清晨", "早晨", "morning", "dawn"],
    "上午": ["上午", "morning"],
    "中午": ["中午", "noon", "midday"],
    "下午": ["下午", "afternoon"],
    "晚上": ["晚上", "夜", "night", "evening"],
    "深夜": ["深夜", "夜", "night"],
}


def _season_of(month: int) -> str:
    if month in (12, 1, 2):
        return "冬天"
    if month in (3, 4, 5):
        return "春天"
    if month in (6, 7, 8):
        return "夏天"
    return "秋天"


def _period_of(hour: int) -> str:
    if 5 <= hour < 9:
        return "清晨"
    if 9 <= hour < 12:
        return "上午"
    if 12 <= hour < 14:
        return "中午"
    if 14 <= hour < 18:
        return "下午"
    if 18 <= hour < 23:
        return "晚上"
    return "深夜"


def _data_dir() -> Path:
    try:
        from astrbot.core.utils.astrbot_path import get_astrbot_plugin_data_path

        root = Path(get_astrbot_plugin_data_path())
    except Exception:
        root = Path("/AstrBot/data/plugin_data")
    return root / PLUGIN_NAME


def _options_from_config(config) -> dict[str, Any]:
    """把 AstrBotConfig 读成一份带默认值的扁平配置。"""

    def get(key: str, default: Any) -> Any:
        try:
            return config.get(key, default)
        except Exception:
            return default

    def as_int(key: str, default: int) -> int:
        try:
            return int(get(key, default) or default)
        except Exception:
            return default

    def as_float(key: str, default: float) -> float:
        try:
            return float(get(key, default) or default)
        except Exception:
            return default

    material_enabled = get("material_enabled", None)
    if material_enabled is None:  # 兼容旧配置：material_source == "group"
        material_enabled = str(get("material_source", "") or "").strip() == "group"

    return {
        "enabled": bool(get("enabled", False)),
        "dry_run": bool(get("dry_run", True)),
        "persona_id": str(get("persona_id", "") or "").strip(),
        "provider_id": str(get("provider_id", "") or "").strip(),
        "publish_enabled": bool(get("publish_enabled", True)),
        "publish_offset_minutes": as_int("publish_offset_minutes", 50),
        "publish_skip_probability": as_float("publish_skip_probability", 0.85),
        "daily_limit": as_int("daily_limit", 3),
        "publish_hours": str(get("publish_hours", "8-23") or "").strip(),
        "post_prompt": str(get("post_prompt", "") or ""),
        "attach_image": bool(get("attach_image", False)),
        "image_prompt": str(get("image_prompt", "") or ""),
        "dynamic_image_prompt": bool(get("dynamic_image_prompt", False)),
        "image_negative": str(get("image_negative", "") or ""),
        "image_director_prompt": str(get("image_director_prompt", "") or ""),
        "inject_time": bool(get("inject_time", True)),
        "inject_weather": bool(get("inject_weather", False)),
        "weather_city": str(get("weather_city", "") or "").strip(),
        "stock_image_enabled": bool(get("stock_image_enabled", False)),
        "stock_image_dir": str(get("stock_image_dir", "") or "") or DEFAULT_STOCK_DIR,
        "stock_image_ratio": as_float("stock_image_ratio", 0.5),
        "material_enabled": bool(material_enabled),
        "material_groups": str(get("material_groups", "") or ""),
        "material_ignore_groups": str(get("material_ignore_groups", "") or ""),
        "material_max_messages": as_int("material_max_messages", 150),
        "require_persona": bool(get("require_persona", True)),
        "interact_enabled": bool(get("interact_enabled", False)),
        "interact_interval_minutes": as_int("interact_interval_minutes", 60),
        "interact_max_per_run": as_int("interact_max_per_run", 2),
        "interact_like": bool(get("interact_like", True)),
        "interact_ignore_users": str(get("interact_ignore_users", "") or ""),
        "interact_min_chars": as_int("interact_min_chars", 8),
        "comment_prompt": str(get("comment_prompt", "") or ""),
        "reply_enabled": bool(get("reply_enabled", True)),
        "reply_interval_minutes": as_int("reply_interval_minutes", 15),
        "reply_max_per_run": as_int("reply_max_per_run", 3),
        "reply_min_age_seconds": as_int("reply_min_age_seconds", 60),
        "reply_prompt": str(get("reply_prompt", "") or ""),
        "max_reply_chars": as_int("max_reply_chars", 80),
    }


def _normalize_options(data: dict[str, Any]) -> dict[str, Any]:
    """校验并落成可保存的配置；不合法时抛 ValueError。"""

    def as_bool(key: str, default: bool) -> bool:
        value = data.get(key, default)
        if isinstance(value, str):
            return value.strip().lower() in ("1", "true", "yes", "on")
        return bool(value)

    def as_int(key: str, default: int, low: int, high: int) -> int:
        try:
            value = int(float(data.get(key, default)))
        except Exception as exc:
            raise ValueError(f"{key} 需要是数字") from exc
        return max(low, min(high, value))

    def as_ratio(key: str, default: float = 0.5) -> float:
        try:
            value = float(data.get(key, default))
        except Exception:
            return default
        return max(0.0, min(1.0, value))

    skip = data.get("publish_skip_probability", 0.85)
    try:
        skip_value = float(skip)
    except Exception as exc:
        raise ValueError("跳过概率需要是 0～1 的小数") from exc
    if not 0.0 <= skip_value <= 0.99:
        raise ValueError("跳过概率要在 0～0.99 之间")

    hours = str(data.get("publish_hours", "") or "").strip()
    if hours and not re.fullmatch(r"\d{1,2}(-\d{1,2})?", hours):
        raise ValueError("发布时段格式应为 8-23 或留空")

    return {
        "enabled": as_bool("enabled", False),
        "dry_run": as_bool("dry_run", True),
        "persona_id": str(data.get("persona_id", "") or "").strip()[:40],
        "provider_id": str(data.get("provider_id", "") or "").strip()[:80],
        "publish_enabled": as_bool("publish_enabled", True),
        "publish_offset_minutes": as_int("publish_offset_minutes", 50, 0, 59),
        "publish_skip_probability": round(skip_value, 3),
        "daily_limit": as_int("daily_limit", 3, 1, 20),
        "publish_hours": hours,
        "post_prompt": str(data.get("post_prompt", "") or "")[:2000],
        "attach_image": as_bool("attach_image", False),
        "image_prompt": str(data.get("image_prompt", "") or "")[:600],
        "dynamic_image_prompt": as_bool("dynamic_image_prompt", False),
        "image_negative": str(data.get("image_negative", "") or "")[:500],
        "image_director_prompt": str(data.get("image_director_prompt", "") or "")[:2000],
        "inject_time": as_bool("inject_time", True),
        "inject_weather": as_bool("inject_weather", False),
        "weather_city": str(data.get("weather_city", "") or "")[:40],
        "stock_image_enabled": as_bool("stock_image_enabled", False),
        "stock_image_dir": str(data.get("stock_image_dir", "") or "")[:300]
        or DEFAULT_STOCK_DIR,
        "stock_image_ratio": as_ratio("stock_image_ratio", 0.5),
        "material_enabled": as_bool("material_enabled", False),
        "material_groups": str(data.get("material_groups", "") or "")[:400],
        "material_ignore_groups": str(data.get("material_ignore_groups", "") or "")[:400],
        "material_max_messages": as_int("material_max_messages", 150, 20, 500),
        "require_persona": as_bool("require_persona", True),
        "interact_enabled": as_bool("interact_enabled", False),
        "interact_interval_minutes": as_int("interact_interval_minutes", 60, 10, 1440),
        "interact_max_per_run": as_int("interact_max_per_run", 2, 1, 5),
        "interact_like": as_bool("interact_like", True),
        "interact_ignore_users": str(data.get("interact_ignore_users", "") or "")[:400],
        "interact_min_chars": as_int("interact_min_chars", 8, 1, 100),
        "comment_prompt": str(data.get("comment_prompt", "") or "")[:2000],
        "reply_enabled": as_bool("reply_enabled", True),
        "reply_interval_minutes": as_int("reply_interval_minutes", 15, 5, 1440),
        "reply_max_per_run": as_int("reply_max_per_run", 3, 1, 10),
        "reply_min_age_seconds": as_int("reply_min_age_seconds", 60, 0, 86400),
        "reply_prompt": str(data.get("reply_prompt", "") or "")[:2000],
        "max_reply_chars": as_int("max_reply_chars", 80, 10, 200),
    }


def _parse_ids(value: Any) -> set[str]:
    """把 "123,456 789" 这类输入拆成纯数字 ID 集合。"""
    parts = re.split(r"[,\s，、;；]+", str(value or ""))
    return {part for part in parts if part.isdigit()}


class QzoneExtPlugin(Star):
    def __init__(self, context: Context, config: AstrBotConfig):
        super().__init__(context)
        self.context = context
        self.config = config

        self._opts = _options_from_config(config)
        self._apply_options(self._opts)

        self._state_path = _data_dir() / "state.json"
        self._state: dict[str, Any] = {}
        self._load_state()

        self._client = QzoneClient(self._get_bot)
        self._tasks: list[asyncio.Task] = []
        self._dry_seen: set[str] = set()
        self._interact_seen: set[str] = set()
        self._register_web_apis()

    # ------------------------------------------------------------------ web api

    def _register_web_apis(self) -> None:
        routes = (
            ("selftest", self.api_selftest, ["GET"], "自检（取 Cookie / 读说说 / 生成示例文案）"),
            ("page/status", self.api_page_status, ["GET"], "页面：状态"),
            ("page/toggle", self.api_page_toggle, ["POST"], "页面：切换 dry-run"),
            ("page/publish", self.api_page_publish, ["POST"], "页面：试发一条"),
            ("page/reply", self.api_page_reply, ["POST"], "页面：检查并回复评论"),
            ("page/config", self.api_page_config, ["GET"], "页面：读取设置"),
            ("page/save", self.api_page_save, ["POST"], "页面：保存设置"),
        )
        for endpoint, handler, methods, desc in routes:
            self.context.register_web_api(
                f"/{PLUGIN_NAME}/{endpoint}", handler, methods, desc
            )

    async def api_page_status(self):
        try:
            return await self._json({"ok": True, "data": self._status_payload()})
        except Exception as exc:
            return await self._json({"ok": False, "error": {"message": str(exc)}})

    async def api_page_toggle(self):
        body = await self._body()
        if "dry_run" in body:
            value = bool(body.get("dry_run"))
            self.dry_run = value
            self._persist_config("dry_run", value)
            logger.info("[qzone_ext] dry_run 已%s", "打开" if value else "关闭")
        return await self._json({"ok": True, "data": self._status_payload()})

    async def api_page_publish(self):
        try:
            result = await self.publish_once(force=True, preview_image=True)
        except Exception as exc:
            logger.exception("[qzone_ext] 页面试发失败")
            return await self._json({"ok": False, "error": {"message": str(exc)}})
        return await self._json(
            {"ok": True, "data": {"result": result, "status": self._status_payload()}}
        )

    async def api_page_reply(self):
        try:
            result = await self.reply_once()
        except Exception as exc:
            logger.exception("[qzone_ext] 页面回评失败")
            return await self._json({"ok": False, "error": {"message": str(exc)}})
        return await self._json(
            {"ok": True, "data": {"result": result, "status": self._status_payload()}}
        )

    async def api_page_config(self):
        try:
            return await self._json(
                {
                    "ok": True,
                    "data": {
                        "values": self._options_payload(),
                        "options": {
                            "personas": self._persona_options(),
                            "providers": self._provider_options(),
                            "draw_available": self._draw_plugin() is not None,
                            "card_available": self._card_plugin() is not None,
                            "style_source": self._style_source(),
                            "defaults": {
                                "post_prompt": DEFAULT_POST_PROMPT,
                                "reply_prompt": DEFAULT_REPLY_PROMPT,
                                "comment_prompt": DEFAULT_COMMENT_PROMPT,
                                "image_prompt": DEFAULT_IMAGE_RULES,
                                "image_director_prompt": DEFAULT_IMAGE_DIRECTOR_PROMPT,
                            },
                        },
                    },
                }
            )
        except Exception as exc:
            return await self._json({"ok": False, "error": {"message": str(exc)}})

    async def api_page_save(self):
        body = await self._body()
        try:
            values = _normalize_options(body)
        except ValueError as exc:
            return await self._json({"ok": False, "error": {"message": str(exc)}})
        if values["persona_id"]:
            ids = {item["id"] for item in self._persona_options()}
            if values["persona_id"] not in ids:
                return await self._json({"ok": False, "error": {"message": "人格不存在"}})
        if values["provider_id"]:
            ids = {item["id"] for item in self._provider_options()}
            if values["provider_id"] not in ids:
                return await self._json({"ok": False, "error": {"message": "聊天模型不存在"}})
        self._persist_config_values(values)
        self._apply_options(values)
        await self._restart_tasks()
        logger.info(
            "[qzone_ext] 设置已保存（enabled=%s dry_run=%s）", self.enabled, self.dry_run
        )
        return await self._json(
            {
                "ok": True,
                "data": {"values": self._options_payload(), "status": self._status_payload()},
            }
        )

    def _apply_options(self, values: dict[str, Any]) -> None:
        self._opts = dict(values)
        self.enabled = bool(values.get("enabled"))
        self.dry_run = bool(values.get("dry_run"))
        self.persona_id = str(values.get("persona_id") or "").strip()
        self.provider_id = str(values.get("provider_id") or "").strip()
        self.publish_enabled = bool(values.get("publish_enabled"))
        self.offset_minutes = max(0, min(59, int(values.get("publish_offset_minutes") or 0)))
        self.skip_probability = min(
            0.99, max(0.0, float(values.get("publish_skip_probability") or 0.0))
        )
        self.daily_limit = max(1, min(20, int(values.get("daily_limit") or 3)))
        self.publish_hours = str(values.get("publish_hours") or "").strip()
        self.post_prompt = str(values.get("post_prompt") or "") or DEFAULT_POST_PROMPT
        self.attach_image = bool(values.get("attach_image"))
        self.image_prompt = str(values.get("image_prompt") or "").strip()
        self.image_rules = self.image_prompt or DEFAULT_IMAGE_RULES
        self.dynamic_image_prompt = bool(values.get("dynamic_image_prompt"))
        self.image_negative = str(values.get("image_negative") or "").strip()
        self.image_director_prompt = (
            str(values.get("image_director_prompt") or "").strip()
            or DEFAULT_IMAGE_DIRECTOR_PROMPT
        )
        self._last_image_prompt = ""
        self.inject_time = bool(values.get("inject_time"))
        self.inject_weather = bool(values.get("inject_weather"))
        self.weather_city = str(values.get("weather_city") or "").strip()
        self.stock_image_enabled = bool(values.get("stock_image_enabled"))
        self.stock_image_dir = str(values.get("stock_image_dir") or DEFAULT_STOCK_DIR)
        self.stock_image_ratio = float(values.get("stock_image_ratio") or 0.0)
        self._weather_cache: tuple[float, str] = (0.0, "")
        self._weather_city_cached = ""
        self.material_enabled = bool(values.get("material_enabled"))
        self.material_groups = _parse_ids(values.get("material_groups"))
        self.material_ignore_groups = _parse_ids(values.get("material_ignore_groups"))
        self.material_max = max(
            20, min(500, int(values.get("material_max_messages") or 150))
        )
        self.require_persona = bool(values.get("require_persona"))
        self.interact_enabled = bool(values.get("interact_enabled"))
        self.interact_interval = max(
            10, min(1440, int(values.get("interact_interval_minutes") or 60))
        )
        self.interact_max = max(1, min(5, int(values.get("interact_max_per_run") or 2)))
        self.interact_like = bool(values.get("interact_like"))
        self.interact_ignore_users = _parse_ids(values.get("interact_ignore_users"))
        self.interact_min_chars = max(1, int(values.get("interact_min_chars") or 8))
        self.comment_prompt = (
            str(values.get("comment_prompt") or "") or DEFAULT_COMMENT_PROMPT
        )
        self._persona_logged = False
        self.reply_enabled = bool(values.get("reply_enabled"))
        self.reply_interval = max(
            5, min(1440, int(values.get("reply_interval_minutes") or 15))
        )
        self.reply_max = max(1, min(10, int(values.get("reply_max_per_run") or 3)))
        self.reply_min_age = max(0, int(values.get("reply_min_age_seconds") or 0))
        self.reply_prompt = str(values.get("reply_prompt") or "") or DEFAULT_REPLY_PROMPT
        self.max_reply_chars = max(
            10, min(200, int(values.get("max_reply_chars") or 80))
        )

    def _options_payload(self) -> dict[str, Any]:
        return {
            "enabled": self.enabled,
            "dry_run": self.dry_run,
            "persona_id": self.persona_id,
            "provider_id": self.provider_id,
            "publish_enabled": self.publish_enabled,
            "publish_offset_minutes": self.offset_minutes,
            "publish_skip_probability": self.skip_probability,
            "daily_limit": self.daily_limit,
            "publish_hours": self.publish_hours,
            "post_prompt": str(self._opts.get("post_prompt") or ""),
            "attach_image": self.attach_image,
            "image_prompt": str(self._opts.get("image_prompt") or ""),
            "dynamic_image_prompt": self.dynamic_image_prompt,
            "image_negative": str(self._opts.get("image_negative") or ""),
            "image_director_prompt": str(self._opts.get("image_director_prompt") or ""),
            "inject_time": self.inject_time,
            "inject_weather": self.inject_weather,
            "weather_city": self.weather_city,
            "stock_image_enabled": self.stock_image_enabled,
            "stock_image_dir": self.stock_image_dir,
            "stock_image_ratio": self.stock_image_ratio,
            "material_enabled": self.material_enabled,
            "material_groups": str(self._opts.get("material_groups") or ""),
            "material_ignore_groups": str(self._opts.get("material_ignore_groups") or ""),
            "material_max_messages": self.material_max,
            "require_persona": self.require_persona,
            "interact_enabled": self.interact_enabled,
            "interact_interval_minutes": self.interact_interval,
            "interact_max_per_run": self.interact_max,
            "interact_like": self.interact_like,
            "interact_ignore_users": str(self._opts.get("interact_ignore_users") or ""),
            "interact_min_chars": self.interact_min_chars,
            "comment_prompt": str(self._opts.get("comment_prompt") or ""),
            "reply_enabled": self.reply_enabled,
            "reply_interval_minutes": self.reply_interval,
            "reply_max_per_run": self.reply_max,
            "reply_min_age_seconds": self.reply_min_age,
            "reply_prompt": str(self._opts.get("reply_prompt") or ""),
            "max_reply_chars": self.max_reply_chars,
        }

    def _persona_options(self) -> list[dict]:
        out: list[dict] = []
        manager = getattr(self.context, "persona_manager", None)
        for persona in getattr(manager, "personas_v3", []) or []:
            if not isinstance(persona, dict):
                continue
            name = str(persona.get("name") or "").strip()
            if name:
                out.append({"id": name, "name": name})
        return out

    def _provider_options(self) -> list[dict]:
        out: list[dict] = []
        manager = getattr(self.context, "provider_manager", None)
        for inst in getattr(manager, "provider_insts", []) or []:
            try:
                meta = inst.meta()
            except Exception:
                continue
            out.append(
                {
                    "id": str(getattr(meta, "id", "") or ""),
                    "model": str(getattr(meta, "model", "") or ""),
                }
            )
        return out

    def _persist_config_values(self, values: dict[str, Any]) -> None:
        try:
            self.config.update(values)
            self.config.save_config()
        except Exception:
            logger.warning("[qzone_ext] 写回配置失败", exc_info=True)

    def _status_payload(self) -> dict[str, Any]:
        history = self._state.get("history")
        if not isinstance(history, list):
            history = []
        return {
            "enabled": self.enabled,
            "dry_run": self.dry_run,
            "ready": self._ready(),
            "uin": self._client.cached_uin(),
            "hours": self.publish_hours,
            "today": self._today_count(),
            "daily_limit": self.daily_limit,
            "skip_probability": self.skip_probability,
            "reply_enabled": self.reply_enabled,
            "reply_interval": self.reply_interval,
            "attach_image": self.attach_image,
            "material_enabled": self.material_enabled,
            "persona_id": self.persona_id,
            "draw_available": self._draw_plugin() is not None,
            "replied_count": len(self._state.get("replied") or {}),
            "history": history[-8:][::-1],
        }

    # ------------------------------------------------------------------ 配图

    def _card_plugin(self):
        """向宿主要「角色卡」插件实例；没装就返回 None。"""
        try:
            star = self.context.get_registered_star("astrbot_plugin_persona_card")
        except Exception:
            return None
        return getattr(star, "star_cls", None) if star else None

    def _card_profile(self) -> dict[str, str]:
        """当前人格对应角色卡的「说说」板块；没有就返回空。"""
        name = str(self.persona_id or "").strip()
        if not name:
            return {}
        plugin = self._card_plugin()
        getter = getattr(plugin, "qzone_profile", None)
        if getter is None:
            return {}
        try:
            data = getter(name)
        except Exception:
            logger.warning("[qzone_ext] 读角色卡的说出版块失败", exc_info=True)
            return {}
        return data if isinstance(data, dict) else {}

    def _image_rules(self) -> str:
        profile = self._card_profile()
        return str(profile.get("image_requirements") or "").strip() or self.image_rules

    def _image_style(self) -> str | None:
        profile = self._card_profile()
        style = str(profile.get("image_style") or "").strip()
        return style or None

    def _image_negative(self) -> str | None:
        profile = self._card_profile()
        value = str(profile.get("image_negative") or "").strip() or self.image_negative
        return value or None

    def _style_source(self) -> dict[str, str]:
        profile = self._card_profile()
        card_style = str(profile.get("image_style") or "").strip()
        if card_style:
            return {
                "kind": "card",
                "label": f"角色卡「{profile.get('card_name') or self.persona_id}」",
                "text": card_style,
            }
        draw = self._draw_plugin()
        draw_style = ""
        try:
            if draw is not None:
                draw_style = str(getattr(draw, "style", "") or "").strip()
        except Exception:
            draw_style = ""
        if draw_style:
            return {"kind": "draw", "label": "画图插件", "text": draw_style}
        return {"kind": "none", "label": "无（不追加风格）", "text": ""}

    def _draw_plugin(self):
        """向宿主要「画图」插件的实例；没有就返回 None（功能降级，不报错）。"""
        try:
            star = self.context.get_registered_star("astrbot_plugin_draw")
        except Exception:
            return None
        return getattr(star, "star_cls", None) if star else None

    def _time_context(self) -> str:
        now = datetime.now()
        weekdays = ["周一", "周二", "周三", "周四", "周五", "周六", "周日"]
        return (
            f"现在是 {now.strftime('%Y-%m-%d %H:%M')}（{weekdays[now.weekday()]}，"
            f"{_season_of(now.month)}，{_period_of(now.hour)}）"
        )

    async def _weather_context(self) -> str:
        if not self.weather_city:
            return ""
        now_ts = time.time()
        if (
            self._weather_cache[0]
            and now_ts - self._weather_cache[0] < 1800
            and self._weather_city_cached == self.weather_city
        ):
            return self._weather_cache[1]
        try:
            import aiohttp

            timeout = aiohttp.ClientTimeout(total=12)
            async with aiohttp.ClientSession(timeout=timeout) as session:
                async with session.get(
                    "https://geocoding-api.open-meteo.com/v1/search",
                    params={
                        "name": self.weather_city,
                        "count": 1,
                        "language": "zh",
                        "format": "json",
                    },
                ) as resp:
                    geo = await resp.json()
                results = (geo or {}).get("results") or []
                if not results:
                    logger.warning("[qzone_ext] 没找到城市：%s", self.weather_city)
                    return ""
                lat = results[0].get("latitude")
                lon = results[0].get("longitude")
                async with session.get(
                    "https://api.open-meteo.com/v1/forecast",
                    params={
                        "latitude": lat,
                        "longitude": lon,
                        "current": "temperature_2m,weather_code,wind_speed_10m,is_day",
                        "timezone": "auto",
                    },
                ) as resp:
                    data = await resp.json()
            current = (data or {}).get("current") or {}
            code = int(current.get("weather_code", -1))
            desc = WMO_WEATHER.get(code, "天气未知")
            temp = current.get("temperature_2m")
            if temp is not None:
                desc += f"，{round(float(temp))}°C"
            city = str(results[0].get("name") or self.weather_city)
            text = f"{city}{desc}"
            self._weather_cache = (now_ts, text)
            self._weather_city_cached = self.weather_city
            return text
        except Exception as exc:
            logger.warning("[qzone_ext] 获取天气失败：%s", exc)
            return ""

    def _pick_stock_image(self, desc: str = "") -> str:
        """按季节/时段/天气 + 画面描述里的关键词打分，并在最近用过的图里绕开。"""
        try:
            root = Path(self.stock_image_dir)
            if not root.is_dir():
                return ""
            suffixes = {".png", ".jpg", ".jpeg", ".webp"}
            files = [p for p in root.rglob("*") if p.suffix.lower() in suffixes]
            if not files:
                return ""

            now = datetime.now()
            words = list(SEASON_ALIASES.get(_season_of(now.month), []))
            words += PERIOD_ALIASES.get(_period_of(now.hour), [])
            weather_text = self._weather_cache[1] if self.inject_weather else ""
            for key, label in (("雨", "雨"), ("雪", "雪"), ("晴", "晴")):
                if key in weather_text:
                    words.append(label)
            for token in re.split(r"[^\u4e00-\u9fa5A-Za-z0-9]+", desc or ""):
                token = token.strip().lower()
                if len(token) >= 2:
                    words.append(token)

            recent = self._stock_recent()

            def score(path: Path) -> int:
                name = path.stem.lower()
                hit = sum(2 for word in words if word and word in name)
                return hit - (6 if str(path) in recent else 0)

            scored = [(score(path), random.random(), path) for path in files]
            scored.sort(key=lambda item: (-item[0], item[1]))
            best = scored[0][0]
            pool = [path for value, _, path in scored if value == best]
            chosen = str(random.choice(pool))
            self._remember_stock(chosen)
            return chosen
        except Exception as exc:
            logger.warning("[qzone_ext] 读取预存图库失败：%s", exc)
            return ""

    def _stock_recent(self) -> list[str]:
        value = self._state.get("stock_recent")
        return [str(item) for item in value] if isinstance(value, list) else []

    def _remember_stock(self, path: str) -> None:
        recent = self._stock_recent()
        recent = [item for item in recent if item != path]
        recent.append(path)
        self._state["stock_recent"] = recent[-5:]
        self._save_state()

    def _stock_image_count(self) -> int:
        try:
            root = Path(self.stock_image_dir)
            if not root.is_dir():
                return 0
            suffixes = {".png", ".jpg", ".jpeg", ".webp"}
            return sum(1 for p in root.rglob("*") if p.suffix.lower() in suffixes)
        except Exception:
            return 0

    async def _image_scene(self, text: str) -> str:
        """让聊天模型根据正文 + 时间天气写一段画面描述；失败返回空串。"""
        if not self.dynamic_image_prompt:
            return ""
        parts = [f"说说内容：{text}"]
        if self.inject_time:
            parts.append(f"当前时间：{self._time_context()}")
        if self.inject_weather:
            weather = await self._weather_context()
            if weather:
                parts.append(f"当前天气：{weather}")
        instruction = self.image_director_prompt
        scene = await self._generate(
            instruction, "\n".join(parts), use_persona=False
        )
        scene = " ".join(scene.split())[:300]
        if scene:
            logger.info("[qzone_ext] 画面描述：%s", scene)
        return scene

    async def _prepare_images(self, text: str, scene: str = "") -> tuple[list[str], str]:
        """配图：按概率优先用预存图，否则调画图插件生成。返回 (路径, 来源)。"""
        self._last_image_prompt = ""
        if not self.attach_image:
            return [], ""
        if self.stock_image_enabled and random.random() < self.stock_image_ratio:
            path = self._pick_stock_image(scene or text)
            if path:
                logger.info("[qzone_ext] 使用预存图：%s", path)
                return [path], "stock"
            logger.info("[qzone_ext] 预存图库为空，改用生成配图")
        return await self._make_post_image(text, scene), "generated"

    async def _call_draw(self, prompt: str) -> list[str]:
        """调画图插件。风格：角色卡 → 画图插件；负面词：角色卡/管家 → 画图插件。"""
        draw = self._draw_plugin()
        if draw is None or not hasattr(draw, "generate"):
            raise RuntimeError("没找到可用的画图插件")
        style = self._image_style()
        negative = self._image_negative()
        try:
            return await draw.generate(prompt, style=style, negative=negative)
        except TypeError:
            pass
        try:
            return await draw.generate(prompt, style=style or "")
        except TypeError:
            return await draw.generate(prompt)

    async def _make_post_image(self, text: str, scene: str = "") -> list[str]:
        """调画图插件生成一张图，返回本地路径；失败返回空列表。"""
        rules = self._image_rules()
        if scene:
            prompt = f"{scene}\n\n{rules}"
        else:
            prompt = f"{rules}\n\n说说内容：{text}"
        self._last_image_prompt = prompt
        try:
            paths = await self._call_draw(prompt)
        except Exception as exc:
            logger.warning("[qzone_ext] 画图插件生成失败：%s", exc)
            return []
        out = [str(path) for path in list(paths)[:1] if path]
        if out:
            logger.info("[qzone_ext] 已生成配图：%s", out[0])
        return out

    @staticmethod
    def _read_images(paths: list[str]) -> list[bytes]:
        images: list[bytes] = []
        for path in paths:
            try:
                images.append(Path(path).read_bytes())
            except Exception:
                logger.warning("[qzone_ext] 读取配图失败：%s", path)
        return images

    @staticmethod
    def _image_preview(path: str, max_side: int = 640) -> dict[str, Any]:
        """给页面看的缩略图（data URL）+ 原图路径。"""
        data_url = ""
        try:
            import base64
            import io

            from PIL import Image

            with Image.open(path) as img:
                rgb = img.convert("RGB")
                rgb.thumbnail((max_side, max_side))
                buffer = io.BytesIO()
                rgb.save(buffer, format="JPEG", quality=80)
                data_url = "data:image/jpeg;base64," + base64.b64encode(
                    buffer.getvalue()
                ).decode()
        except Exception:
            try:
                import base64

                data_url = "data:image/png;base64," + base64.b64encode(
                    Path(path).read_bytes()
                ).decode()
            except Exception:
                data_url = ""
        return {"path": path, "data": data_url}

    def _persist_config(self, key: str, value: Any) -> None:
        self._persist_config_values({key: value})

    def _push_history(self, entry: dict[str, Any]) -> None:
        history = self._state.get("history")
        if not isinstance(history, list):
            history = []
        history.append(entry)
        self._state["history"] = history[-20:]
        self._save_state()

    async def _body(self) -> dict:
        if _request is None:
            return {}
        try:
            data = await _request.get_json(silent=True)
        except TypeError:
            data = await _request.get_json()
        except Exception:
            return {}
        return data if isinstance(data, dict) else {}

    @staticmethod
    async def _json(payload: dict) -> Any:
        if _jsonify is None:
            return payload
        return _jsonify(payload)

    async def api_selftest(self):
        try:
            data = await self._selftest()
            want_publish = ""
            if _request is not None:
                try:
                    want_publish = str((_request.args or {}).get("publish", ""))
                except Exception:
                    want_publish = ""
            if want_publish == "1":
                if not self.dry_run:
                    data["publish"] = "refused: dry_run 已关闭，自检接口不允许真发"
                else:
                    data["publish"] = await self.publish_once(force=True)
            want_reply = ""
            if _request is not None:
                try:
                    want_reply = str((_request.args or {}).get("reply", ""))
                except Exception:
                    want_reply = ""
            if want_reply == "1":
                data["reply"] = await self.reply_once()
            want_image = ""
            if _request is not None:
                try:
                    want_image = str((_request.args or {}).get("image", ""))
                except Exception:
                    want_image = ""
            if want_image == "1":
                try:
                    paths = await self._make_post_image(
                        "今天在整理巡逻记录，窗外的风有点凉。"
                    )
                    data["image_bytes"] = [
                        len(item) for item in self._read_images(paths)
                    ]
                    data["draw_available"] = self._draw_plugin() is not None
                except Exception as exc:
                    data["image_error"] = str(exc)
            want_interact = ""
            if _request is not None:
                try:
                    want_interact = str((_request.args or {}).get("interact", ""))
                except Exception:
                    want_interact = ""
            if want_interact == "1":
                try:
                    data["interact"] = await self.interact_once()
                except Exception as exc:
                    data["interact_error"] = str(exc)
            want_context = ""
            if _request is not None:
                try:
                    want_context = str((_request.args or {}).get("context", ""))
                except Exception:
                    want_context = ""
            if want_context == "1":
                data["time_context"] = self._time_context()
                data["weather_context"] = await self._weather_context()
                data["stock_images"] = self._stock_image_count()
                data["stock_dir"] = self.stock_image_dir
            return await self._json({"ok": True, "data": data})
        except Exception as exc:
            logger.exception("[qzone_ext] 自检失败")
            return await self._json({"ok": False, "error": {"message": str(exc)}})

    async def _selftest(self) -> dict[str, Any]:
        result: dict[str, Any] = {
            "enabled": self.enabled,
            "dry_run": self.dry_run,
            "ready": self._ready(),
        }
        if not result["ready"]:
            result["error"] = "bot_not_ready"
            return result
        try:
            ctx = await self._client.context()
            result["uin"] = ctx["uin"]
        except Exception as exc:
            result["error"] = f"cookie: {exc}"
            return result
        try:
            posts = await self._client.own_feeds(3)
            result["feeds"] = len(posts)
            result["comments"] = sum(len(comments_of(p)) for p in posts)
            result["latest_post"] = (
                str(posts[0].get("content") or "")[:80] if posts else ""
            )
        except Exception as exc:
            result["feeds_error"] = str(exc)

        now = datetime.now()
        weekday = ["周一", "周二", "周三", "周四", "周五", "周六", "周日"][now.weekday()]
        sample = await self._generate(
            self.post_prompt,
            f"现在是 {now.strftime('%Y-%m-%d %H:%M')}（{weekday}）。写一条现在发出去的说说。",
        )
        result["sample_post"] = sample
        return result

    # ------------------------------------------------------------------ 生命周期

    async def initialize(self) -> None:
        self._start_tasks()

    async def terminate(self) -> None:
        self._stop_tasks()
        await self._client.close()
        self._save_state()

    def _stop_tasks(self) -> None:
        for task in self._tasks:
            task.cancel()
        self._tasks.clear()

    def _start_tasks(self) -> None:
        if not self.enabled:
            logger.info("[qzone_ext] 未启用（enabled=false），不启动定时任务")
            return
        if self.publish_enabled:
            self._tasks.append(asyncio.create_task(self._publish_loop()))
        if self.reply_enabled:
            self._tasks.append(asyncio.create_task(self._reply_loop()))
        if self.interact_enabled:
            self._tasks.append(asyncio.create_task(self._interact_loop()))
        logger.info(
            "[qzone_ext] 已启动：发说说=%s 自动回评=%s 主动互动=%s dry_run=%s",
            self.publish_enabled,
            self.reply_enabled,
            self.interact_enabled,
            self.dry_run,
        )

    async def _restart_tasks(self) -> None:
        self._stop_tasks()
        await asyncio.sleep(0)
        self._start_tasks()

    # ------------------------------------------------------------------ 平台

    def _get_bot(self):
        try:
            manager = getattr(self.context, "platform_manager", None)
            for platform in getattr(manager, "platform_insts", []) or []:
                meta = platform.meta() if hasattr(platform, "meta") else None
                name = str(getattr(meta, "name", "") or "").lower()
                if "aiocqhttp" in name or "cqhttp" in name:
                    bot = getattr(platform, "bot", None)
                    if bot is not None:
                        return bot
        except Exception:
            logger.debug("[qzone_ext] 查找平台失败", exc_info=True)
        return None

    def _ready(self) -> bool:
        return self._get_bot() is not None

    # ------------------------------------------------------------------ 模型

    async def _provider(self):
        manager = getattr(self.context, "provider_manager", None)
        if manager is None:
            return None
        if self.provider_id:
            try:
                provider = await manager.get_provider_by_id(self.provider_id)
                if provider is not None:
                    return provider
            except Exception:
                logger.warning("[qzone_ext] 取不到 chat provider %s", self.provider_id)
        try:
            return await self.context.get_using_provider_async()
        except Exception:
            return None

    async def _resolve_persona(self) -> tuple[str, str]:
        """返回 (人格 prompt, 来源说明)：优先插件配置的人格，其次 AstrBot 默认人格。"""
        manager = getattr(self.context, "persona_manager", None)
        personas = getattr(manager, "personas_v3", []) or []
        if self.persona_id:
            for persona in personas:
                if isinstance(persona, dict) and str(persona.get("name")) == self.persona_id:
                    prompt = str(persona.get("prompt") or "").strip()
                    if prompt:
                        return prompt, f"配置人格「{self.persona_id}」"
                    break
            logger.warning(
                "[qzone_ext] 找不到人格「%s」，回退到 AstrBot 默认人格", self.persona_id
            )
        try:
            default = await manager.get_default_persona_v3()
        except Exception:
            default = None
        if isinstance(default, dict):
            prompt = str(default.get("prompt") or "").strip()
        else:
            prompt = str(getattr(default, "prompt", "") or "").strip()
        if prompt:
            return prompt, "AstrBot 默认人格"
        return "", ""

    async def _generate(
        self, instruction: str, user_prompt: str, *, use_persona: bool = True
    ) -> str:
        provider = await self._provider()
        if provider is None:
            logger.warning("[qzone_ext] 没有可用的聊天模型，本轮跳过")
            return ""
        persona, label = await self._resolve_persona() if use_persona else ("", "")
        if not persona:
            if use_persona and self.require_persona:
                logger.warning(
                    "[qzone_ext] 没有可用人格（配置=%r），按设置拒绝本次生成",
                    self.persona_id,
                )
                return ""
            system_prompt = instruction
        else:
            if not self._persona_logged:
                self._persona_logged = True
                logger.info("[qzone_ext] 已加载%s用于生成", label)
            system_prompt = f"{persona}\n\n{instruction}"
        try:
            response = await provider.text_chat(
                prompt=user_prompt,
                contexts=[],
                func_tool=None,
                system_prompt=system_prompt,
                session_id="qzone_ext",
            )
        except Exception as exc:
            logger.warning("[qzone_ext] 生成失败：%s", exc)
            return ""
        text = str(getattr(response, "completion_text", "") or "").strip()
        return " ".join(text.split())

    # ------------------------------------------------------------------ 素材

    async def _material_context(self) -> str:
        """按设置从群里拉一段聊天记录当灵感素材；失败/关闭时返回空串。"""
        if not self.material_enabled:
            return ""
        bot = self._get_bot()
        if bot is None:
            return ""
        try:
            groups = await bot.api.call_action("get_group_list")
        except Exception as exc:
            logger.warning("[qzone_ext] 取群列表失败，改用纯人格模式：%s", exc)
            return ""
        ids = [
            str(item.get("group_id"))
            for item in groups or []
            if isinstance(item, dict) and item.get("group_id")
        ]
        candidates = [
            gid
            for gid in ids
            if (not self.material_groups or gid in self.material_groups)
            and gid not in self.material_ignore_groups
        ]
        if not candidates:
            logger.info("[qzone_ext] 没有可用的素材群，改用纯人格模式")
            return ""
        group_id = random.choice(candidates)

        messages: list[dict] = []
        seq = 0
        while len(messages) < self.material_max:
            try:
                result = await bot.api.call_action(
                    "get_group_msg_history",
                    group_id=group_id,
                    message_seq=seq,
                    count=200,
                    reverseOrder=True,
                )
            except Exception as exc:
                logger.warning("[qzone_ext] 拉取群 %s 聊天记录失败：%s", group_id, exc)
                break
            batch = (result or {}).get("messages") or []
            if not batch:
                break
            messages.extend(batch)
            if len(batch) < 200:
                break
            try:
                seq = batch[0]["message_id"]
            except Exception:
                break

        lines: list[str] = []
        for msg in messages[: self.material_max]:
            segments = msg.get("message") or []
            text = "".join(
                str(seg.get("data", {}).get("text") or "")
                for seg in segments
                if isinstance(seg, dict) and seg.get("type") == "text"
            ).strip()
            if not text:
                continue
            nickname = str((msg.get("sender") or {}).get("nickname") or "")
            lines.append(f"{nickname}: {text}"[:200])
        if not lines:
            return ""
        logger.info("[qzone_ext] 已取素材：群 %s，%s 条消息", group_id, len(lines))
        return "\n".join(lines)

    # ------------------------------------------------------------------ 发说说

    def _next_slot(self) -> float:
        now = time.time()
        hour_start = now - (now % 3600) + 3600
        slot = hour_start + random.randint(0, self.offset_minutes) * 60
        if slot - now < 120:
            slot += 3600 + random.randint(0, self.offset_minutes) * 60
        return slot

    async def _publish_loop(self) -> None:
        try:
            while True:
                slot = self._next_slot()
                await asyncio.sleep(max(30.0, slot - time.time()))
                try:
                    await self.publish_once()
                except Exception:
                    logger.exception("[qzone_ext] 发说说异常，等待下一轮")
        except asyncio.CancelledError:
            return

    def _today(self) -> str:
        return datetime.now().strftime("%Y-%m-%d")

    def _in_hours(self) -> bool:
        raw = self.publish_hours
        if not raw:
            return True
        try:
            start_text, _, end_text = raw.partition("-")
            start = int(start_text)
            end = int(end_text) if end_text else start
        except Exception:
            return True
        hour = datetime.now().hour
        if start <= end:
            return start <= hour <= end
        return hour >= start or hour <= end

    def _today_count(self) -> int:
        if self._state.get("date") != self._today():
            self._state["date"] = self._today()
            self._state["count"] = 0
        return int(self._state.get("count", 0) or 0)

    def _bump_count(self) -> None:
        self._today_count()
        self._state["count"] = int(self._state.get("count", 0)) + 1
        self._state["last_publish"] = int(time.time())
        self._save_state()

    async def publish_once(
        self, force: bool = False, preview_image: bool = False
    ) -> dict[str, Any]:
        if not self._ready():
            logger.info("[qzone_ext] 机器人未就绪，跳过本轮发说说")
            return {"ok": False, "reason": "bot_not_ready"}
        if not force:
            if not self._in_hours():
                logger.debug("[qzone_ext] 当前 %s 点不在发布时段", datetime.now().hour)
                return {"ok": False, "reason": "off_hours"}
            if self._today_count() >= self.daily_limit:
                logger.info("[qzone_ext] 今日已达 %s 条上限", self.daily_limit)
                return {"ok": False, "reason": "daily_limit"}
            if random.random() < self.skip_probability:
                logger.info("[qzone_ext] 本轮随机跳过（%.0f%%）", self.skip_probability * 100)
                return {"ok": False, "reason": "skipped"}

        parts: list[str] = []
        if self.inject_time:
            parts.append(self._time_context() + "。")
        if self.inject_weather:
            weather = await self._weather_context()
            parts.append(
                (weather + "。")
                if weather
                else "（天气数据获取失败，不要编造天气）。"
            )
        parts.append("写一条现在发出去的说说。")
        user_prompt = "".join(parts)
        material = await self._material_context()
        if material:
            user_prompt = (
                user_prompt
                + "\n\n下面是群里最近的聊天片段，只作灵感参考：不要引用原话、不要点名、"
                "不要写成「群里有人说」；用第一人称写你自己的小事或感受，受素材启发即可。\n"
                + material
            )
        profile = self._card_profile()
        post_prompt = str(profile.get("post_prompt") or "").strip() or self.post_prompt
        if profile:
            logger.info(
                "[qzone_ext] 使用角色卡「%s」的说说配置",
                profile.get("card_name") or self.persona_id,
            )
        text = await self._generate(post_prompt, user_prompt)
        if not text:
            return {"ok": False, "reason": "empty"}
        text = text.strip("\"'“”")[:300]

        if self.dry_run:
            payload: dict[str, Any] = {
                "ok": True,
                "dry_run": True,
                "text": text,
                "image": None,
                "image_prompt": "",
                "image_source": "",
            }
            entry: dict[str, Any] = {
                "ts": int(time.time()),
                "mode": "dry",
                "text": text,
            }
            if preview_image and self.attach_image:
                scene = await self._image_scene(text)
                paths, source = await self._prepare_images(text, scene)
                if paths:
                    payload["image"] = self._image_preview(paths[0])
                    entry["image_path"] = paths[0]
                    logger.info("[qzone_ext][dry-run] 已生成配图预览：%s", paths[0])
                if source:
                    payload["image_source"] = source
                    entry["image_source"] = source
                if self._last_image_prompt:
                    payload["image_prompt"] = self._last_image_prompt
                    entry["image_prompt"] = self._last_image_prompt
            logger.info("[qzone_ext][dry-run] 将要发说说：%s", text)
            self._push_history(entry)
            return payload

        image_paths: list[str] = []
        image_source = ""
        if self.attach_image:
            scene = await self._image_scene(text)
            image_paths, image_source = await self._prepare_images(text, scene)
        images = self._read_images(image_paths)

        try:
            result = await self._client.publish(text, images=images or None)
        except QzoneError as exc:
            logger.warning("[qzone_ext] 发说说失败：%s", exc)
            self._push_history(
                {"ts": int(time.time()), "mode": "live", "text": text, "ok": False, "error": str(exc)}
            )
            return {"ok": False, "reason": str(exc)}
        if int(result.get("code", -1)) != 0:
            logger.warning("[qzone_ext] 发说说失败：%s", str(result)[:200])
            self._push_history(
                {"ts": int(time.time()), "mode": "live", "text": text, "ok": False, "error": "api_error"}
            )
            return {"ok": False, "reason": "api_error", "raw": str(result)[:300]}
        self._bump_count()
        skipped = bool(result.get("_image_skipped"))
        if skipped:
            logger.warning("[qzone_ext] 配图上传失败，已改发纯文字")
        logger.info("[qzone_ext] 已发说说：%s", text)
        self._push_history(
            {
                "ts": int(time.time()),
                "mode": "live",
                "text": text,
                "ok": True,
                "image_source": image_source if not skipped else "",
                "image_prompt": self._last_image_prompt if image_source == "generated" and not skipped else "",
                "note": "配图上传失败，已改发纯文字" if skipped else "",
            }
        )
        return {"ok": True, "text": text, "image_skipped": skipped}

    # ------------------------------------------------------------------ 自动回评

    async def _reply_loop(self) -> None:
        try:
            await asyncio.sleep(60)
            while True:
                try:
                    await self.reply_once()
                except Exception:
                    logger.exception("[qzone_ext] 自动回评异常，等待下一轮")
                await asyncio.sleep(self.reply_interval * 60)
        except asyncio.CancelledError:
            return

    async def reply_once(self) -> dict[str, Any]:
        if not self._ready():
            logger.debug("[qzone_ext] 机器人未就绪，跳过自动回评")
            return {"ok": False, "reason": "bot_not_ready"}
        try:
            posts = await self._client.own_feeds(5)
        except QzoneError as exc:
            logger.warning("[qzone_ext] 读取说说失败：%s", exc)
            return {"ok": False, "reason": str(exc)}

        replied_map: dict[str, int] = dict(self._state.get("replied") or {})
        sent = 0
        previewed = 0
        now = int(time.time())
        for post in posts:
            if sent + previewed >= self.reply_max:
                break
            for comment in comments_of(post):
                if sent + previewed >= self.reply_max:
                    break
                cid = comment_id(comment)
                if not cid or cid in replied_map or cid in self._dry_seen:
                    continue
                if comment.get("parent_tid"):
                    continue  # 只回主评论，避免楼中楼
                created = comment_time(comment)
                if created and now - created < self.reply_min_age:
                    continue
                body = comment_text(comment)
                if not body:
                    replied_map[cid] = now
                    continue
                nickname = str(comment.get("name") or comment.get("nickname") or "对方")
                post_text = str(post.get("content") or "").strip()[:200]
                user_prompt = (
                    f"我说说：{post_text}\n评论（{nickname}）：{body}\n写一句回复。"
                )
                reply = await self._generate(self.reply_prompt, user_prompt)
                if not reply:
                    continue
                reply = reply.strip("\"'“”")[: self.max_reply_chars]
                if self.dry_run:
                    # 预演不落状态：真发时这些评论仍会被回复；同一个进程内不重复预演
                    self._dry_seen.add(cid)
                    previewed += 1
                    logger.info("[qzone_ext][dry-run] 将要回复 %s：%s", cid, reply)
                    continue
                try:
                    result = await self._client.reply_comment(post, comment, reply)
                except QzoneError as exc:
                    logger.warning("[qzone_ext] 回复评论失败：%s", exc)
                    continue
                if int(result.get("code", -1)) != 0:
                    logger.warning("[qzone_ext] 回复评论失败：%s", str(result)[:200])
                    continue
                logger.info("[qzone_ext] 已回复评论 %s：%s", cid, reply)
                replied_map[cid] = now
                sent += 1

        if len(replied_map) > 500:
            keep = dict(sorted(replied_map.items(), key=lambda kv: kv[1])[-500:])
            replied_map = keep
        self._state["replied"] = replied_map
        self._save_state()
        if sent:
            logger.info("[qzone_ext] 本轮回复 %s 条评论", sent)
        if previewed:
            logger.info("[qzone_ext][dry-run] 本轮预演回复 %s 条（未写入状态）", previewed)
        return {"ok": True, "replied": sent, "previewed": previewed}

    # ------------------------------------------------------------------ 主动互动

    async def _interact_loop(self) -> None:
        try:
            await asyncio.sleep(90)
            while True:
                try:
                    await self.interact_once()
                except Exception:
                    logger.exception("[qzone_ext] 主动互动异常，等待下一轮")
                await asyncio.sleep(self.interact_interval * 60)
        except asyncio.CancelledError:
            return

    async def interact_once(self) -> dict[str, Any]:
        """挑好友最近一条说说写评论（dry-run 只预演），按好友逐个读，避免动态流里只有自己。"""
        if not self._ready():
            return {"ok": False, "reason": "bot_not_ready"}
        try:
            friends = await self._client.friend_list()
        except QzoneError as exc:
            logger.warning("[qzone_ext] 取好友列表失败：%s", exc)
            return {"ok": False, "reason": str(exc)}
        if not friends:
            logger.info("[qzone_ext] 好友列表为空")
            return {"ok": True, "commented": 0, "previewed": 0}

        interacted: dict[str, int] = dict(self._state.get("interacted") or {})
        sent = 0
        previewed = 0
        now = int(time.time())
        self_uin = self._client.cached_uin()

        candidates: list[tuple[str, str]] = []
        for friend in friends:
            uid = str(friend.get("user_id") or friend.get("uin") or "").strip()
            if not uid.isdigit():
                continue
            if self_uin and uid == str(self_uin):
                continue
            if uid in self.interact_ignore_users:
                continue
            nickname = str(friend.get("remark") or friend.get("nickname") or "")
            candidates.append((uid, nickname))
        random.shuffle(candidates)
        logger.info(
            "[qzone_ext] 主动互动：好友 %s 位，本轮最多 %s 条",
            len(candidates),
            self.interact_max,
        )

        for uid, nickname in candidates[: max(6, self.interact_max * 4)]:
            if sent + previewed >= self.interact_max:
                break
            try:
                posts = await self._client.friend_feeds(uid, 3)
            except QzoneError as exc:
                logger.info("[qzone_ext] 读取好友 %s 的说说失败：%s", uid, exc)
                continue
            logger.debug("[qzone_ext] 好友 %s 返回 %s 条说说", uid, len(posts))
            for post in posts:
                if sent + previewed >= self.interact_max:
                    break
                tid = str(post.get("tid") or "")
                text = str(post.get("content") or "").strip()
                logger.info(
                    "[qzone_ext] 候选 %s_%s 文本 %s 字：%s",
                    uid,
                    tid[:10],
                    len(text),
                    text[:40],
                )
                if not tid:
                    continue
                key = f"{uid}_{tid}"
                if key in interacted or key in self._interact_seen:
                    continue
                if len(text) < self.interact_min_chars:
                    continue
                author = str(post.get("name") or nickname or "好友")
                comment = await self._generate(
                    self.comment_prompt,
                    f"好友「{author}」的说说：{text[:300]}\n写一句评论。",
                )
                if not comment:
                    continue
                comment = comment.strip("\"'“”")[: self.max_reply_chars]
                if self.dry_run:
                    self._interact_seen.add(key)
                    previewed += 1
                    logger.info("[qzone_ext][dry-run] 将要评论 %s：%s", key, comment)
                    continue
                host_uin = int(post.get("uin") or uid)
                try:
                    result = await self._client.comment_post(host_uin, tid, comment)
                except QzoneError as exc:
                    logger.warning("[qzone_ext] 评论失败：%s", exc)
                    continue
                if int(result.get("code", -1)) != 0:
                    logger.warning("[qzone_ext] 评论失败：%s", str(result)[:200])
                    continue
                if self.interact_like:
                    try:
                        await self._client.like_post(host_uin, tid)
                    except Exception as exc:
                        logger.debug("[qzone_ext] 点赞失败：%s", exc)
                logger.info("[qzone_ext] 已评论 %s：%s", key, comment)
                interacted[key] = now
                sent += 1

        if len(interacted) > 500:
            interacted = dict(sorted(interacted.items(), key=lambda kv: kv[1])[-500:])
        self._state["interacted"] = interacted
        self._save_state()
        if sent:
            logger.info("[qzone_ext] 本轮评论 %s 条好友说说", sent)
        if previewed:
            logger.info("[qzone_ext][dry-run] 本轮预演评论 %s 条（未写入状态）", previewed)
        return {"ok": True, "commented": sent, "previewed": previewed}

    # ------------------------------------------------------------------ 状态

    def _load_state(self) -> None:
        try:
            if self._state_path.is_file():
                data = json.loads(self._state_path.read_text(encoding="utf-8-sig"))
                if isinstance(data, dict):
                    self._state = data
        except Exception:
            self._state = {}

    def _save_state(self) -> None:
        try:
            self._state_path.parent.mkdir(parents=True, exist_ok=True)
            tmp = self._state_path.with_suffix(".json.tmp")
            tmp.write_text(
                json.dumps(self._state, ensure_ascii=False, indent=2), encoding="utf-8"
            )
            os.replace(tmp, self._state_path)
        except Exception:
            logger.debug("[qzone_ext] 状态落盘失败", exc_info=True)

    # ------------------------------------------------------------------ 指令

    @filter.permission_type(filter.PermissionType.ADMIN)
    @filter.command("qz_post")
    async def cmd_post(self, event):
        result = await self.publish_once(force=True)
        if result.get("ok") and result.get("dry_run"):
            yield event.plain_result(f"[dry-run] 将发：{result.get('text')}")
        elif result.get("ok"):
            yield event.plain_result(f"已发说说：{result.get('text')}")
        else:
            yield event.plain_result(f"没发出去：{result.get('reason')}")

    @filter.permission_type(filter.PermissionType.ADMIN)
    @filter.command("qz_status")
    async def cmd_status(self, event):
        lines = [
            f"启用：{self.enabled}｜dry-run：{self.dry_run}",
            f"今日已发：{self._today_count()}/{self.daily_limit}",
            f"自动回评：{self.reply_enabled}（每 {self.reply_interval} 分钟，单轮最多 {self.reply_max} 条）",
            f"平台就绪：{self._ready()}",
            f"已回评论记录：{len(self._state.get('replied') or {})} 条",
        ]
        yield event.plain_result("\n".join(lines))
