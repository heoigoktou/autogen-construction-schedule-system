"""Lightweight Agent communication router.

This is intentionally simpler than a full distributed runtime. It gives the
team deterministic broadcast/direct/arbitration behavior while keeping every
message traceable in Excel.
"""

from __future__ import annotations

from typing import Protocol

from communication.event_topics import subscribers_for
from communication.message_logger import MessageLogger
from communication.message_schema import AgentMessage, Priority, make_message


class RoutableAgent(Protocol):
    """Minimal protocol required by the router."""

    name: str

    def receive(self, message: AgentMessage) -> dict[str, object]:
        """Receive a message and return a structured response."""


class CommunicationRouter:
    """Route messages between registered Agents and log every interaction."""

    def __init__(self, logger: MessageLogger) -> None:
        self.logger = logger
        self._agents: dict[str, RoutableAgent] = {}

    def register(self, agent: RoutableAgent) -> None:
        """Register one Agent by name."""

        if agent.name in self._agents:
            raise ValueError(f"Agent already registered: {agent.name}")
        self._agents[agent.name] = agent

    def registered_agents(self) -> list[str]:
        """Return all registered Agent names."""

        return sorted(self._agents)

    def send_direct(self, message: AgentMessage) -> dict[str, object]:
        """Send one direct request and return the receiver response."""

        message.mode = "direct"
        message.status = "sent"
        self.logger.log(message)
        agent = self._require_agent(message.receiver)
        response = agent.receive(message)

        response_message = make_message(
            sender=message.receiver,
            receiver=message.sender,
            mode="direct",
            event_type=message.event_type,
            summary=str(response.get("summary", "direct response completed")),
            priority=message.priority,
            related_sheet=message.related_sheet,
            related_id=message.related_id,
            payload_extra={"response_to": message.message_id, "response": response},
        )
        response_message.status = "responded"
        self.logger.log(response_message)
        return response

    def broadcast(
        self,
        *,
        sender: str,
        event_type: str,
        summary: str,
        priority: Priority = "medium",
        related_sheet: str | None = None,
        related_id: str | None = None,
        receivers: list[str] | None = None,
    ) -> list[dict[str, object]]:
        """Broadcast an event to subscribers and log both aggregate and deliveries."""

        target_agents = receivers or subscribers_for(event_type)
        aggregate = make_message(
            sender=sender,
            receiver="ALL",
            mode="broadcast",
            event_type=event_type,
            summary=summary,
            priority=priority,
            related_sheet=related_sheet,
            related_id=related_id,
            payload_extra={"receivers": target_agents},
        )
        aggregate.status = "sent"
        self.logger.log(aggregate)

        responses = []
        for receiver in target_agents:
            if receiver not in self._agents:
                responses.append(
                    {
                        "agent": receiver,
                        "status": "skipped",
                        "summary": "agent not registered",
                    }
                )
                continue
            delivery = make_message(
                sender=sender,
                receiver=receiver,
                mode="broadcast",
                event_type=event_type,
                summary=summary,
                priority=priority,
                related_sheet=related_sheet,
                related_id=related_id,
                payload_extra={"broadcast_id": aggregate.message_id},
            )
            delivery.status = "sent"
            self.logger.log(delivery)
            responses.append(self._agents[receiver].receive(delivery))
        return responses

    def request_arbitration(
        self,
        *,
        sender: str,
        summary: str,
        priority: Priority = "high",
        related_sheet: str | None = None,
        related_id: str | None = None,
    ) -> dict[str, object]:
        """Request coordinator-led arbitration."""

        message = make_message(
            sender=sender,
            receiver="coordinator_agent",
            mode="arbitration",
            event_type="arbitration.completed",
            summary=summary,
            priority=priority,
            related_sheet=related_sheet,
            related_id=related_id,
            payload_extra={"required_response": "arbitration_decision"},
        )
        message.status = "sent"
        self.logger.log(message)
        response = self._require_agent("coordinator_agent").receive(message)
        result = make_message(
            sender="coordinator_agent",
            receiver="ALL",
            mode="arbitration",
            event_type="arbitration.completed",
            summary=str(response.get("summary", "arbitration completed")),
            priority=priority,
            related_sheet=related_sheet,
            related_id=related_id,
            payload_extra={"response": response},
        )
        result.status = "completed"
        self.logger.log(result)
        return response

    def log_only(
        self,
        *,
        sender: str,
        event_type: str,
        summary: str,
        priority: Priority = "low",
        related_sheet: str | None = None,
        related_id: str | None = None,
    ) -> AgentMessage:
        """Record a status change without triggering an Agent response."""

        message = make_message(
            sender=sender,
            receiver="SYSTEM",
            mode="log_only",
            event_type=event_type,
            summary=summary,
            priority=priority,
            related_sheet=related_sheet,
            related_id=related_id,
        )
        message.status = "completed"
        self.logger.log(message)
        return message

    def _require_agent(self, name: str) -> RoutableAgent:
        try:
            return self._agents[name]
        except KeyError as exc:
            raise KeyError(f"Agent is not registered: {name}") from exc
