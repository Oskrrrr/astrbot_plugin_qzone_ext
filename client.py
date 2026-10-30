# SPDX-License-Identifier: GPL-3.0-or-later
# QQ Zone protocol layer derived from astrbot_plugin_qzone by Zhalslar (GPLv3).
# See NOTICE and README.md for provenance and attribution.
"""QQ 空间最小客户端：cookie 取自 NapCat，只做发说说 / 读自己说说 / 回评论。"""

from __future__ import annotations

import asyncio
import base64
import re
import time
from http.cookies import SimpleCookie
from time import monotonic
from typing import Any

import aiohttp

from astrbot.api import logger

try:  # AstrBot 环境里有 json5；没有就退回标准 json
    import json5  # type: ignore
except Exception:  # pragma: no cover
    import json as json5  # type: ignore

DOMAIN = "user.qzone.qq.com"
BASE = "https://user.qzone.qq.com"
PUBLISH_URL = (
    f"{BASE}/proxy/domain/taotao.qzone.qq.com/cgi-bin/emotion_cgi_publish_v6"
)
LIST_URL = f"{BASE}/proxy/domain/taotao.qq.com/cgi-bin/emotion_cgi_msglist_v6"
REPLY_URL = (
    "https://h5.qzone.qq.com/proxy/domain/taotao.qzone.qq.com/cgi-bin/emotion_cgi_re_feeds"
)
UPLOAD_IMAGE_URL = "https://up.qzone.qq.com/cgi-bin/upload/cgi_upload_image"
ZONE_LIST_URL = f"{BASE}/proxy/domain/ic2.qzone.qq.com/cgi-bin/feeds/feeds3_html_more"
COMMENT_URL = f"{BASE}/proxy/domain/taotao.qzone.qq.com/cgi-bin/emotion_cgi_re_feeds"
LIKE_URL = f"{BASE}/proxy/domain/w.qzone.qq.com/cgi-bin/likes/internal_dolike_app"
UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/138.0.0.0 Safari/537.36"
)


class QzoneError(RuntimeError):
    pass


def _parse_body(text: str) -> dict[str, Any]:
    """解析 JSON / JSONP / 带 callback 的脏 JSON。"""
    if not text or not text.strip():
        raise QzoneError("QQ空间返回空响应")
    match = re.search(r"callback\s*\(\s*([^{]*(\{.*\})[^)]*)\s*\)", text, re.I | re.S)
    json_str = match.group(2) if match else text[text.find("{") : text.rfind("}") + 1]
    json_str = json_str.replace("undefined", "null").strip()
    try:
        data = json5.loads(json_str)
    except Exception as exc:
        raise QzoneError(f"QQ空间返回无法解析: {exc}") from exc
    if not isinstance(data, dict):
        raise QzoneError("QQ空间返回格式异常")
    return data


def _gtk(p_skey: str) -> str:
    value = 5381
    for ch in p_skey:
        value += (value << 5) + ord(ch)
    return str(value & 0x7FFFFFFF)


