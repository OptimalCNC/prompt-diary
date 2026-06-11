from pathlib import Path


DOCS_WORKFLOW = Path(__file__).resolve().parents[1] / ".github" / "workflows" / "docs.yml"


def test_docs_workflow_publishes_docs_from_main_docs_changes() -> None:
    content = DOCS_WORKFLOW.read_text(encoding="utf-8")

    assert "workflow_dispatch" not in content
    assert "branches:" in content
    assert "- main" in content
    assert '- "docs/**"' in content
    assert '- ".github/workflows/docs.yml"' in content
    assert "mdbook build docs" in content
    assert "actions/upload-pages-artifact" in content
    assert "path: docs/book" in content
    assert "actions/deploy-pages" in content
