"""Validation helpers for Excel public blackboard rows."""

from __future__ import annotations

from collections.abc import Mapping

from blackboard.sheet_schema import SheetSpec, get_sheet_spec


class BlackboardValidationError(ValueError):
    """Raised when a row or sheet violates the public blackboard contract."""


def validate_headers(sheet_name: str, headers: list[str]) -> None:
    """Validate that a worksheet contains the required headers."""

    spec = get_sheet_spec(sheet_name)
    missing = [field for field in spec.required_headers if field not in headers]
    if missing:
        raise BlackboardValidationError(
            f"Sheet {sheet_name!r} is missing required headers: {', '.join(missing)}"
        )


def validate_row(sheet_name: str, row: Mapping[str, object]) -> None:
    """Validate one row before appending it to a worksheet."""

    spec: SheetSpec = get_sheet_spec(sheet_name)
    missing = [
        field
        for field in spec.required_headers
        if field not in row or row[field] is None or str(row[field]).strip() == ""
    ]
    if missing:
        raise BlackboardValidationError(
            f"Row for sheet {sheet_name!r} is missing required values: {', '.join(missing)}"
        )

    extra = [field for field in row if field not in spec.headers]
    if extra:
        raise BlackboardValidationError(
            f"Row for sheet {sheet_name!r} has unknown fields: {', '.join(extra)}"
        )
