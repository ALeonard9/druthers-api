"""
This module contains the API routes for user-related operations.
"""

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from app.auth.oauth2 import get_current_session_user, get_current_user
from app.config import get_settings
from app.db import db_user
from app.db.database import get_db
from app.schemas.model_schemas import InUserBase, OutResponseUserModel

router = APIRouter(
    tags=['users'],
)


# Read all users
@router.get('', response_model=OutResponseUserModel)
def get_all_users(
    db: Session = Depends(get_db), current_user: InUserBase = Depends(get_current_user)
):
    """
    Retrieve all users from the database. Must be an admin to view all users.

    Args:
        db (Session): The database session.

    Returns:
        List: OutUserDisplay: A list of users.
    """
    if current_user[0].user_group == 'admin':
        users = db_user.get_all_users(db)
        return OutResponseUserModel(data=users, message='Users found')

    raise HTTPException(
        status_code=403,
        detail='User does not have permission to view all users.',
    )


# Read user
@router.get('/{uuid}', response_model=OutResponseUserModel)
def get_user(
    uuid: str,
    db: Session = Depends(get_db),
    current_user: InUserBase = Depends(get_current_user),
):
    """
    Retrieve a user by ID from the database.
    Admins can view all users, while users can only view their own account.

    Args:
        uuid (str): The ID of the user.
        db (Session): The database session.

    Returns:
        List: OutUserDisplay: The user data.
    """
    if current_user[0].user_group == 'admin':
        user = db_user.get_user(db, uuid)
        return OutResponseUserModel(data=user, message='User found')

    if current_user[0].id == uuid:
        user = db_user.get_user(db, uuid)
        return OutResponseUserModel(data=user, message='User found')
    raise HTTPException(
        status_code=403,
        detail='User can only view their own account.',
    )


# Create user
@router.post('', response_model=OutResponseUserModel, status_code=201)
def create_user(request: InUserBase, db: Session = Depends(get_db)):
    """
    Create a new user in the database.

    Args:
        request (InUserBase): Must include email,
        display_name (max length 16 characters), and password.
        db (Session): The database session.

    Returns:
        List: OutUserDisplay: The newly created user data.
    """
    if get_settings().disable_signup:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=(
                'New account registration is closed. This app is '
                'invite-only - contact the administrator for access.'
            ),
        )
    user = db_user.create_user(db, request)
    return OutResponseUserModel(data=user, message='User created')


# Update user
@router.put('/{uuid}', response_model=OutResponseUserModel)
def update_user(
    uuid: str,
    request: InUserBase,
    db: Session = Depends(get_db),
    current_user: InUserBase = Depends(get_current_user),
):
    """
    Update a user's information in the database.
    Admins can update any user, while users can only update their own account.

    Args:
        uuid (str): The ID of the user.
        request (InUserBase): The updated user data.
        db (Session): The database session.

    Returns:
        List: OutUserDisplay: The updated user data.
    """
    if current_user[0].user_group == 'admin':
        user = db_user.update_user(db, uuid, request)
        return OutResponseUserModel(data=user, message='User updated')

    if current_user[0].id == uuid:
        user = db_user.update_user(db, uuid, request)
        return OutResponseUserModel(data=user, message='User updated')
    raise HTTPException(
        status_code=403,
        detail='User can only update their own account.',
    )


# Delete user
@router.delete('/{uuid}', response_model=OutResponseUserModel)
def delete_user(
    uuid: str,
    db: Session = Depends(get_db),
    current_user: list = Depends(get_current_session_user),
):
    """
    Delete a user by ID from the database.
    Admins can delete any user, while users can only delete their own account.

    Args:
        uuid (str): The ID of the user.
        db (Session): The database session.
        current_user (dict): The current authenticated user.

    Returns:
        List: OutUserDisplay: The deleted user data.
    """
    # Check if current user is admin
    if current_user[0].user_group == 'admin':
        user = db_user.delete_user(db, uuid)
        return OutResponseUserModel(data=user, message='User deleted')

    if current_user[0].id == uuid:
        user = db_user.delete_user(db, uuid)
        return OutResponseUserModel(data=user, message='User deleted')
    raise HTTPException(
        status_code=403,
        detail='User can only delete their own account.',
    )
