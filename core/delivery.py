import asyncio
import base64
import hashlib
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from urllib.parse import quote

import aiohttp

from .config import PiggyError, Settings
from .database import Database
from .diagnostics import safe_detail
from .storage import ImagePublisher, response_json

IMAGE_ERRORS = {304010, 40034004}
# Retry forced-verification rejections without unnecessarily reuploading images.
RETRYABLE_ERRORS = IMAGE_ERRORS | {40034141}
EXPIRED_ERRORS = {304103, 40034005, 40034128}
DEDUPE_ERRORS = {40054005}
TEXT_LIMIT = 1500
# QQ accepts at most five passive replies to one user message.
MAX_PARTS = 4


class QQError(PiggyError):
    def __init__(self, code: int, status: int, uncertain: bool = False, *, reason="", trace_id=""):
        self.code = code
        self.status = status
        self.uncertain = uncertain
        self.reason = reason if isinstance(reason, str) else ""
        self.trace_id = trace_id if isinstance(trace_id, str) else ""
        if uncertain:
            message = "QQ 发送结果暂时无法确认，请先查看群消息；抽取记录已保存。"
        elif code in IMAGE_ERRORS:
            message = "QQ 图片转存未成功；抽取记录已保存，请稍后重试。"
        elif code in EXPIRED_ERRORS:
            message = "本条消息的回复时限已过，请重新发送指令；不会重复计数。"
        else:
            message = (
                f"QQ 拒绝本次消息（错误码 {code}，HTTP {status}），请检查平台权限、格式或频率限制。"
            )
        super().__init__(message)

    @property
    def retryable(self) -> bool:
        return self.uncertain or self.status == 429 or self.code in RETRYABLE_ERRORS


class QQTransport:
    """Use AstrBot's token lifecycle, but retain QQ error codes discarded by botpy 1.2.1."""

    def __init__(self, timeout: float):
        self.timeout = timeout
        self.session = None

    async def request(self, event, payload: dict) -> dict:
        return await self._request(event, payload, "messages", "id")

    async def upload_image(self, event, data: bytes) -> str:
        if not 0 < len(data) <= 10 * 1024 * 1024:
            raise PiggyError("渲染图片为空或超过 10 MB，请减少单页内容。")
        return await self._upload(event, data, 1)

    async def upload_video(self, event, data: bytes) -> str:
        if not 0 < len(data) <= 10 * 1024 * 1024:
            raise PiggyError("回放视频为空或超过 10 MB。")
        return await self._upload(event, data, 2)

    async def _upload(self, event, data: bytes, file_type: int) -> str:
        result = await self._request(
            event,
            {
                "file_type": file_type,
                "file_data": base64.b64encode(data).decode("ascii"),
                "srv_send_msg": False,
            },
            "files",
            "file_info",
        )
        return result["file_info"]

    async def _request(self, event, payload: dict, resource: str, expected: str) -> dict:
        if self.session is None:
            self.session = aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=self.timeout))
        http = event.bot.api._http
        await http.check_session()
        group = event.get_group_id()
        if not group:
            raise PiggyError("此版本面向 QQ 官方群聊，请在群内使用。")
        domain = (
            "https://sandbox.api.sgroup.qq.com" if http.is_sandbox else "https://api.bot.qq.com"
        )
        url = f"{domain}/v2/groups/{quote(str(group), safe='')}/{resource}"
        headers = {
            k: v for k, v in http._headers.items() if k in {"Authorization", "X-Union-Appid"}
        }
        try:
            async with self.session.post(
                url, json=payload, headers=headers, allow_redirects=False
            ) as response:
                try:
                    data = await response_json(response)
                except (ValueError, UnicodeDecodeError):
                    data = None
                if not isinstance(data, dict):
                    raise QQError(
                        0,
                        response.status,
                        uncertain=response.status >= 500 or response.status < 300,
                    )
                try:
                    code = int(data.get("err_code", data.get("code", 0)))
                except (TypeError, ValueError):
                    raise QQError(0, response.status, uncertain=True) from None
                if code or response.status not in {200, 201, 202}:
                    raise QQError(
                        code,
                        response.status,
                        uncertain=response.status >= 500 and code not in IMAGE_ERRORS,
                        reason=data.get("message", ""),
                        trace_id=data.get("trace_id", ""),
                    )
                if not isinstance(data.get(expected), str) or not data[expected]:
                    raise QQError(0, response.status, uncertain=True)
                return data
        except (aiohttp.ClientError, TimeoutError):
            raise QQError(0, 0, uncertain=True) from None

    async def close(self):
        if self.session is not None:
            await self.session.close()