class QzoneClient:
    """一个够用的 QQ 空间客户端。

    bot 通过 callback 懒取：平台还没连上时返回 None，调用方应把它当成"本轮跳过"。
    """

    def __init__(self, bot_getter, *, timeout: int = 30, cookie_ttl: int = 3600) -> None:
        self._bot_getter = bot_getter
        self._timeout = max(5, int(timeout))
        self._ttl = max(0, int(cookie_ttl))
        self._ctx: dict[str, Any] | None = None
        self._at = 0.0
        self._lock = asyncio.Lock()
        self._session: aiohttp.ClientSession | None = None

    async def close(self) -> None:
        if self._session is not None:
            await self._session.close()
            self._session = None

    def _http(self) -> aiohttp.ClientSession:
        if self._session is None or self._session.closed:
            self._session = aiohttp.ClientSession(
                timeout=aiohttp.ClientTimeout(total=self._timeout)
            )
        return self._session

    # ------------------------------------------------------------------ 上下文

    async def context(self) -> dict[str, Any]:
        async with self._lock:
            if self._ctx is None or self._expired():
                self._ctx = await self._refresh_locked()
            return self._ctx

    def cached_uin(self) -> int:
        """已缓存的 uin，没有就返回 0（不触发网络请求）。"""
        try:
            return int((self._ctx or {}).get("uin") or 0)
        except Exception:
            return 0

    def _expired(self) -> bool:
        if self._ttl <= 0:
            return False
        return monotonic() - self._at >= self._ttl

    async def _refresh_locked(self) -> dict[str, Any]:
        bot = self._bot_getter()
        if bot is None:
            raise QzoneError("机器人尚未就绪")
        payload = await bot.get_cookies(domain=DOMAIN)
        cookies_str = str((payload or {}).get("cookies") or "").strip()
        if not cookies_str:
            raise QzoneError("NapCat 没有返回 QQ 空间 Cookie")
        jar = {k: v.value for k, v in SimpleCookie(cookies_str).items()}
        uin_text = jar.get("uin", "0")
        uin_raw = uin_text[1:] if uin_text[:1].lower() == "o" else uin_text
        if not uin_raw.isdigit():
            raise QzoneError("Cookie 里没有合法的 uin")
        skey = jar.get("skey", "")
        p_skey = jar.get("p_skey", "") or skey
        self._at = monotonic()
        return {"uin": int(uin_raw), "skey": skey, "p_skey": p_skey, "gtk": _gtk(p_skey)}

    def _headers(self, uin: int) -> dict[str, str]:
        return {
            "User-Agent": UA,
            "referer": f"{BASE}/{uin}",
            "origin": BASE,
            "Connection": "keep-alive",
        }

    # ------------------------------------------------------------------ 请求

    async def _request(
        self,
        method: str,
        url: str,
        *,
        params: dict[str, Any] | None = None,
        data: dict[str, Any] | None = None,
        retry: int = 0,
    ) -> dict[str, Any]:
        ctx = await self.context()
        uin = ctx["uin"]
        try:
            async with self._http().request(
                method,
                url,
                params=params,
                data=data,
                headers=self._headers(uin),
                cookies={
                    "uin": f"o{uin}",
                    "skey": ctx["skey"],
                    "p_skey": ctx["p_skey"],
                },
            ) as resp:
                text = await resp.text()
                status = resp.status
        except aiohttp.ClientError as exc:
            raise QzoneError(f"请求 QQ 空间失败: {exc}") from exc

        if status in (401, 403) and retry < 1:
            logger.warning("[qzone_ext] QQ 空间返回 %s，重新取 Cookie 后重试", status)
            async with self._lock:
                self._ctx = None
            return await self._request(method, url, params=params, data=data, retry=1)
        if status >= 500:
            raise QzoneError(f"QQ 空间服务异常 HTTP {status}")
        return _parse_body(text)

    # ------------------------------------------------------------------ 业务

    async def _upload_image(self, image: bytes) -> tuple[str, str]:
        """上传一张图，返回 (pic_bo, richval)。"""
        ctx = await self.context()
        form = {
            "filename": "filename",
            "uploadtype": "1",
            "albumtype": "7",
            "skey": ctx["skey"],
            "uin": ctx["uin"],
            "p_skey": ctx["p_skey"],
            "output_type": "json",
            "base64": "1",
            "picfile": base64.b64encode(image).decode(),
        }
        raw = await self._request("POST", UPLOAD_IMAGE_URL, data=form)
        ret = raw.get("ret", raw.get("code"))
        if ret not in (0, "0", None):
            raise QzoneError(f"上传图片失败: {str(raw)[:200]}")
        payload = raw.get("data")
        if not isinstance(payload, dict):
            raise QzoneError(f"上传图片返回异常: {str(raw)[:200]}")
        url = str(payload.get("url") or "")
        if "&bo=" not in url:
            raise QzoneError("上传图片没有返回 pic_bo")
        pic_bo = url.split("&bo=", 1)[1]
        richval = ",{},{},{},{},{},{},,{},{}".format(
            payload.get("albumid", ""),
            payload.get("lloc", ""),
            payload.get("sloc", ""),
            payload.get("type", ""),
            payload.get("height", ""),
            payload.get("width", ""),
            payload.get("height", ""),
            payload.get("width", ""),
        )
        return pic_bo, richval

    async def publish(self, text: str, images: list[bytes] | None = None) -> dict[str, Any]:
        ctx = await self.context()
        data: dict[str, Any] = {
            "syn_tweet_verson": "1",
            "paramstr": "1",
            "who": "1",
            "con": text,
            "feedversion": "1",
            "ver": "1",
            "ugc_right": "1",
            "to_sign": "0",
            "hostuin": ctx["uin"],
            "code_version": "1",
            "format": "json",
            "qzreferrer": f"{BASE}/{ctx['uin']}",
        }
        image_skipped = False
        if images:
            pic_bos: list[str] = []
            richvals: list[str] = []
            try:
                for image in images:
                    pic_bo, richval = await self._upload_image(image)
                    pic_bos.append(pic_bo)
                    richvals.append(richval)
            except QzoneError as exc:
                # 上传失败不该拖垮整条说说：退回纯文字再发一次
                logger.warning("[qzone] 配图上传失败，改发纯文字：%s", exc)
                pic_bos, richvals = [], []
                image_skipped = True
            if pic_bos:
                data.update(
                    pic_bo=",".join(pic_bos),
                    richtype="1",
                    richval="\t".join(richvals),
                )
        result = await self._request(
            "POST", PUBLISH_URL, params={"g_tk": ctx["gtk"], "uin": ctx["uin"]}, data=data
        )
        if image_skipped and isinstance(result, dict):
            result["_image_skipped"] = True
        return result

    async def own_feeds(self, num: int = 5) -> list[dict[str, Any]]:
        ctx = await self.context()
        raw = await self._request(
            "GET",
            LIST_URL,
            params={
                "g_tk": ctx["gtk"],
                "uin": ctx["uin"],
                "ftype": 0,
                "sort": 0,
                "pos": 0,
                "num": max(1, num),
                "replynum": 100,
                "callback": "_preloadCallback",
                "code_version": 1,
                "format": "json",
                "need_comment": 1,
                "need_private_comment": 1,
            },
        )
        msglist = raw.get("msglist")
        if not isinstance(msglist, list):
            data = raw.get("data")
            msglist = data.get("msglist") if isinstance(data, dict) else []
        return [item for item in msglist or [] if isinstance(item, dict)]

    async def recent_feeds(self) -> list[dict[str, Any]]:
        """好友动态流（只取说说类 appid=311）。"""
        ctx = await self.context()
        raw = await self._request(
            "GET",
            ZONE_LIST_URL,
            params={
                "uin": ctx["uin"],
                "scope": 0,
                "view": 1,
                "filter": "all",
                "flag": 1,
                "applist": "all",
                "pagenum": 1,
                "aisortEndTime": 0,
                "aisortOffset": 0,
                "aisortBeginTime": 0,
                "begintime": 0,
                "format": "json",
                "g_tk": ctx["gtk"],
                "useutf8": 1,
                "outputhtmlfeed": 1,
            },
        )
        data = raw.get("data")
        feeds = data.get("data") if isinstance(data, dict) else None
        if not isinstance(feeds, list):
            logger.warning(
                "[qzone_ext] 动态流结构异常：keys=%s data_type=%s",
                list(raw.keys())[:8],
                type(data).__name__,
            )
            return []
        counts: dict[str, int] = {}
        for feed in feeds:
            if isinstance(feed, dict):
                key = str(feed.get("appid"))
                counts[key] = counts.get(key, 0) + 1
        logger.info(
            "[qzone_ext] 动态流 %s 条，appid 分布 %s",
            len(feeds),
            sorted(counts.items(), key=lambda kv: -kv[1])[:5],
        )
        return [
            feed
            for feed in feeds
            if isinstance(feed, dict) and str(feed.get("appid", "")) == "311"
        ]

    async def friend_list(self) -> list[dict[str, Any]]:
        """从 NapCat 取好友列表。"""
        bot = self._bot_getter()
        if bot is None:
            raise QzoneError("机器人尚未就绪")
        try:
            data = await bot.api.call_action("get_friend_list")
        except Exception as exc:
            raise QzoneError(f"取好友列表失败: {exc}") from exc
        return [item for item in data or [] if isinstance(item, dict)]

    async def friend_feeds(self, target_id: str, num: int = 3) -> list[dict[str, Any]]:
        """指定好友最近的说说列表。"""
        ctx = await self.context()
        raw = await self._request(
            "GET",
            LIST_URL,
            params={
                "g_tk": ctx["gtk"],
                "uin": target_id,
                "ftype": 0,
                "sort": 0,
                "pos": 0,
                "num": max(1, num),
                "replynum": 100,
                "callback": "_preloadCallback",
                "code_version": 1,
                "format": "json",
                "need_comment": 1,
                "need_private_comment": 1,
            },
        )
        msglist = raw.get("msglist")
        if not isinstance(msglist, list):
            data = raw.get("data")
            msglist = data.get("msglist") if isinstance(data, dict) else []
        return [item for item in msglist or [] if isinstance(item, dict)]

    async def comment_post(self, host_uin: int, tid: str, content: str) -> dict[str, Any]:
        ctx = await self.context()
        return await self._request(
            "POST",
            COMMENT_URL,
            params={"g_tk": ctx["gtk"]},
            data={
                "topicId": f"{host_uin}_{tid}__1",
                "uin": ctx["uin"],
                "hostUin": host_uin,
                "feedsType": 100,
                "inCharset": "utf-8",
                "outCharset": "utf-8",
                "plat": "qzone",
                "source": "ic",
                "platformid": 52,
                "format": "fs",
                "ref": "feeds",
                "content": content,
            },
        )

    async def like_post(self, host_uin: int, tid: str) -> dict[str, Any]:
        ctx = await self.context()
        key = f"{BASE}/{host_uin}/mood/{tid}"
        return await self._request(
            "POST",
            LIKE_URL,
            params={"g_tk": ctx["gtk"]},
            data={
                "qzreferrer": f"{BASE}/{ctx['uin']}",
                "opuin": ctx["uin"],
                "unikey": key,
                "curkey": key,
                "appid": 311,
                "from": 1,
                "typeid": 0,
                "abstime": int(time.time()),
                "fid": tid,
                "active": 0,
                "format": "json",
                "fupdate": 1,
            },
        )

    async def reply_comment(
        self, post: dict[str, Any], comment: dict[str, Any], content: str
    ) -> dict[str, Any]:
        ctx = await self.context()
        host_uin = post.get("uin") or ctx["uin"]
        data = {
            "topicId": f"{host_uin}_{post.get('tid')}__1",
            "uin": ctx["uin"],
            "hostUin": host_uin,
            "feedsType": 100,
            "inCharset": "utf-8",
            "outCharset": "utf-8",
            "plat": "qzone",
            "source": "ic",
            "platformid": 52,
            "format": "fs",
            "ref": "feeds",
            "content": content,
            "commentId": comment.get("tid") or comment.get("commentid"),
        }
        return await self._request("POST", REPLY_URL, params={"g_tk": ctx["gtk"]}, data=data)


def comments_of(post: dict[str, Any]) -> list[dict[str, Any]]:
    items = post.get("commentlist")
    if not isinstance(items, list):
        return []
    return [item for item in items if isinstance(item, dict)]


def comment_text(comment: dict[str, Any]) -> str:
    return str(comment.get("content") or "").strip()


def comment_uin(comment: dict[str, Any]) -> int:
    try:
        return int(comment.get("uin") or 0)
    except Exception:
        return 0


def comment_id(comment: dict[str, Any]) -> str:
    return str(comment.get("tid") or comment.get("commentid") or "")


def comment_time(comment: dict[str, Any]) -> int:
    for key in ("create_time", "createTime", "time"):
        value = comment.get(key)
        if value is not None:
            try:
                return int(value)
            except Exception:
                continue
    return 0
