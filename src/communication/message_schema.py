"""Unified message schema for Agent communication."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any, Literal
from uuid import uuid4

MessageMode = Literal["broadcast", "direct", "handoff", "arbitration", "log_only"]
Priority = Literal["low", "medium", "high"]
MessageStatus = Literal["pending", "sent", "responded", "completed", "failed"]

CHINA_TZ = timezone(timedelta(hours=8))


def now_iso() -> str:
    """Return current time in China timezone as ISO text."""

    return datetime.now(CHINA_TZ).isoformat(timespec="seconds")


def new_message_id() -> str:
    """Generate a traceable message id."""

    stamp = datetime.now(CHINA_TZ).strftime("%Y%m%d")
    suffix = uuid4().hex[:8].upper()
    return f"MSG-{stamp}-{suffix}"


@dataclass(slots=True)
class AgentMessage:
    """Canonical JSON-like message exchanged between Agents."""

    sender: str
    receiver: str
    mode: MessageMode
    event_type: str
    payload: dict[str, Any]
    priority: Priority = "medium"
    status: MessageStatus = "pending"
    related_sheet: str | None = None
    related_id: str | None = None
    message_id: str = field(default_factory=new_message_id)
    timestamp: str = field(default_factory=now_iso)

    def validate(self) -> None:
        """Validate the message fields before route/log operations."""

        required = {
            "message_id": self.message_id,
            "sender": self.sender,
            "receiver": self.receiver,
            "mode": self.mode,
            "event_type": self.event_type,
            "status": self.status,
        }
        missing = [name for name, value in required.items() if not str(value).strip()]
        if missing:
            raise ValueError(f"Message is missing required fields: {', '.join(missing)}")

        if "summary" not in self.payload:
            raise ValueError("Message payload must include a 'summary' field")

    def to_dict(self) -> dict[str, Any]:
        """Return a JSON-serializable message dictionary."""

        self.validate()
        return {
            "message_id": self.message_id,
            "timestamp": self.timestamp,
            "sender": self.sender,
            "receiver": self.receiver,
            "mode": self.mode,
            "event_type": self.event_type,
            "priority": self.priority,
            "related_sheet": self.related_sheet,
            "related_id": self.related_id,
            "payload": self.payload,
            "status": self.status,
        }

    def to_log_row(self) -> dict[str, Any]:
        """Convert the message into the agent_message_log sheet format."""

        self.validate()
        return {
            "message_id": self.message_id,
            "sender": self.sender,
            "receiver": self.receiver,
            "mode": self.mode,
            "event_type": self.event_type,
            "priority": self.priority,
            "payload_summary": str(self.payload.get("summary", "")),
            "status": self.status,
            "timestamp": self.timestamp,
            "related_sheet": self.related_sheet or "",
            "related_id": self.related_id or "",
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> AgentMessage:
        """Build a message from a dictionary."""

        return cls(
            message_id=data.get("message_id") or new_message_id(),
            timestamp=data.get("timestamp") or now_iso(),
            sender=data["sender"],
            receiver=data["receiver"],
            mode=data["mode"],
            event_type=data["event_type"],
            priority=data.get("priority", "medium"),
            related_sheet=data.get("related_sheet"),
            related_id=data.get("related_id"),
            payload=data.get("payload") or {"summary": ""},
            status=data.get("status", "pending"),
        )


def make_message(
    *,
    sender: str,
    receiver: str,
    mode: MessageMode,
    event_type: str,
    summary: str,
    priority: Priority = "medium",
    related_sheet: str | None = None,
    related_id: str | None = None,
    payload_extra: dict[str, Any] | None = None,
) -> AgentMessage:
    """Convenience factory for common messages."""

    payload = {"summary": summary}
    if payload_extra:
        payload.update(payload_extra)
    return AgentMessage(
        sender=sender,
        receiver=receiver,
        mode=mode,
        event_type=event_type,
        payload=payload,
        priority=priority,
        related_sheet=related_sheet,
        related_id=related_id,
    )
