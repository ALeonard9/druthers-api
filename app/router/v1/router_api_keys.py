# pylint: disable=missing-function-docstring
"""
API-key management: long-lived credentials for programmatic access - the MCP
server running locally, Proxmox crons, scripts. Keys authenticate through the
normal ``Authorization: Bearer`` header (see app/auth/oauth2.py).
"""

from typing import List

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from app.auth import oauth2
from app.auth.oauth2 import get_current_user
from app.db.database import get_db
from app.db.models import DbApiKey
from app.schemas.model_schemas import (
    InApiKeyCreate,
    OutApiKey,
    OutApiKeyCreated,
)

router = APIRouter(prefix='/v1/users/me/api-keys', tags=['API keys'])


@router.post('', response_model=OutApiKeyCreated, status_code=status.HTTP_201_CREATED)
def create_api_key(
    body: InApiKeyCreate,
    db: Session = Depends(get_db),
    current_user: list = Depends(get_current_user),
):
    """
    Mint a new key. The plaintext secret is in this response and nowhere
    else - it is never stored or shown again.
    """
    secret = oauth2.generate_api_key()
    row = DbApiKey(
        user_id=current_user[0].pk,
        name=body.name.strip(),
        key_hash=oauth2.hash_api_key(secret),
        prefix=secret[:12],
    )
    db.add(row)
    db.commit()
    db.refresh(row)
    return OutApiKeyCreated(
        id=row.id,
        name=row.name,
        prefix=row.prefix,
        created_at=row.created_at,
        key=secret,
    )


@router.get('', response_model=List[OutApiKey])
def list_api_keys(
    db: Session = Depends(get_db),
    current_user: list = Depends(get_current_user),
):
    return (
        db.query(DbApiKey)
        .filter(DbApiKey.user_id == current_user[0].pk)
        .order_by(DbApiKey.created_at)
        .all()
    )


@router.delete('/{key_id}', status_code=status.HTTP_204_NO_CONTENT)
def revoke_api_key(
    key_id: str,
    db: Session = Depends(get_db),
    current_user: list = Depends(get_current_user),
):
    """Revocation is deletion - the hash is gone, the key can never auth again."""
    row = (
        db.query(DbApiKey)
        .filter(DbApiKey.id == key_id, DbApiKey.user_id == current_user[0].pk)
        .first()
    )
    if row is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail='API key not found'
        )
    db.delete(row)
    db.commit()
