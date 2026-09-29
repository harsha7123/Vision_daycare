"""Alert routing (blueprint section 8): console, Telegram (with Acknowledge button), webhook, FCM.

Critical -> admin + parents immediately. High/Medium -> admin; escalated to the centre owner
if nobody acknowledges within `escalation.unacknowledged_after_s`.
"""
from __future__ import annotations

import hashlib
import hmac
import json
import logging
import os
import queue
import threading
import time
from pathlib import Path

import httpx

log = logging.getLogger("daycare.alerts")

PUBLIC_KEYS = ["event_id", "rule", "title", "priority", "camera", "room", "track_id", "role", "started_at",
               "duration_s", "children_in_zone", "adults_in_zone", "confidence", "snapshot_url", "clip_url", "ack"]
EMOJI = {"critical": "\U0001F6A8", "high": "⚠️", "medium": "\U0001F7E1", "low": "ℹ️"}


def public_payload(ev: dict) -> dict:
    return {k: ev.get(k) for k in PUBLIC_KEYS}


def caption(ev: dict, kind: str = "event") -> str:
    head = "ESCALATION - not acknowledged\n" if kind == "escalation" else ""
    return (f"{head}{EMOJI.get(ev['priority'], '')} {ev['priority'].upper()} - {ev['title']}\n"
            f"{ev['room']} ({ev['camera']}) - {ev['duration_s']} s\n"
            f"Children in zone: {ev['children_in_zone']} - Adults: {ev['adults_in_zone']} - "
            f"confidence {ev['confidence']}\n{ev['created_at']}")


def _enabled(cfg: dict, *env_names: str) -> bool:
    flag = cfg.get("enabled", "auto")
    if flag == "auto":
        return all(os.environ.get(e) for e in env_names if e)
    return bool(flag)


class Channel:
    name = "channel"

    def send(self, ev: dict, audiences: list[str], kind: str, snapshot: Path | None) -> None:
        raise NotImplementedError


class ConsoleChannel(Channel):
    name = "console"

    def send(self, ev, audiences, kind, snapshot):
        log.warning("[%s -> %s] %s %s %s track=%s dur=%ss", kind.upper(), ",".join(audiences), ev["priority"].upper(),
                    ev["rule"], ev["camera"], ev.get("track_id"), ev["duration_s"])


class TelegramChannel(Channel):
    name = "telegram"

    def __init__(self, cfg: dict, store):
        self.token = os.environ[cfg.get("bot_token_env", "TELEGRAM_BOT_TOKEN")]
        self.chats = {aud: os.environ.get(env) for aud, env in (cfg.get("chats") or {}).items()}
        self.api = f"https://api.telegram.org/bot{self.token}"
        self.store = store
        self._offset = 0
        threading.Thread(target=self._poll, daemon=True, name="telegram-poll").start()

    def send(self, ev, audiences, kind, snapshot):
        markup = json.dumps({"inline_keyboard": [[{"text": "✅ Acknowledge", "callback_data": f"ack:{ev['event_id']}"}]]})
        for aud in audiences:
            chat = self.chats.get(aud)
            if not chat:
                continue
            data = {"chat_id": chat, "caption": caption(ev, kind)}
            if aud != "parents":
                data["reply_markup"] = markup       # parents are informed; staff acknowledge
            if snapshot and snapshot.exists():
                with open(snapshot, "rb") as f:
                    r = httpx.post(f"{self.api}/sendPhoto", data=data, files={"photo": f}, timeout=20)
            else:
                data["text"] = data.pop("caption")
                r = httpx.post(f"{self.api}/sendMessage", data=data, timeout=20)
            r.raise_for_status()

    def _poll(self):
        """Long-poll callback queries so the inline Acknowledge button works without a public URL."""
        while True:
            try:
                r = httpx.get(f"{self.api}/getUpdates", params={
                    "timeout": 25, "offset": self._offset, "allowed_updates": json.dumps(["callback_query"])},
                    timeout=35)
                for u in r.json().get("result", []):
                    self._offset = u["update_id"] + 1
                    cq = u.get("callback_query")
                    if not cq or not str(cq.get("data", "")).startswith("ack:"):
                        continue
                    who = cq.get("from", {})
                    by = "telegram:" + (who.get("username") or str(who.get("id")))
                    ok = self.store.ack(cq["data"][4:], by=by) is not None
                    httpx.post(f"{self.api}/answerCallbackQuery", data={
                        "callback_query_id": cq["id"], "text": "Acknowledged" if ok else "Event not found"}, timeout=10)
            except Exception as e:                  # network hiccups must not kill the thread
                log.debug("telegram poll: %s", e)
                time.sleep(5)


