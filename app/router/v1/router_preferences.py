# pylint: disable=missing-function-docstring
"""
Display preferences (#122): how many entries of a ranked list to show by
default, and the caller's own time zone. Viewer settings, not sharing ones —
see :mod:`app.services.preferences` for why they live apart from visibility.
"""

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.auth.oauth2 import get_current_user
from app.db.database import get_db
from app.schemas.model_schemas import InPreferencesUpdate, OutPreferences
from app.services import preferences

router = APIRouter(prefix='/v1', tags=['Preferences'])


def _out(user) -> OutPreferences:
    """One place that decides how a stored row reads, so GET and PUT cannot drift."""
    return OutPreferences(
        ranked_list_length=preferences.coerce(user.ranked_list_length),
        onboarding_completed=user.onboarding_completed,
        time_zone=preferences.coerce_time_zone(user.time_zone),
    )


@router.get('/users/me/preferences', response_model=OutPreferences)
def get_preferences(current_user: list = Depends(get_current_user)):
    return _out(current_user[0])


@router.put('/users/me/preferences', response_model=OutPreferences)
def update_preferences(
    request: InPreferencesUpdate,
    db: Session = Depends(get_db),
    current_user: list = Depends(get_current_user),
):
    user = current_user[0]
    data = request.model_dump(exclude_unset=True)
    if 'ranked_list_length' in data and data['ranked_list_length'] is not None:
        user.ranked_list_length = data['ranked_list_length']
    if 'onboarding_completed' in data and data['onboarding_completed'] is not None:
        user.onboarding_completed = data['onboarding_completed']
    if 'time_zone' in data and data['time_zone'] is not None:
        user.time_zone = data['time_zone']

    if data:
        db.commit()
        db.refresh(user)

    return _out(user)
