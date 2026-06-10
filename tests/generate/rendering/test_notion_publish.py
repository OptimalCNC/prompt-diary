"""Tests for the Notion publisher logic against a fake client.

The publisher pushes a rendered ``report.notion.json`` payload into a Notion database as a new row.
These tests pin the schema-driven property mapping (title by type, date column ← report date, a
reporter into its text column, others left alone), the status-colored banner and table of contents
prepended to the body, the always-create-new behaviour (one create, never an
edit/delete), and the request shaping that keeps every append within Notion's
≤100 top-level children / ≤1000 block-elements / two-level nesting limits while still uploading an
arbitrarily deep tree. The real SDK is exercised only behind the thin adapter; here a fake client
records calls and returns ids.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, cast

import pytest

from prompt_diary.config import ReporterTarget
from prompt_diary.errors import PromptDiaryError
from prompt_diary.generate.rendering.notion_publish import (
    publish_report,
    publish_workspace_report,
)
from prompt_diary.generate.rendering.render_notion import (
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
    from collections.abc import Iterator
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

    def create_page(
        self,
        *,
        parent: dict[str, Any],
        properties: dict[str, Any],
        children: list[dict[str, Any]] | None = None,
    ) -> dict[str, Any]:
        self.calls.append(("create", parent, properties, children or []))
        self.counter += 1
        return {"id": f"page-{self.counter}", "url": f"https://notion.so/page-{self.counter}"}

    def append_children(
        self,
        *,
        block_id: str,
        children: list[dict[str, Any]],
        after_block_id: str | None = None,
    ) -> dict[str, Any]:
        return self._append_children(
            block_id=block_id, children=children, after_block_id=after_block_id
        )

    def _append_children(
        self,
        *,
        block_id: str,
        children: list[dict[str, Any]],
        after_block_id: str | None,
    ) -> dict[str, Any]:
        self.calls.append(("append", block_id, children, after_block_id))
        results: list[dict[str, Any]] = []
        for child in children:
            self.counter += 1
            results.append({"id": f"blk-{self.counter}", "type": child["type"]})
        return {"results": results}


def _schema() -> dict[str, Any]:
    # Mirrors the live "daily report" database shape after the user's schema change: a title, a date
    # column (日期), a Notion-managed created-time column (创建时间), and a text column for the
    # reporter (汇报人). ASCII names — the mapping is driven by property *type*, not name.
    return {
        "Name": {"type": "title"},
        "Date": {"type": "date"},
        "Created": {"type": "created_time"},
        "Reporter": {"type": "rich_text"},
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


def _creates(
    client: _FakeNotionClient,
) -> list[tuple[dict[str, Any], dict[str, Any], list[dict[str, Any]]]]:
    return [(call[1], call[2], call[3]) for call in client.calls if call[0] == "create"]


def _appends_with_after(
    client: _FakeNotionClient,
) -> list[tuple[str, list[dict[str, Any]], str | None]]:
    return [(call[1], call[2], call[3]) for call in client.calls if call[0] == "append"]


def _created_body(client: _FakeNotionClient) -> list[dict[str, Any]]:
    create = next(call for call in client.calls if call[0] == "create")
    return cast("list[dict[str, Any]]", create[3])


def _published_body(client: _FakeNotionClient) -> list[dict[str, Any]]:
    created = _created_body(client)
    if created:
        return created
    return _appends(client)[0][1]


def _appended_text(client: _FakeNotionClient) -> str:
    return " ".join(
        run["text"]["content"]
        for children in [_created_body(client), *[children for _, children in _appends(client)]]
        for block in _iter_request_blocks(children)
        for run in block[block["type"]].get("rich_text", [])
        if run["type"] == "text"
    )


def _iter_request_blocks(blocks: list[dict[str, Any]]) -> Iterator[dict[str, Any]]:
    for block in blocks:
        yield block
        yield from _iter_request_blocks(_request_children(block))


def _request_children(block: dict[str, Any]) -> list[dict[str, Any]]:
    body = block[block["type"]]
    value = body.get("children", [])
    return cast("list[dict[str, Any]]", value) if isinstance(value, list) else []


def _request_block_count(blocks: list[dict[str, Any]]) -> int:
    return sum(1 + _request_block_count(_request_children(block)) for block in blocks)


def _all_request_runs(blocks: list[dict[str, Any]]) -> Iterator[dict[str, Any]]:
    for block in _iter_request_blocks(blocks):
        yield from block[block["type"]].get("rich_text", [])


def _internal_link_target() -> dict[str, str]:
    return {"project_key": "k", "session_ref": "S0001", "turn_ref": "T0001"}


def _linked_run(text: str, target: dict[str, str]) -> dict[str, Any]:
    return {
        "type": "text",
        "text": {"content": text},
        "_prompt_diary_link_target": target,
    }


def _evidence_toggle(
    target: dict[str, str], children: list[dict[str, Any]] | None = None
) -> dict[str, Any]:
    block = _toggle(f"{target['session_ref']}/{target['turn_ref']}", children or [])
    block["_prompt_diary_evidence_target"] = target
    return block


def _evidence_appendix(target: dict[str, str]) -> dict[str, Any]:
    block = _heading("Evidence Chains", level=1)
    block["heading_1"]["is_toggleable"] = True
    block["heading_1"]["children"] = [
        _heading("Project K", level=2),
        _evidence_toggle(target, [_para("Trigger: User asked for the linked work.")]),
    ]
    block["_prompt_diary_evidence_appendix"] = True
    return block


def _heading(text: str, *, level: int = 3) -> dict[str, Any]:
    block_type = f"heading_{level}"
    return {"object": "block", "type": block_type, block_type: {"rich_text": [_run(text)]}}


def _assert_append_request_limits(client: _FakeNotionClient) -> None:
    for _, children in _appends(client):
        assert len(children) <= 100
        assert _request_block_count(children) <= 1000
        for block in children:
            for child in _request_children(block):
                assert _request_children(child) == []


def _assert_published_request_limits(client: _FakeNotionClient) -> None:
    created = _created_body(client)
    if created:
        assert len(created) <= 100
        assert _request_block_count(created) <= 1000
        for block in created:
            for child in _request_children(block):
                assert _request_children(child) == []
    _assert_append_request_limits(client)


# --- property mapping --------------------------------------------------------------------------


def test_publish_maps_title_and_date_but_leaves_managed_and_text_columns() -> None:
    client = _FakeNotionClient(_schema())

    publish_report(client=client, database_id="db", payload=_payload([]))

    create = next(call for call in client.calls if call[0] == "create")
    properties = create[2]
    assert properties["Name"] == {"title": [_run("Prompt Diary Report — 2026-05-28")]}
    # The date column gets the report date. The Notion-managed created_time column is left alone
    # (Notion auto-fills it, with time). The text column stays empty without a reporter.
    assert properties["Date"] == {"date": {"start": "2026-05-28"}}
    assert "Created" not in properties
    assert "Reporter" not in properties


def test_publish_writes_reporter_into_the_named_text_column() -> None:
    client = _FakeNotionClient(_schema())

    result = publish_report(
        client=client,
        database_id="db",
        payload=_payload([]),
        reporter=ReporterTarget(column="Reporter", name="Wei Hu"),
    )

    properties = next(call for call in client.calls if call[0] == "create")[2]
    assert properties["Reporter"] == {"rich_text": [_run("Wei Hu")]}
    assert result.warnings == ()  # a clean write carries no warning


def test_publish_warns_when_a_column_exists_but_no_reporter_name_is_configured() -> None:
    # The common case the user hit: the database HAS the column, but no name is set, so it was left
    # empty. That must be flagged, not silently skipped.
    client = _FakeNotionClient(_schema())

    result = publish_report(
        client=client,
        database_id="db",
        payload=_payload([]),
        reporter=ReporterTarget(column="Reporter", name=None),
    )

    properties = next(call for call in client.calls if call[0] == "create")[2]
    assert "Reporter" not in properties  # nothing written...
    assert any("Reporter" in warning for warning in result.warnings)  # ...but the gap is reported


def test_publish_warns_when_the_named_column_is_absent() -> None:
    client = _FakeNotionClient(_schema())

    result = publish_report(
        client=client,
        database_id="db",
        payload=_payload([]),
        reporter=ReporterTarget(column="Nope", name="Wei Hu"),
    )

    properties = next(call for call in client.calls if call[0] == "create")[2]
    assert "Nope" not in properties  # a misnamed column never fails an otherwise-good publish...
    assert any("Nope" in warning for warning in result.warnings)  # ...but it is no longer silent


def test_publish_warns_when_the_named_column_is_not_text() -> None:
    client = _FakeNotionClient(_schema())

    result = publish_report(
        client=client,
        database_id="db",
        payload=_payload([]),
        reporter=ReporterTarget(column="Date", name="Wei Hu"),
    )

    properties = next(call for call in client.calls if call[0] == "create")[2]
    # Targeting a non-text column never clobbers it: Date keeps its report-date mapping.
    assert properties["Date"] == {"date": {"start": "2026-05-28"}}
    assert any("Date" in warning for warning in result.warnings)


def test_publish_warns_when_a_wrong_type_column_exists_and_no_name_is_configured() -> None:
    # An existing reporter-named column of the wrong type, with no name, must still be flagged: it
    # cannot be filled, so it is NOT the same as "this database has no reporter column at all".
    client = _FakeNotionClient(_schema())

    result = publish_report(
        client=client,
        database_id="db",
        payload=_payload([]),
        reporter=ReporterTarget(column="Date", name=None),
    )

    properties = next(call for call in client.calls if call[0] == "create")[2]
    assert properties["Date"] == {"date": {"start": "2026-05-28"}}  # the date mapping is untouched
    assert any("Date" in warning for warning in result.warnings)  # the wrong-type column is flagged


def test_publish_is_silent_when_there_is_no_reporter_column_and_no_name() -> None:
    # A database that simply has no reporter column is never nagged about a missing reporter.
    client = _FakeNotionClient(_schema())

    result = publish_report(
        client=client,
        database_id="db",
        payload=_payload([]),
        reporter=ReporterTarget(column="Nope", name=None),
    )

    assert result.warnings == ()


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

    first_block = _published_body(client)[0]
    assert first_block["type"] == "callout"
    text = "".join(run["text"]["content"] for run in first_block["callout"]["rich_text"])
    # Columns the database lacks (status / window / overall confidence) survive in the body.
    assert "Status: final" in text
    assert "Window: 2026-05-28, Asia/Shanghai" in text
    assert "Overall confidence: medium" in text


def test_publish_colors_the_banner_by_status() -> None:
    final_client = _FakeNotionClient(_schema())
    publish_report(client=final_client, database_id="db", payload=_payload([]))
    final_banner = _published_body(final_client)[0]
    assert final_banner["callout"]["color"] == "green_background"

    partial_client = _FakeNotionClient(_schema())
    partial = NotionPagePayload(
        title="t", properties={"report_date": "2026-05-28", "status": "partial"}, children=[]
    )
    publish_report(client=partial_client, database_id="db", payload=partial)
    partial_banner = _published_body(partial_client)[0]
    assert partial_banner["callout"]["color"] == "yellow_background"

    other_client = _FakeNotionClient(_schema())
    other = NotionPagePayload(
        title="t", properties={"report_date": "2026-05-28", "status": "draft"}, children=[]
    )
    publish_report(client=other_client, database_id="db", payload=other)
    other_banner = _published_body(other_client)[0]
    assert other_banner["callout"]["color"] == "gray_background"  # neutral fallback


def test_publish_inserts_a_table_of_contents_after_the_banner() -> None:
    client = _FakeNotionClient(_schema())

    publish_report(client=client, database_id="db", payload=_payload([_para("body")]))

    body = _published_body(client)
    assert body[0]["type"] == "callout"  # banner first
    assert body[1]["type"] == "table_of_contents"  # then a navigable table of contents


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
    assert result.url == "https://notion.so/page-1"


# --- request shaping (depth + breadth limits) --------------------------------------------------


def test_publish_creates_page_with_body_when_request_fits() -> None:
    client = _FakeNotionClient(_schema())

    publish_report(client=client, database_id="db", payload=_payload([_para("body")]))

    body = _created_body(client)
    assert [block["type"] for block in body] == ["callout", "table_of_contents", "paragraph"]
    assert _appends(client) == []


def test_publish_uses_two_level_requests_without_losing_deep_child_ids() -> None:
    client = _FakeNotionClient(_schema())
    deep = _toggle("Outer", [_toggle("Inner", [_para("deep leaf")])])

    publish_report(client=client, database_id="db", payload=_payload([deep]))

    appends = _appends(client)
    _assert_append_request_limits(client)
    # The outer toggle's child still needs its own descendants, so the outer request is stripped to
    # keep the inner toggle's id available from a first-level append result.
    page_blocks = appends[0][1]
    outer = page_blocks[2]
    assert "children" not in outer["toggle"]
    # The inner toggle's child is a leaf, so it is safe to inline in the second append request.
    inner = appends[1][1][0]
    assert inner["toggle"]["children"] == [_para("deep leaf")]
    assert "deep leaf" in _appended_text(client)
    assert len(appends) == 2


def test_publish_linked_citations_anchor_first_and_insert_main_before_appendix() -> None:
    client = _FakeNotionClient(_schema())
    target = _internal_link_target()
    claim = _para("Main claim ")
    claim["paragraph"]["rich_text"].append(_linked_run("S0001/T0001", target))
    payload = _payload([claim, _evidence_appendix(target)])

    publish_report(client=client, database_id="db", payload=payload)

    assert _created_body(client) == []
    creates = _creates(client)
    assert len(creates) == 1
    appends = _appends_with_after(client)
    assert len(appends) == 3
    assert appends[0][0] == "page-1"
    assert appends[0][2] is None
    assert [block["type"] for block in appends[0][1]] == [
        "callout",
        "table_of_contents",
        "heading_1",
    ]
    assert "children" not in appends[0][1][2]["heading_1"]
    assert appends[1][0] == "blk-4"
    assert [block["type"] for block in appends[1][1]] == ["heading_2", "toggle"]
    assert appends[2][0] == "page-1"
    assert appends[2][2] == "blk-3"
    start_body = appends[2][1]
    assert [block["type"] for block in start_body] == ["paragraph"]
    linked = next(run for run in _all_request_runs(start_body) if run["type"] == "mention")
    assert linked == {
        "type": "mention",
        "mention": {"type": "page", "page": {"id": "blk-6"}},
    }
    for _, children, _ in appends:
        for block in _iter_request_blocks(children):
            assert "_prompt_diary_evidence_appendix" not in block
            assert "_prompt_diary_evidence_target" not in block
        for run in _all_request_runs(children):
            assert "_prompt_diary_link_target" not in run


def test_publish_linked_citations_capture_chunked_evidence_targets() -> None:
    client = _FakeNotionClient(_schema())
    targets = [
        {"project_key": "k", "session_ref": "S0001", "turn_ref": f"T{index:04d}"}
        for index in range(105)
    ]
    claim = _para("Main claim ")
    claim["paragraph"]["rich_text"].append(_linked_run("S0001/T0104", targets[-1]))
    appendix = _evidence_appendix(targets[0])
    appendix["heading_1"]["children"] = [
        _heading("Project K", level=2),
        *[
            _evidence_toggle(target, [_para(f"Evidence {index}")])
            for index, target in enumerate(targets)
        ],
    ]

    result = publish_report(client=client, database_id="db", payload=_payload([claim, appendix]))

    assert result.warnings == ()
    appends = _appends_with_after(client)
    assert len(appends) == 4
    assert [len(children) for parent, children, _ in appends if parent == "blk-4"] == [100, 6]
    linked_body = appends[-1][1]
    linked = next(run for run in _all_request_runs(linked_body) if run["type"] == "mention")
    assert linked == {
        "type": "mention",
        "mention": {"type": "page", "page": {"id": "blk-110"}},
    }


def test_publish_linked_citations_without_appendix_warn_and_publish_unlinked() -> None:
    client = _FakeNotionClient(_schema())
    target = _internal_link_target()
    claim = _para("Main claim ")
    claim["paragraph"]["rich_text"].append(_linked_run("S0001/T0001", target))

    result = publish_report(client=client, database_id="db", payload=_payload([claim]))

    assert any("evidence appendix" in warning for warning in result.warnings)
    assert _created_body(client) == []
    appended = _appends_with_after(client)[0][1]
    citation = next(
        run for run in _all_request_runs(appended) if run["text"]["content"] == "S0001/T0001"
    )
    assert "link" not in citation["text"]


def test_publish_linked_citations_warn_when_target_block_is_missing() -> None:
    client = _FakeNotionClient(_schema())
    target = _internal_link_target()
    other_target = {"project_key": "k", "session_ref": "S0002", "turn_ref": "T0001"}
    claim = _para("Main claim ")
    claim["paragraph"]["rich_text"].append(_linked_run("S0001/T0001", target))

    result = publish_report(
        client=client,
        database_id="db",
        payload=_payload([claim, _evidence_appendix(other_target)]),
    )

    assert any("1 Notion citation target" in warning for warning in result.warnings)
    start_body = next(children for _, children, after in _appends_with_after(client) if after)
    citation = next(
        run for run in _all_request_runs(start_body) if run["text"]["content"] == "S0001/T0001"
    )
    assert "link" not in citation["text"]


def test_publish_linked_citations_fall_back_when_native_block_mention_is_rejected() -> None:
    class _RejectNativeMention(_FakeNotionClient):
        def append_children(
            self,
            *,
            block_id: str,
            children: list[dict[str, Any]],
            after_block_id: str | None = None,
        ) -> dict[str, Any]:
            if any(run["type"] == "mention" for run in _all_request_runs(children)):
                self.calls.append(("append", block_id, children, after_block_id))
                raise RuntimeError(_NETWORK_DOWN)
            return super().append_children(
                block_id=block_id,
                children=children,
                after_block_id=after_block_id,
            )

    client = _RejectNativeMention(_schema())
    target = _internal_link_target()
    missing_target = {"project_key": "k", "session_ref": "S0002", "turn_ref": "T0001"}
    claim = _para("Main claim ")
    claim["paragraph"]["rich_text"].append(_linked_run("S0001/T0001", target))
    claim["paragraph"]["rich_text"].append(_run("; "))
    claim["paragraph"]["rich_text"].append(_linked_run("S0002/T0001", missing_target))

    result = publish_report(
        client=client,
        database_id="db",
        payload=_payload([claim, _evidence_appendix(target)]),
    )

    assert any("native Notion evidence block mention" in warning for warning in result.warnings)
    assert any("1 Notion citation target" in warning for warning in result.warnings)
    fallback_body = _appends_with_after(client)[-1][1]
    citation = next(
        run for run in _all_request_runs(fallback_body) if run["text"]["content"] == "S0001/T0001"
    )
    assert citation["text"]["link"] == {"url": "https://notion.so/page-1#blk-6"}
    missing = next(
        run for run in _all_request_runs(fallback_body) if run["text"]["content"] == "S0002/T0001"
    )
    assert "link" not in missing["text"]


def test_publish_linked_citations_fall_back_when_after_insert_is_rejected() -> None:
    class _RejectAfter(_FakeNotionClient):
        def append_children(
            self,
            *,
            block_id: str,
            children: list[dict[str, Any]],
            after_block_id: str | None = None,
        ) -> dict[str, Any]:
            if after_block_id is not None:
                self.calls.append(("append", block_id, children, after_block_id))
                raise RuntimeError(_NETWORK_DOWN)
            return super().append_children(
                block_id=block_id,
                children=children,
                after_block_id=after_block_id,
            )

    client = _RejectAfter(_schema())
    target = _internal_link_target()
    claim = _para("Main claim ")
    claim["paragraph"]["rich_text"].append(_linked_run("S0001/T0001", target))

    result = publish_report(
        client=client,
        database_id="db",
        payload=_payload([claim, _evidence_appendix(target)]),
    )

    assert any(
        "inserting linked report content before the evidence appendix" in warning
        for warning in result.warnings
    )
    fallback_body = _appends_with_after(client)[-1][1]
    citation = next(
        run for run in _all_request_runs(fallback_body) if run["text"]["content"] == "S0001/T0001"
    )
    assert "link" not in citation["text"]


def test_publish_linked_citations_accepts_after_response_with_following_siblings() -> None:
    class _AfterReturnsFollowingSiblings(_FakeNotionClient):
        def _append_children(
            self,
            *,
            block_id: str,
            children: list[dict[str, Any]],
            after_block_id: str | None,
        ) -> dict[str, Any]:
            response = super()._append_children(
                block_id=block_id,
                children=children,
                after_block_id=after_block_id,
            )
            if after_block_id is not None:
                response["results"].append({"id": "already-existing-sibling", "type": "paragraph"})
            return response

    client = _AfterReturnsFollowingSiblings(_schema())
    target = _internal_link_target()
    claim = _para("Main claim ")
    claim["paragraph"]["rich_text"].append(_linked_run("S0001/T0001", target))

    result = publish_report(
        client=client,
        database_id="db",
        payload=_payload([claim, _evidence_appendix(target)]),
    )

    assert result.warnings == ()
    linked_body = _appends_with_after(client)[-1][1]
    linked = next(run for run in _all_request_runs(linked_body) if run["type"] == "mention")
    assert linked["mention"] == {"type": "page", "page": {"id": "blk-6"}}


def test_publish_linked_citations_skips_untargeted_appendix_toggles() -> None:
    client = _FakeNotionClient(_schema())
    target = _internal_link_target()
    claim = _para("Main claim ")
    claim["paragraph"]["rich_text"].append(_linked_run("S0001/T0001", target))
    appendix = _evidence_appendix(target)
    appendix["heading_1"]["children"].insert(
        0,
        _toggle("Untargeted evidence note", [_para("No target metadata.")]),
    )

    result = publish_report(client=client, database_id="db", payload=_payload([claim, appendix]))

    assert result.warnings == ()
    linked_body = _appends_with_after(client)[-1][1]
    linked = next(run for run in _all_request_runs(linked_body) if run["type"] == "mention")
    assert linked["mention"] == {"type": "page", "page": {"id": "blk-7"}}


def test_publish_linked_citations_warn_when_evidence_toggle_result_lacks_id() -> None:
    class _NoEvidenceToggleId(_FakeNotionClient):
        def _append_children(
            self,
            *,
            block_id: str,
            children: list[dict[str, Any]],
            after_block_id: str | None,
        ) -> dict[str, Any]:
            response = super()._append_children(
                block_id=block_id,
                children=children,
                after_block_id=after_block_id,
            )
            for child, result in zip(children, response["results"], strict=True):
                if child["type"] == "toggle":
                    result.pop("id", None)
            return response

    client = _NoEvidenceToggleId(_schema())
    target = _internal_link_target()
    claim = _para("Main claim ")
    claim["paragraph"]["rich_text"].append(_linked_run("S0001/T0001", target))

    result = publish_report(
        client=client,
        database_id="db",
        payload=_payload([claim, _evidence_appendix(target)]),
    )

    assert any("1 Notion citation target" in warning for warning in result.warnings)
    linked_body = _appends_with_after(client)[-1][1]
    citation = next(
        run for run in _all_request_runs(linked_body) if run["text"]["content"] == "S0001/T0001"
    )
    assert "link" not in citation["text"]


def test_publish_inlines_leaf_children_in_their_parent_append() -> None:
    client = _FakeNotionClient(_schema())
    fillers = [_para(f"filler {index}") for index in range(100)]
    parent = _toggle("Parent", [_para("leaf one"), _para("leaf two")])

    publish_report(client=client, database_id="db", payload=_payload([*fillers, parent]))

    appends = _appends(client)
    appended_parent = next(
        block for _, children in appends for block in children if block["type"] == "toggle"
    )
    assert appended_parent["type"] == "toggle"
    assert appended_parent["toggle"]["children"] == [_para("leaf one"), _para("leaf two")]


def test_publish_splits_inlined_batches_before_request_block_limit() -> None:
    client = _FakeNotionClient(_schema())
    children = [
        _toggle(f"Parent {index}", [_para(f"leaf {index}-{n}") for n in range(100)])
        for index in range(10)
    ]

    publish_report(client=client, database_id="db", payload=_payload(children))

    page_appends = [children for block_id, children in _appends(client) if block_id == "page-1"]
    assert [len(children) for children in page_appends] == [11, 1]
    _assert_append_request_limits(client)


def test_publish_batches_more_than_100_top_level_blocks() -> None:
    client = _FakeNotionClient(_schema())
    many = [_para(f"p{index}") for index in range(150)]

    publish_report(client=client, database_id="db", payload=_payload(many))

    # The page body is banner + ToC + 150 paragraphs = 152 blocks, appended in ≤100 batches.
    page_appends = [children for block_id, children in _appends(client) if block_id == "page-1"]
    sizes = [len(children) for children in page_appends]
    expected_total = 152
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
    # The real report is deep and wide; every request still respects Notion's request-shape limits.
    _assert_published_request_limits(client)
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
            self,
            *,
            parent: dict[str, Any],
            properties: dict[str, Any],
            children: list[dict[str, Any]] | None = None,
        ) -> dict[str, Any]:
            self.calls.append(("create", parent, properties, children or []))
            return {"url": "https://notion.so/x"}

    client = _NoId(_schema())

    with pytest.raises(PromptDiaryError, match="no page id"):
        publish_report(client=client, database_id="db", payload=_payload([]))


def test_publish_raises_when_append_result_count_mismatches() -> None:
    class _Short(_FakeNotionClient):
        def append_children(
            self,
            *,
            block_id: str,
            children: list[dict[str, Any]],
            after_block_id: str | None = None,
        ) -> dict[str, Any]:
            self.calls.append(("append", block_id, children, after_block_id))
            return {"results": []}  # fewer results than blocks sent

    client = _Short(_schema())

    with pytest.raises(PromptDiaryError, match="partial row"):
        publish_report(
            client=client,
            database_id="db",
            payload=_payload([_para(f"x{index}") for index in range(150)]),
        )


def test_publish_raises_when_append_result_lacks_a_block_id() -> None:
    class _NoBlockId(_FakeNotionClient):
        def append_children(
            self,
            *,
            block_id: str,
            children: list[dict[str, Any]],
            after_block_id: str | None = None,
        ) -> dict[str, Any]:
            self.calls.append(("append", block_id, children, after_block_id))
            return {"results": [{"type": child["type"]} for child in children]}  # ids missing

    client = _NoBlockId(_schema())
    deep = _toggle("Outer", [_toggle("Inner", [_para("inner")])])

    with pytest.raises(PromptDiaryError, match="partial row"):
        publish_report(client=client, database_id="db", payload=_payload([deep]))


def test_publish_wraps_a_retrieve_or_create_failure_with_an_actionable_message() -> None:
    class _RetrieveBoom(_FakeNotionClient):
        def retrieve_database(self, *, database_id: str) -> dict[str, Any]:
            self.calls.append(("retrieve", database_id))
            raise RuntimeError(_NETWORK_DOWN)  # e.g. a 401/404/timeout from the SDK

    client = _RetrieveBoom(_schema())

    with pytest.raises(PromptDiaryError, match="Notion request failed"):
        publish_report(client=client, database_id="db", payload=_payload([]))


def test_publish_wraps_an_append_failure_with_the_created_page_location() -> None:
    class _Boom(_FakeNotionClient):
        def append_children(
            self,
            *,
            block_id: str,
            children: list[dict[str, Any]],
            after_block_id: str | None = None,
        ) -> dict[str, Any]:
            self.calls.append(("append", block_id, children, after_block_id))
            raise RuntimeError(_NETWORK_DOWN)

    client = _Boom(_schema())

    with pytest.raises(PromptDiaryError, match="partial row") as exc_info:
        publish_report(
            client=client,
            database_id="db",
            payload=_payload([_para(f"x{index}") for index in range(150)]),
        )
    # The created page's URL is surfaced so the partial row can be found and deleted.
    assert "https://notion.so/page-1" in str(exc_info.value)
