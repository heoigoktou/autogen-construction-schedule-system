"""Exceptions raised by the AgentChat production workflow."""

from __future__ import annotations


class AgentChatRuntimeError(RuntimeError):
    """Raised when the real AutoGen AgentChat runtime cannot continue."""


class ModelConfigurationError(AgentChatRuntimeError):
    """Raised when live model settings are missing or invalid."""


class AgentOutputValidationError(AgentChatRuntimeError):
    """Raised when model-produced blackboard rows violate the contract."""