@dataclass(frozen=True)
class Message:
    text: str
    images: tuple[Path | bytes, ...] = ()
    keyboard: dict | None = None
    local: bool = False
    markdown: bool = False
    # Builds MP4 bytes (or None) in a worker thread; sent best-effort before the main reply.
    video: Callable[[], bytes | None] | None = field(default=None, compare=False, repr=False)
    # Plain text sent first of all, so the group knows a slow reply is on its way.
    notice: str = ""

    def parts(self) -> list[str]:
        """Split long text by lines; QQ allows a few passive replies per message."""
        parts, current = [], ""
        for line in self.text.split("\n"):
            while len(line) > TEXT_LIMIT:
                if current:
                    parts.append(current)
                    current = ""
                parts.append(line[:TEXT_LIMIT])
                line = line[TEXT_LIMIT:]
            if current and len(current) + 1 + len(line) > TEXT_LIMIT:
                parts.append(current)
                current = line
            else:
                current = f"{current}\n{line}" if current else line
        parts.append(current)
        return parts[:MAX_PARTS]

    def content(self, urls: list[str]) -> str:
        result = self.text
        for index, url in enumerate(urls):
            result = result.replace(f"{{{{image:{index}}}}}", url)
        return result


def video_failure(exc: Exception, elapsed: float, timeout: float) -> str:
    """Why one stage of the replay video failed, in words an admin can act on."""
    if isinstance(exc, QQError) and not exc.code and not exc.status:
        return f"{elapsed:.1f} 秒后仍没有收到 QQ 答复（超时或断线；当前请求超时 {timeout:g} 秒）"
    if isinstance(exc, QQError):
        detail = f"QQ 返回 HTTP {exc.status}、错误码 {exc.code}"
        return f"{detail}：{exc.reason}" if exc.reason else detail
    if isinstance(exc, TimeoutError):
        return f"{elapsed:.1f} 秒内没有完成"
    return f"{type(exc).__name__}：{exc}"


def message_key(event, app_id: str) -> str:
    raw = f"{app_id}:{event.get_group_id()}:{event.message_obj.message_id}"
    return hashlib.sha256(raw.encode()).hexdigest()


