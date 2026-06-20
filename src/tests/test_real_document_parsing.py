from pathlib import Path

from agents.data_parser_agent import DataParserAgent
from blackboard.excel_store import ExcelBlackboardStore
from tools.document_tools import read_source_documents
from tools.parameter_extraction import extract_parameter_checklist_by_rules


def test_rule_extraction_builds_parameter_checklist_rows() -> None:
    text = """
    工程名称：星河湾住宅项目
    总建筑面积 68000 平方米，地上 26 层，地下 2 层。
    计划工期 540 天，开工日期 2026年03月01日，竣工日期 2027年08月22日。
    基坑土方开挖工程量 86000 m3，塔吊 4 台。
    混凝土养护技术间歇不少于 7 天。
    """

    rows = extract_parameter_checklist_by_rules(text, source_names="case.txt")
    by_name = {row["name"]: row for row in rows}

    assert by_name["总工期"]["status"] == "source_exact"
    assert by_name["总工期"]["note"] == "540日历天"
    assert by_name["开工日期"]["note"] == "2026-03-01"
    assert by_name["竣工日期"]["note"] == "2027-08-22"
    assert by_name["塔吊型号与数量"]["note"] == "4台"
    assert by_name["主体结构工期"]["status"] == "missing"


def test_data_parser_agent_reads_documents_and_writes_blackboard(tmp_path: Path) -> None:
    input_dir = tmp_path / "input_docs"
    input_dir.mkdir()
    (input_dir / "case.md").write_text(
        "\n".join(
            [
                "项目名称：星河湾住宅项目",
                "总工期 540 天。",
                "计划开工 2026-03-01，计划竣工 2027-08-22。",
                "土方开挖 86000 m3，塔吊 4 台。",
            ]
        ),
        encoding="utf-8",
    )

    store = ExcelBlackboardStore(tmp_path / "blackboard.xlsx")
    store.initialize()
    documents = read_source_documents(input_dir)
    result = DataParserAgent(store=store).run_from_documents(
        documents=documents,
        model_settings={"provider": "mock"},
        use_model=False,
    )

    assert result["status"] == "success"
    checklist = store.read_rows("parameter_checklist")
    project_parameters = store.read_rows("project_parameters")
    assert any(row["name"] == "总工期" and row["status"] == "source_exact" for row in checklist)
    assert any(row["parameter_id"] == "P-002" for row in project_parameters)
    messages = store.read_rows("agent_message_log")
    assert any(row["event_type"] == "source.documents.parsed" for row in messages)
