# pylint: disable=missing-function-docstring
"""
The home summary endpoint - one bounded call behind the landing page.
"""

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from app.auth.oauth2 import get_current_user
from app.db.database import get_db
from app.schemas.model_schemas import OutSummary
from app.services.summary import TOP_N, build_summary

router = APIRouter(prefix='/v1', tags=['Summary'])


@router.get('/users/me/summary', response_model=OutSummary)
def get_summary(
    db: Session = Depends(get_db),
    current_user: list = Depends(get_current_user),
    top: int = Query(TOP_N, ge=1, le=TOP_N, description='Entries per shelf'),
):
    """
    Per-shelf Top 5 plus ranked/queued counts for the signed-in user.

    Replaces the home page's four full-collection fetches; row count is
    independent of library size.
    """
    return build_summary(db, current_user[0], top_n=top)
