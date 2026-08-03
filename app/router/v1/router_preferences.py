# pylint: disable=missing-function-docstring
"""
Display preferences (#122): today, just how many entries of a ranked list to
show by default. A viewer setting, not a sharing one — see
:mod:`app.services.preferences` for why it lives apart from visibility.
"""

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.auth.oauth2 import get_current_user
from app.db.database import get_db
from app.schemas.model_schemas import InPreferencesUpdate, OutPreferences
from app.services import preferences

router = APIRouter(prefix='/v1', tags=['Preferences'])


@router.get('/users/me/preferences', response_model=OutPreferences)
def get_preferences(current_user: list = Depends(get_current_user)):
    user = current_user[0]
    return OutPreferences(
        ranked_list_length=preferences.coerce(user.ranked_list_length)
    )


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
        db.commit()
        db.refresh(user)
    return OutPreferences(
        ranked_list_length=preferences.coerce(user.ranked_list_length)
    )
