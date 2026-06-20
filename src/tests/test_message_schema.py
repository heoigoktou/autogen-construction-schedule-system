from communication.message_schema import AgentMessage, make_message


def test_message_to_log_row() -> None:
    message = make_message(
        sender="coordinator_agent",
        receiver="data_parser_agent",
        mode="direct",
        event_type="parameter.check.requested",
        summary="请检查参数清单。",
    )

    row = message.to_log_row()

    assert row["message_id"].startswith("MSG-")
    assert row["sender"] == "coordinator_agent"
    assert row["receiver"] == "data_parser_agent"
    assert row["payload_summary"] == "请检查参数清单。"


def test_message_requires_summary() -> None:
    message = AgentMessage(
        sender="a",
        receiver="b",
        mode="direct",
        event_type="demo",
        payload={},
    )

    try:
        message.validate()
    except ValueError as exc:
        assert "summary" in str(exc)
    else:
        raise AssertionError("Expected ValueError")
