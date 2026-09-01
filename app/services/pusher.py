"""
推送渠道派发服务。
将业务事件（信号、告警、订单等）派发到已启用的渠道。
支持飞书、钉钉、企业微信、Telegram、自定义 Webhook。
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import logging
import time
from typing import Any, Dict, List, Optional

import httpx

logger = logging.getLogger("portal.pusher")


class PushError(Exception):
    """推送失败。"""


async def _post_json(url: str, payload: dict, headers: Optional[dict] = None, timeout: float = 5.0) -> None:
    async with httpx.AsyncClient(timeout=timeout) as client:
        resp = await client.post(url, json=payload, headers=headers or {})
        if resp.status_code >= 400:
            raise PushError(f"HTTP {resp.status_code}: {resp.text[:200]}")
        # 飞书 / 钉钉自定义机器人返回 200，但业务错误码在 body 中
        try:
            body = resp.json()
        except Exception:  # noqa: BLE001
            return
        if isinstance(body, dict):
            # 飞书：code != 0 表示失败
            if "code" in body and body.get("code") not in (0, None):
                raise PushError(f"飞书拒绝：{body.get('msg') or body}")
            # 钉钉：errcode != 0 表示失败
            if "errcode" in body and body.get("errcode") not in (0, None):
                raise PushError(f"钉钉拒绝：{body.get('errmsg') or body}")


def _feishu_sign(timestamp: str, secret: str) -> str:
    """飞书自定义机器人签名：HMAC-SHA256(key=secret, msg=f'{timestamp}\\n{secret}') 后 base64。"""
    string_to_sign = f"{timestamp}\n{secret}"
    digest = hmac.new(string_to_sign.encode("utf-8"), digestmod=hashlib.sha256).digest()
    return base64.b64encode(digest).decode("utf-8")


def _feishu_payload(title: str, content: str, level: str, secret: Optional[str] = None) -> dict:
    """飞书富文本卡片消息；配置签名校验密钥时附加 timestamp + sign。"""
    color = {"info": "blue", "success": "green", "warn": "orange", "error": "red"}.get(level, "blue")
    payload: Dict[str, Any] = {
        "msg_type": "interactive",
        "card": {
            "config": {"wide_screen_mode": True},
            "header": {"title": {"tag": "plain_text", "content": title}, "template": color},
            "elements": [{"tag": "div", "text": {"tag": "lark_md", "content": content}}],
        },
    }
    if secret:
        ts = str(int(time.time()))
        payload["timestamp"] = ts
        payload["sign"] = _feishu_sign(ts, secret)
    return payload


def _dingtalk_payload(title: str, content: str) -> dict:
    return {
        "msgtype": "markdown",
        "markdown": {"title": title, "text": f"### {title}\n\n{content}"},
    }


def _wecom_payload(title: str, content: str) -> dict:
    return {
        "msgtype": "markdown",
        "markdown": {"content": f"### {title}\n{content}"},
    }


def _telegram_payload(chat_id: str, text: str) -> dict:
    return {"chat_id": chat_id, "text": text, "parse_mode": "Markdown"}


async def dispatch(
    channel_type: str,
    config: Dict[str, Any],
    *,
    title: str,
    content: str,
    level: str = "info",
) -> None:
    """
    向单个渠道发送消息。
    调用方应捕获 PushError 并记录失败，不应因推送失败影响主业务。
    """
    try:
        if channel_type == "feishu":
            url = config["webhook_url"]
            # 兼容前端字段 signing_key 与历史字段 secret
            secret = config.get("signing_key") or config.get("secret")
            await _post_json(url, _feishu_payload(title, content, level, secret=secret))
        elif channel_type == "dingtalk":
            url = config["webhook_url"]
            # 钉钉加签逻辑可在此补充：timestamp + sign
            await _post_json(url, _dingtalk_payload(title, content))
        elif channel_type == "wecom":
            url = config["webhook_url"]
            await _post_json(url, _wecom_payload(title, content))
        elif channel_type == "telegram":
            bot_token = config["bot_token"]
            chat_id = config["chat_id"]
            url = f"https://api.telegram.org/bot{bot_token}/sendMessage"
            await _post_json(url, _telegram_payload(chat_id, f"*{title}*\n{content}"))
        elif channel_type == "custom_webhook":
            url = config["webhook_url"]
            headers = config.get("headers") or {}
            await _post_json(
                url,
                {"title": title, "content": content, "level": level},
                headers=headers,
            )
        else:
            raise PushError(f"未知渠道类型：{channel_type}")
        logger.info("push sent via %s: %s", channel_type, title)
    except Exception as exc:  # noqa: BLE001
        logger.warning("push failed via %s: %s", channel_type, exc)
        raise PushError(str(exc)) from exc


async def broadcast(
    channels: List[dict],
    *,
    title: str,
    content: str,
    level: str = "info",
) -> Dict[str, str]:
    """
    向多个启用渠道广播消息。
    返回 {channel: error} 字典；成功的渠道值为 "ok"。
    """
    results: Dict[str, str] = {}
    for ch in channels:
        if not ch.get("enabled"):
            continue
        try:
            await dispatch(
                ch["channel_type"],
                ch.get("config") or {},
                title=title,
                content=content,
                level=level,
            )
            results[ch["channel"]] = "ok"
        except PushError as exc:
            results[ch["channel"]] = str(exc)
    return results