class WebhookChannel(Channel):
    name = "webhook"

    def __init__(self, cfg: dict):
        self.url = os.environ[cfg.get("url_env", "WEBHOOK_URL")]
        self.secret = os.environ.get(cfg.get("secret_env", "WEBHOOK_SECRET"), "").encode()

    def send(self, ev, audiences, kind, snapshot):
        body = json.dumps({"kind": kind, "audiences": audiences, "event": public_payload(ev)}).encode()
        headers = {"Content-Type": "application/json"}
        if self.secret:
            headers["X-Daycare-Signature"] = "sha256=" + hmac.new(self.secret, body, hashlib.sha256).hexdigest()
        for attempt in range(4):
            try:
                httpx.post(self.url, content=body, headers=headers, timeout=10).raise_for_status()
                return
            except httpx.HTTPError:
                if attempt == 3:
                    raise
                time.sleep(2 ** attempt)


class FcmChannel(Channel):
    name = "fcm"

    def __init__(self, cfg: dict):
        import firebase_admin
        from firebase_admin import credentials, messaging
        if not firebase_admin._apps:
            firebase_admin.initialize_app(credentials.Certificate(os.environ[cfg.get("credentials_env")]))
        self.messaging = messaging
        self.prefix = cfg.get("topic_prefix", "centre")

    def send(self, ev, audiences, kind, snapshot):
        if "parents" not in audiences:
            return
        topic = f"{self.prefix}_{ev['room']}".lower().replace(" ", "_")
        m = self.messaging
        m.send(m.Message(topic=topic, notification=m.Notification(title=ev["title"], body=caption(ev, kind)),
                         data={"event_id": ev["event_id"], "rule": ev["rule"], "priority": ev["priority"]}))


class Dispatcher:
    def __init__(self, alerts_cfg: dict, rules_cfg: dict, store):
        self.store = store
        self.routing = alerts_cfg.get("routing", {})
        esc = rules_cfg.get("escalation", {})
        self.escalate_after = float(esc.get("unacknowledged_after_s", 120))
        self.escalate_to = list(esc.get("escalate_to", ["centre_owner"]))
        self.channels: list[Channel] = []
        ch = alerts_cfg.get("channels", {})
        builders = [
            ("console", lambda c: ConsoleChannel(), ()),
            ("telegram", lambda c: TelegramChannel(c, store), ("bot_token_env",)),
            ("webhook", lambda c: WebhookChannel(c), ("url_env",)),
            ("fcm", lambda c: FcmChannel(c), ("credentials_env",)),
        ]
        for name, build, env_keys in builders:
            c = ch.get(name)
            if c is None or not _enabled(c, *(c.get(k) for k in env_keys)):
                continue
            try:
                self.channels.append(build(c))
            except Exception as e:
                log.error("alert channel %s disabled: %s", name, e)
        self.sent: list[dict] = []          # last deliveries, shown on the dashboard
        self._q: queue.Queue = queue.Queue()
        threading.Thread(target=self._worker, daemon=True, name="alert-worker").start()
        threading.Thread(target=self._escalator, daemon=True, name="alert-escalation").start()

    @property
    def channel_names(self) -> list[str]:
        return [c.name for c in self.channels]

    def dispatch(self, ev: dict, kind: str = "event") -> None:
        audiences = self.escalate_to if kind == "escalation" else self.routing.get(ev["priority"], ["admin"])
        self._q.put((ev, list(audiences), kind))

    def _snapshot_path(self, ev: dict) -> Path | None:
        rel = ev.get("snapshot")
        return self.store.media / rel.removeprefix("/media/") if rel else None

    def _worker(self):
        while True:
            ev, audiences, kind = self._q.get()
            for c in self.channels:
                status = "sent"
                try:
                    c.send(ev, audiences, kind, self._snapshot_path(ev))
                except Exception as e:
                    status = f"failed: {e}"
                    log.error("alert via %s failed: %s", c.name, e)
                self.sent.append({"event_id": ev["event_id"], "channel": c.name, "audiences": audiences,
                                  "kind": kind, "status": status, "at": time.time()})
            del self.sent[:-50]

    def _escalator(self):
        while True:
            time.sleep(5)
            try:
                for ev in self.store.due_for_escalation(self.escalate_after):
                    self.store.mark_escalated(ev["event_id"])
                    self.dispatch(ev, "escalation")
            except Exception as e:
                log.error("escalation check failed: %s", e)
