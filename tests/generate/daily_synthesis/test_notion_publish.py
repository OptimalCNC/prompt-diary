"""Tests for the Notion publisher logic against a fake client.

The publisher pushes a rendered ``report.notion.json`` payload into a Notion database as a new row.
These tests pin the schema-driven property mapping (title by type, date columns ← report date, other
types left alone), the metadata banner prepended to the body, the always-create-new behaviour (one
create, never an edit/delete), and the request shaping that keeps every append within Notion's
≤100-children / single-nesting-level limits while still uploading an arbitrarily deep tree. The real
SDK is exercised only behind the thin adapter; here a fake client records calls and returns ids.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

import pytest

from prompt_diary.errors import PromptDiaryError
from prompt_diary.generate.daily_synthesis.notion_publish import (
    publish_report,
    publish_workspace_report,
)
from prompt_diary.generate.daily_synthesis.render_notion import (
    NotionPagePayload,
    render_notion_artifact,
)
from tests.support.daily_synthesis import (
    build_daily_report_via_api,
    copy_basic_daily_workspace,
    fill_synthesize_slots,
    finalize_daily_report_via_api,
)

if TYPE_CHECKING:
    from pathlib import Path


@dataclass
class _FakeNotionClient:
    """A recording Notion client: returns deterministic ids and never mutates anything real."""

    schema: dict[str, Any]
    calls: list[tuple[Any, ...]] = field(default_factory=list)
    counter: int = 0

    def retrieve_database(self, *, database_id: str) -> dict[str, Any]:
        self.calls.append(("retrieve", database_id))
        return {"properties": self.schema}

    def create_page(self, *, parent: dict[str, Any], properties: dict[str, Any]) -> dict[str, Any]:
        self.calls.append(("create", parent, properties))
        self.counter += 1
        return {"id": "page-1", "url": "https://notion.so/page-x"}

    def append_children(self, *, block_id: str, children: list[dict[str, Any]]) -> dict[str, Any]:
        self.calls.append(("append", block_id, children))
        results: list[dict[str, Any]] = []
        for child in children:
            self.counter += 1
            results.append({"id": f"blk-{self.counter}", "type": child["type"]})
        return {"results": results}


def _schema() -> dict[str, Any]:
    # Mirrors the live "daily report" database shape (a title, two date columns, a people column),
    # but with ASCII names — the mapping is driven by property *type*, not name.
    return {
        "Name": {"type": "title"},
        "Date": {"type": "date"},
        "Created": {"type": "date"},
        "Reporter": {"type": "people"},
    }


def _payload(children: list[dict[str, Any]]) -> NotionPagePayload:
    return NotionPagePayload(
        title="Prompt Diary Report — 2026-05-28",
        properties={
            "report_date": "2026-05-28",
            "status": "final",
            "window": "2026-05-28, Asia/Shanghai",
            "overall_confidence": "medium",
        },
        children=children,
    )


def _para(text: str) -> dict[str, Any]:
    return {"object": "block", "type": "paragraph", "paragraph": {"rich_text": [_run(text)]}}


def _toggle(label: str, children: list[dict[str, Any]]) -> dict[str, Any]:
    return {
        "object": "block",
        "type": "toggle",
        "toggle": {"rich_text": [_run(label)], "children": children},
    }


def _run(text: str) -> dict[str, Any]:
    return {"type": "text", "text": {"content": text}}


def _appends(client: _FakeNotionClient) -> list[tuple[str, list[dict[str, Any]]]]:
    return [(call[1], call[2]) for call in client.calls if call[0] == "append"]


def _appended_text(client: _FakeNotionClient) -> str:
    return " ".join(
        run["text"]["content"]
        for _, children in _appends(client)
        for block in children
        for run in block[block["type"]].get("rich_text", [])
    )


# --- property mapping --------------------------------------------------------------------------


def test_publish_maps_title_by_type_and_date_columns_to_report_date() -> None:
    client = _FakeNotionClient(_schema())

    publish_report(client=client, database_id="db", payload=_payload([]))

    create = next(call for call in client.calls if call[0] == "create")
    properties = create[2]
    assert properties["Name"] == {"title": [_run("Prompt Diary Report — 2026-05-28")]}
    # Both date-typed columns receive the report date; the people column is left for the user.
    assert properties["Date"] == {"date": {"start": "2026-05-28"}}
    assert properties["Created"] == {"date": {"start": "2026-05-28"}}
    assert "Reporter" not in properties


def test_publish_creates_under_the_target_database() -> None:
    client = _FakeNotionClient(_schema())

    publish_report(client=client, database_id="db-123", payload=_payload([]))

    create = next(call for call in client.calls if call[0] == "create")
    assert create[1] == {"database_id": "db-123"}


def test_publish_raises_when_database_has_no_title_property() -> None:
    client = _FakeNotionClient({"Date": {"type": "date"}})

    with pytest.raises(PromptDiaryError, match="no title property"):
        publish_report(client=client, database_id="db", payload=_payload([]))


# --- metadata banner ---------------------------------------------------------------------------


def test_publish_prepends_a_metadata_banner_callout() -> None:
    client = _FakeNotionClient(_schema())

    publish_report(client=client, database_id="db", payload=_payload([]))

    first_block = _appends(client)[0][1][0]
    assert first_block["type"] == "callout"
    text = "".join(run["text"]["content"] for run in first_block["callout"]["rich_text"])
    # Columns the database lacks (status / window / overall confidence) survive in the body.
    assert "Status: final" in text
    assert "Window: 2026-05-28, Asia/Shanghai" in text
    assert "Overall confidence: medium" in text


# --- always create new, never edit -------------------------------------------------------------


def test_publish_creates_exactly_one_new_page_and_never_edits() -> None:
    client = _FakeNotionClient(_schema())

    publish_report(client=client, database_id="db", payload=_payload([_para("x")]))

    kinds = [call[0] for call in client.calls]
    # Exactly one row is created per publish. The stronger "never edits/deletes/archives" guarantee
    # is structural — ``NotionClientProtocol`` exposes no such method — not merely asserted here.
    assert kinds.count("create") == 1
    assert set(kinds) <= {"retrieve", "create", "append"}


def test_publish_returns_created_page_id_and_url() -> None:
    client = _FakeNotionClient(_schema())

    result = publish_report(client=client, database_id="db", payload=_payload([]))

    assert result.page_id == "page-1"
    assert result.url == "https://notion.so/page-x"


# --- request shaping (depth + breadth limits) --------------------------------------------------


def test_publish_appends_a_deep_tree_one_nesting_level_per_request() -> None:
    client = _FakeNotionClient(_schema())
    deep = _toggle("Outer", [_toggle("Inner", [_para("deep leaf")])])

    publish_report(client=client, database_id="db", payload=_payload([deep]))

    appends = _appends(client)
    # Every appended block is shallow: its nested ``children`` are stripped from the request body.
    for _, children in appends:
        for block in children:
            assert "children" not in block[block["type"]]
    # The recursion reached the deepest leaf, via its own append call.
    assert "deep leaf" in _appended_text(client)
    # Page body, Outer's children, and Inner's children are three separate append calls.
    min_append_calls = 3
    assert len(appends) >= min_append_calls


def test_publish_batches_more_than_100_top_level_blocks() -> None:
    client = _FakeNotionClient(_schema())
    many = [_para(f"p{index}") for index in range(150)]

    publish_report(client=client, database_id="db", payload=_payload(many))

    # The page body is banner + 150 paragraphs = 151 blocks, appended to the page in ≤100 batches.
    page_appends = [children for block_id, children in _appends(client) if block_id == "page-1"]
    sizes = [len(children) for children in page_appends]
    expected_total = 151
    assert max(sizes) <= 100
    assert sum(sizes) == expected_total


# --- publish from the workspace artifact -------------------------------------------------------


def test_publish_workspace_report_publishes_the_rendered_artifact(tmp_path: Path) -> None:
    workspace = copy_basic_daily_workspace(tmp_path)
    build_daily_report_via_api(workspace)
    fill_synthesize_slots(workspace)
    finalize_daily_report_via_api(workspace)
    render_notion_artifact(workspace_path=workspace)
    client = _FakeNotionClient(_schema())

    result = publish_workspace_report(workspace_path=workspace, client=client, database_id="db")

    assert result.page_id == "page-1"
    create = next(call for call in client.calls if call[0] == "create")
    assert create[2]["Name"]["title"][0]["text"]["content"] == "Prompt Diary Report — 2026-05-28"
    # The real report is deep and wide; every append still respects the shallow / ≤100 contract.
    for _, children in _appends(client):
        assert len(children) <= 100
        for block in children:
            assert "children" not in block[block["type"]]
    # A known model claim from the basic fixture reached the appended blocks.
    assert "Three-layer QA strategy delivered." in _appended_text(client)


def test_publish_workspace_report_raises_when_artifact_missing(tmp_path: Path) -> None:
    client = _FakeNotionClient(_schema())

    with pytest.raises(PromptDiaryError, match="no Notion report payload"):
        publish_workspace_report(workspace_path=tmp_path, client=client, database_id="db")


# --- contract-violation / failure paths --------------------------------------------------------

_NETWORK_DOWN = "network down"


def test_publish_refuses_to_publish_without_report_date() -> None:
    client = _FakeNotionClient(_schema())
    payload = NotionPagePayload(title="t", properties={"status": "final"}, children=[])

    with pytest.raises(PromptDiaryError, match="undated row"):
        publish_report(client=client, database_id="db", payload=payload)


def test_publish_raises_when_create_returns_no_page_id() -> None:
    class _NoId(_FakeNotionClient):
        def create_page(
            self, *, parent: dict[str, Any], properties: dict[str, Any]
        ) -> dict[str, Any]:
            self.calls.append(("create", parent, properties))
            return {"url": "https://notion.so/x"}

    client = _NoId(_schema())

    with pytest.raises(PromptDiaryError, match="no page id"):
        publish_report(client=client, database_id="db", payload=_payload([]))


def test_publish_raises_when_append_result_count_mismatches() -> None:
    class _Short(_FakeNotionClient):
        def append_children(
            self, *, block_id: str, children: list[dict[str, Any]]
        ) -> dict[str, Any]:
            self.calls.append(("append", block_id, children))
            return {"results": []}  # fewer results than blocks sent

    client = _Short(_schema())

    with pytest.raises(PromptDiaryError, match="partial row"):
        publish_report(client=client, database_id="db", payload=_payload([_para("x")]))


def test_publish_raises_when_append_result_lacks_a_block_id() -> None:
    class _NoBlockId(_FakeNotionClient):
        def append_children(
            self, *, block_id: str, children: list[dict[str, Any]]
        ) -> dict[str, Any]:
            self.calls.append(("append", block_id, children))
            return {"results": [{"type": child["type"]} for child in children]}  # ids missing

    client = _NoBlockId(_schema())
    deep = _toggle("Outer", [_para("inner")])

    with pytest.raises(PromptDiaryError, match="partial row"):
        publish_report(client=client, database_id="db", payload=_payload([deep]))


def test_publish_wraps_an_append_failure_with_the_created_page_location() -> None:
    class _Boom(_FakeNotionClient):
        def append_children(
            self, *, block_id: str, children: list[dict[str, Any]]
        ) -> dict[str, Any]:
            self.calls.append(("append", block_id, children))
            raise RuntimeError(_NETWORK_DOWN)

    client = _Boom(_schema())

    with pytest.raises(PromptDiaryError, match="partial row") as exc_info:
        publish_report(client=client, database_id="db", payload=_payload([_para("x")]))
    # The created page's URL is surfaced so the partial row can be found and deleted.
    assert "https://notion.so/page-x" in str(exc_info.value)
