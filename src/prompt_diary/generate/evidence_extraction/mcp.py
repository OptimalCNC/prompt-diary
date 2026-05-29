"""Transport-independent evidence extraction MCP tool APIs."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Literal, TypeAlias

from prompt_diary.errors import PromptDiaryError

if TYPE_CHECKING:
    from pathlib import Path


@dataclass(frozen=True)
class EvidenceWriteError:
    """Structured validation error returned by rejected evidence writes."""

    path: str
    message: str
    hint: str


@dataclass(frozen=True)
class WriteEvidenceAppendedResult:
    """Successful evidence-chain write result."""

    status: Literal["appended"]
    project_key: str
    session_ref: str
    turn_ref: str


@dataclass(frozen=True)
class WriteEvidenceInvalidResult:
    """Rejected evidence-chain write result."""

    status: Literal["invalid"]
    errors: tuple[EvidenceWriteError, ...]


WriteEvidenceResult: TypeAlias = WriteEvidenceAppendedResult | WriteEvidenceInvalidResult


def write_evidence(
    *,
    workspace_path: Path,
    project_key: str,
    session_ref: str,
    evidence_chain: dict[str, Any],
) -> WriteEvidenceResult:
    """Validate and append one evidence chain to a session evidence card.

    The canonical validation and write implementation will be added with the evidence extraction
    API. This placeholder keeps the public API surface explicit for tests and MCP registration.
    """
    del workspace_path, project_key, session_ref, evidence_chain
    raise PromptDiaryError(_not_implemented_message())


def _not_implemented_message() -> str:
    return "write_evidence API is not implemented yet"
