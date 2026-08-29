"""GET /v1/corpus/recent — recent cleared question records for the public corpus page.

Returns sanitised, publishable records (same criteria as the monthly
publication snapshot: consent_version IS NOT NULL, capture_level IN
('full','minimal'), review_status = 'cleared'). Ordered by timestamp DESC.

Also serves GET /v1/corpus/manifest — reads the corpus/manifest.json from
the repo root if it exists, so the UI can show publication provenance.
"""

import json
from pathlib import Path

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import FileResponse
from sqlalchemy import text

router = APIRouter(prefix="/v1/corpus", tags=["corpus"])

_REPO_ROOT = Path(__file__).resolve().parent.parent.parent.parent
_CORPUS_DIR = _REPO_ROOT / "corpus"


@router.get("/recent")
async def recent_questions(
    request: Request,
    limit: int = 50,
) -> dict[str, object]:
    engine = request.app.state.engine
    # Clamp limit to a sane range
    limit = max(1, min(limit, 200))
    async with engine.connect() as conn:
        rows = (
            await conn.execute(
                text(
                    """
                    SELECT id, timestamp, capture_level,
                           natural_language_question, tool_called,
                           geography_referenced, indicators_returned,
                           sources_used, result_status, asker_sector,
                           marked_useful
                    FROM corpus.question_record
                    WHERE consent_version IS NOT NULL
                      AND capture_level IN ('full', 'minimal')
                      AND review_status = 'cleared'
                    ORDER BY timestamp DESC, id DESC
                    LIMIT :limit
                    """
                ),
                {"limit": limit},
            )
        ).all()

    return {
        "count": len(rows),
        "questions": [
            {
                "id": str(r.id),
                "timestamp": r.timestamp.isoformat() if r.timestamp else None,
                "capture_level": r.capture_level,
                "question": r.natural_language_question,
                "tool_called": r.tool_called,
                "geography": _geography_summary(r.geography_referenced),
                "indicators": list(r.indicators_returned or []),
                "sources": list(r.sources_used or []),
                "result_status": r.result_status,
                "asker_sector": r.asker_sector,
                "marked_useful": r.marked_useful,
            }
            for r in rows
        ],
    }


def _geography_summary(ref: object) -> list[dict[str, str]]:
    if isinstance(ref, list):
        return [
            {"type": str(item.get("type", "")), "name": str(item.get("name", ""))}
            for item in ref
            if isinstance(item, dict) and item.get("type")
        ]
    if isinstance(ref, dict) and ref.get("type"):
        return [{"type": str(ref.get("type", "")), "name": str(ref.get("name", ""))}]
    return []


@router.get("/files/{filename}")
async def get_corpus_file(filename: str) -> FileResponse:
    """Serve a published corpus artefact listed in the current manifest."""
    manifest_path = _CORPUS_DIR / "manifest.json"
    if not manifest_path.exists():
        raise HTTPException(status_code=404, detail="corpus manifest unavailable")

    try:
        manifest = json.loads(manifest_path.read_text())
    except (json.JSONDecodeError, OSError) as exc:
        raise HTTPException(status_code=404, detail="corpus manifest unavailable") from exc

    allowed = {
        str(item.get("name"))
        for item in manifest.get("files", [])
        if isinstance(item, dict) and item.get("name")
    }
    if filename not in allowed:
        raise HTTPException(status_code=404, detail="corpus file not published")

    path = _CORPUS_DIR / filename
    if not path.is_file() or path.parent != _CORPUS_DIR:
        raise HTTPException(status_code=404, detail="corpus file unavailable")

    return FileResponse(
        path,
        media_type="application/gzip",
        filename=filename,
    )


@router.get("/manifest")
async def get_manifest() -> dict[str, object]:
    manifest_path = _CORPUS_DIR / "manifest.json"
    if not manifest_path.exists():
        return {"available": False}
    try:
        data = json.loads(manifest_path.read_text())
        # Also list the actual files so the UI can link to them
        files = []
        for f in data.get("files", []):
            name = f.get("name", "")
            path = _CORPUS_DIR / name
            files.append(
                {
                    "name": name,
                    "sha256": f.get("sha256", ""),
                    "size_bytes": f.get("size_bytes", 0),
                    "exists": path.exists(),
                }
            )
        return {
            "available": True,
            "period": data.get("period"),
            "catalogue_version": data.get("catalogue_version"),
            "sanitisation_rules_version": data.get("sanitisation_rules_version"),
            "generator_git_sha": data.get("generator_git_sha"),
            "files": files,
        }
    except Exception:
        return {"available": False}
