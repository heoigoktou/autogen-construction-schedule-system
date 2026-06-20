"""Base Agent abstraction for schedule planning demos."""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any

from blackboard.excel_store import ExcelBlackboardStore
from communication.message_schema import (
    AgentMessage,
    MessageMode,
    MessageStatus,
    Priority,
    make_message,
)


@dataclass
class AgentResult:
    """Recommended structured Agent return shape."""

    agent: str
    status: str
    summary: str
    written_sheets: list[str] = field(default_factory=list)
    messages: list[str] = field(default_factory=list)
    needs_human_confirmation: bool = False
    warnings: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        """Return a serializable result."""

        return {
            "agent": self.agent,
            "status": self.status,
            "summary": self.summary,
            "written_sheets": self.written_sheets,
            "messages": self.messages,
            "needs_human_confirmation": self.needs_human_confirmation,
            "warnings": self.warnings,
        }


class BaseAgent:
    """Small wrapper shared by all schedule planning Agents."""

    def __init__(
        self,
        *,
        name: str,
        display_name: str,
        role: str,
        store: ExcelBlackboardStore,
    ) -> None:
        self.name = name
        self.display_name = display_name
        self.role = role
        self.store = store
        self.logger = logging.getLogger(name)

    def receive(self, message: AgentMessage) -> dict[str, Any]:
        """Receive a routed message and return a structured response."""

        self.logger.info("%s received %s from %s", self.name, message.event_type, message.sender)
        return self.handle_message(message).to_dict()

    def handle_message(self, message: AgentMessage) -> AgentResult:
        """Default message acknowledgement for legacy router smoke checks."""

        return AgentResult(
            agent=self.name,
            status="success",
            summary=(
                f"{self.display_name}已接收{message.mode}消息："
                f"{message.payload.get('summary', '')}"
            ),
            messages=[message.message_id],
        )

    def result(
        self,
        *,
        status: str,
        summary: str,
        written_sheets: list[str] | None = None,
        messages: list[str] | None = None,
        needs_human_confirmation: bool = False,
        warnings: list[str] | None = None,
    ) -> AgentResult:
        """Build a standard result with this Agent name."""

        return AgentResult(
            agent=self.name,
            status=status,
            summary=summary,
            written_sheets=written_sheets or [],
            messages=messages or [],
            needs_human_confirmation=needs_human_confirmation,
            warnings=warnings or [],
        )

    def log_message(
        self,
        *,
        receiver: str,
        mode: MessageMode,
        event_type: str,
        summary: str,
        priority: Priority = "medium",
        status: MessageStatus = "sent",
        related_sheet: str | None = None,
        related_id: str | None = None,
        payload_extra: dict[str, Any] | None = None,
    ) -> str:
        """Log an Agent message through the shared message schema."""

        message = make_message(
            sender=self.name,
            receiver=receiver,
            mode=mode,
            event_type=event_type,
            summary=summary,
            priority=priority,
            related_sheet=related_sheet,
            related_id=related_id,
            payload_extra=payload_extra,
        )
        message.status = status
        self.store.append_row("agent_message_log", message.to_log_row())
        return message.message_id
