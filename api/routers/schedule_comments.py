"""Schedule Comments router (Q069): day-level notes for managers."""

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field

from ..dependencies import (
    _sanitize_500,
    get_db,
    require_auth,
    require_planer,
)

router = APIRouter()


# ── Schemas ──────────────────────────────────────────────────


class ScheduleCommentCreate(BaseModel):
    date: str = Field(..., pattern=r"^\d{4}-\d{2}-\d{2}$", description="Date YYYY-MM-DD")
    group_id: int = Field(..., ge=0, description="Group ID (0 = all groups)")
    text: str = Field(..., min_length=1, max_length=500, description="Comment text")


# ── Endpoints ────────────────────────────────────────────────


@router.get(
    "/api/schedule/comments",
    tags=["Schedule"],
    summary="List schedule comments",
    description="Return day-level schedule comments. All authenticated users can read.",
)
def list_schedule_comments(
    group_id: int | None = Query(None, description="Filter by group ID"),
    from_date: str | None = Query(None, alias="from", description="Start date YYYY-MM-DD"),
    to_date: str | None = Query(None, alias="to", description="End date YYYY-MM-DD"),
    _user: dict = Depends(require_auth),
):
    try:
        return get_db().get_schedule_comments(
            group_id=group_id,
            date_from=from_date,
            date_to=to_date,
        )
    except Exception as exc:
        raise _sanitize_500(exc)


@router.post(
    "/api/schedule/comments",
    tags=["Schedule"],
    summary="Create schedule comment",
    description="Create (or replace) a day-level comment for a group. Requires Planer role.",
    status_code=201,
)
def create_schedule_comment(
    body: ScheduleCommentCreate,
    cur_user: dict = Depends(require_planer),
):
    # Validate date
    try:
        from datetime import datetime
        datetime.strptime(body.date, "%Y-%m-%d")
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid date format, expected YYYY-MM-DD")

    author = cur_user.get("NAME", "")
    try:
        comment = get_db().add_schedule_comment(
            date=body.date,
            group_id=body.group_id,
            text=body.text,
            author=author,
        )
        return comment
    except Exception as exc:
        raise _sanitize_500(exc)


@router.delete(
    "/api/schedule/comments/{comment_id}",
    tags=["Schedule"],
    summary="Delete schedule comment",
    description="Delete a day-level comment by ID. Requires Planer role.",
)
def delete_schedule_comment(
    comment_id: int,
    _cur_user: dict = Depends(require_planer),
):
    try:
        deleted = get_db().delete_schedule_comment(comment_id)
        if deleted == 0:
            raise HTTPException(status_code=404, detail="Comment not found")
        return {"ok": True, "deleted": deleted}
    except HTTPException:
        raise
    except Exception as exc:
        raise _sanitize_500(exc)