class Sender:
    def __init__(
        self, settings: Settings, db: Database, publisher: ImagePublisher, transport, *, logger=None
    ):
        self.settings, self.db, self.publisher, self.transport = (
            settings,
            db,
            publisher,
            transport,
        )
        self.logger = logger
        # Video encoding is CPU heavy; render one replay at a time.
        self.video_lock = asyncio.Lock()

    def _retry_delay(
        self,
        exc: QQError,
        attempt: int,
        retry_count: int,
        deadline: float,
        stage: str,
        retryable: bool,
    ) -> float | None:
        delay = self.settings.delay(attempt)
        if not retryable:
            stop = "not_retryable"
        elif attempt >= retry_count:
            stop = "exhausted"
        elif time.monotonic() + delay >= deadline:
            stop = "deadline"
        else:
            stop = ""
        if self.logger is not None:
            detail = safe_detail(f"reason={exc.reason} trace_id={exc.trace_id}", self.settings)
            self.logger.warning(
                "[piggy] QQ failure stage=%s attempt=%s/%s code=%s http=%s "
                "retryable=%s stop=%s retry_delay=%s | %s",
                stage,
                attempt + 1,
                retry_count + 1,
                exc.code,
                exc.status,
                retryable,
                stop or "none",
                0 if stop else delay,
                detail,
            )
        return None if stop else delay

    async def announce(self, target, message: Message, deadline: float | None = None):
        """Proactive group message: no msg_id, so the group must allow bot posts."""
        deadline = deadline if deadline is not None else time.monotonic() + 120
        payload = {}
        if message.local:
            media = await self._upload_local(target, message.images[0], deadline)
            payload.update(msg_type=7, media={"file_info": media})
        elif message.images:
            urls = [await self.publisher.publish(p, deadline=deadline) for p in message.images]
            payload.update(msg_type=2, markdown={"content": message.content(urls)})
            if message.keyboard:
                payload["keyboard"] = message.keyboard
        else:
            payload.update(msg_type=0, content=message.text)
        await self.transport.request(target, payload)

    async def send(
        self,
        event,
        app_id: str,
        message: Message,
        deadline: float | None = None,
        *,
        force_upload: bool = False,
    ):
        key = message_key(event, app_id)
        receipt = await self.db.delivery(key)
        if receipt["done"]:
            return
        sequence = receipt["sequence"]
        deadline = deadline if deadline is not None else time.monotonic() + 240
        sequence = await self._preface(event, key, message, sequence, deadline)
        urls = []
        media = None
        if message.local:
            if len(message.images) != 1 or not isinstance(message.images[0], bytes):
                raise PiggyError("普通图片消息必须包含一张完整的渲染卡片。")
            media = await self._upload_local(event, message.images[0], deadline)
        else:
            for path in message.images:
                urls.append(
                    await self.publisher.publish(path, force=force_upload, deadline=deadline)
                )
        refreshed = False
        parts = [message.text] if message.images or message.local else message.parts()
        for index, part in enumerate(parts):
            final = index == len(parts) - 1
            for attempt in range(self.settings.image_retry_count + 1):
                if time.monotonic() >= deadline:
                    raise PiggyError("本次回复等待过久，请重新发送指令；抽取记录已保留。")
                payload = {
                    "msg_id": event.message_obj.message_id,
                    "msg_seq": sequence,
                }
                if message.local:
                    payload.update(msg_type=7, media={"file_info": media})
                elif message.images or message.markdown:
                    payload.update(
                        msg_type=2,
                        markdown={
                            "content": message.content(urls) if message.images else part,
                            **({"force_verify_image_resource": True} if message.images else {}),
                        },
                    )
                    if message.keyboard and final:
                        payload["keyboard"] = message.keyboard
                else:
                    payload.update(msg_type=0, content=part)
                try:
                    await self.transport.request(event, payload)
                except QQError as exc:
                    if exc.code in DEDUPE_ERRORS:
                        # Same persisted sequence: a previous attempt already reached QQ.
                        break
                    image_error = exc.code in IMAGE_ERRORS or (message.local and exc.code == 304080)
                    retryable = image_error or exc.retryable
                    delay = self._retry_delay(
                        exc, attempt, self.settings.image_retry_count, deadline, "send", retryable
                    )
                    if not retryable:
                        raise
                    if not exc.uncertain:
                        # QQ explicitly rejected this message. It is safe to allocate the next
                        # sequence.
                        sequence += 1
                        await self.db.delivery_update(key, sequence, False)
                    if delay is None:
                        raise
                    await asyncio.sleep(delay)
                    if image_error and message.local:
                        media = await self._upload_local(event, message.images[0], deadline)
                    elif image_error and not refreshed:
                        # Repair expired/deleted host objects once per message, then let QQ
                        # retry transfer.
                        urls = [
                            await self.publisher.publish(path, force=True, deadline=deadline)
                            for path in message.images
                        ]
                        refreshed = True
                    continue
                break
            if final:
                await self.db.delivery_update(key, sequence, True)
                return
            sequence += 1
            await self.db.delivery_update(key, sequence, False)

    async def _preface(self, event, key: str, message: Message, sequence: int, deadline: float):
        """Send the notice, then the replay video, ahead of the main reply; neither can block it."""
        if message.notice:
            try:
                await self.transport.request(
                    event,
                    {
                        "msg_id": event.message_obj.message_id,
                        "msg_seq": sequence,
                        "msg_type": 0,
                        "content": message.notice,
                    },
                )
            except QQError as exc:
                if self.logger is not None and exc.code not in DEDUPE_ERRORS:
                    self.logger.warning("[piggy] Notice not sent: %s", exc)
            sequence += 1
            await self.db.delivery_update(key, sequence, False)
        if message.video is not None:
            await self._send_video(event, message.video, sequence, deadline)
            sequence += 1
            await self.db.delivery_update(key, sequence, False)
        return sequence

    async def _render_video(self, build, deadline: float) -> bytes:
        async with self.video_lock:
            return await asyncio.wait_for(
                asyncio.to_thread(build), max(1.0, deadline - time.monotonic())
            )

    async def _post_video(self, event, media: str, sequence: int):
        await self.transport.request(
            event,
            {
                "msg_id": event.message_obj.message_id,
                "msg_seq": sequence,
                "msg_type": 7,
                "media": {"file_info": media},
            },
        )

    async def _send_video(self, event, build, sequence: int, deadline: float):
        """Render, upload and send the replay; failures only reach the log."""
        stage, started = "render", time.monotonic()
        try:
            data = await self._render_video(build, deadline)
            if not data:
                return
            stage, started = "upload", time.monotonic()
            media = await self.transport.upload_video(event, data)
            stage, started = "send", time.monotonic()
            await self._post_video(event, media, sequence)
        except Exception as exc:
            if self.logger is not None:
                self.logger.warning(
                    "[piggy] Raid replay video skipped at %s after %.1fs (%s): %s",
                    stage,
                    time.monotonic() - started,
                    type(exc).__name__,
                    safe_detail(str(exc), self.settings),
                )

    async def check_video(self, event, build, sequence: int, deadline: float) -> list[str]:
        """Run the replay pipeline once and describe how each stage went."""
        timeout = self.settings.request_timeout
        started = time.monotonic()
        try:
            data = await self._render_video(build, deadline)
        except Exception as exc:
            return [f"渲染：失败，{video_failure(exc, time.monotonic() - started, timeout)}"]
        if not data:
            return ["渲染：失败，没有生成任何画面"]
        lines = [
            f"渲染：成功，{time.monotonic() - started:.1f} 秒，视频 {len(data) / 2**20:.1f} MB"
        ]
        started = time.monotonic()
        try:
            media = await self.transport.upload_video(event, data)
        except Exception as exc:
            return lines + [
                f"上传：失败，{video_failure(exc, time.monotonic() - started, timeout)}"
            ]
        lines.append(f"上传：成功，{time.monotonic() - started:.1f} 秒")
        started = time.monotonic()
        try:
            await self._post_video(event, media, sequence)
        except Exception as exc:
            return lines + [
                f"发送：失败，{video_failure(exc, time.monotonic() - started, timeout)}"
            ]
        lines.append(f"发送：成功，{time.monotonic() - started:.1f} 秒")
        return lines

    async def _upload_local(self, event, data: bytes, deadline: float) -> str:
        for attempt in range(self.settings.upload_retry_count + 1):
            if time.monotonic() >= deadline:
                raise PiggyError("QQ 本地图片上传超过回复时限，请重新发送指令。")
            try:
                return await self.transport.upload_image(event, data)
            except QQError as exc:
                delay = self._retry_delay(
                    exc,
                    attempt,
                    self.settings.upload_retry_count,
                    deadline,
                    "upload",
                    exc.retryable,
                )
                if delay is None:
                    raise PiggyError(
                        f"QQ 本地图片上传失败（错误码 {exc.code}，HTTP {exc.status}，"
                        f"尝试 {attempt + 1}/{self.settings.upload_retry_count + 1} 次）。"
                    ) from None
                await asyncio.sleep(delay)
        raise AssertionError("Unreachable local upload state")
