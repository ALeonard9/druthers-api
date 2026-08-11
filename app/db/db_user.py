"""
This module contains database operations for user-related actions.
"""

import os

from email_validator import EmailNotValidError, validate_email
from fastapi import HTTPException, status
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session
from sqlalchemy import or_

from app.db.hash import Hash
from app.db.models import DbUser
from app.log.logging_config import logger
from app.schemas.model_schemas import InUserBase


def create_user(db: Session, request: InUserBase) -> list[DbUser]:
    """
    Create a new user in the database.

    Args:
        db (Session): The database session.
        request (InUserBase): The user data.

    Returns:
        List[DbUser]: The newly created user.
    """
    try:
        logger.info('Validating email address: %s', request.email)
        validate_email(request.email)
    except EmailNotValidError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail='Invalid email address',
        ) from exc
    existing_user = db.query(DbUser).filter(DbUser.email == request.email).first()
    if existing_user:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail='Email already registered',
        )
    new_user = DbUser(
        display_name=request.display_name,
        email=request.email,
        password=Hash.hash_password(request.password),
    )
    try:
        db.add(new_user)
        db.commit()
        # Refresh to obtain newly created ID
        db.refresh(new_user)
        logger.info(
            'User created: %s, display_name: %s, email: %s',
            new_user.id,
            new_user.display_name,
            new_user.email,
        )
    except IntegrityError as exc:  # pragma: no cover
        db.rollback()
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail='Error creating user',
        ) from exc
    response = []
    response.append(new_user)
    return response


def create_admin_user(db: Session) -> list[DbUser]:
    """
    Create an admin user in the database.

    Args:
        db (Session): The database session.

    Returns:
        List[DbUser]: The newly created admin user or existing admin user.
    """
    admin_display_name = os.getenv('ADMIN_DISPLAY_NAME')
    admin_email = os.getenv('ADMIN_EMAIL')
    admin_password = os.getenv('ADMIN_PASSWORD')

    if not all([admin_display_name, admin_email, admin_password]):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail='Environment variables not set for admin creation',
        )
    try:
        logger.info('Validating email address: %s', admin_email)
        validate_email(admin_email)
    except EmailNotValidError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail='Invalid email address',
        ) from exc

    existing_user = db.query(DbUser).filter(DbUser.email == admin_email).first()
    if existing_user:
        logger.info('Admin user already exists with email: %s', admin_email)
        response = []
        response.append(existing_user)
        return response

    new_user = DbUser(
        display_name=admin_display_name,
        email=admin_email,
        user_group='admin',
        password=Hash.hash_password(admin_password),
    )
    try:
        db.add(new_user)
        db.commit()
        # Refresh to obtain newly created ID
        db.refresh(new_user)
        logger.info('Admin created: %s', new_user.id)
    except IntegrityError as exc:  # pragma: no cover
        db.rollback()
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail='Error creating admin',
        ) from exc
    response = []
    response.append(new_user)
    return response


def get_all_users(db: Session) -> list[DbUser]:
    """
    Retrieve all users from the database.

    Args:
        db (Session): The database session.

    Returns:
        list[DbUser]: A list of all users.
    """
    return db.query(DbUser).all()


def get_user(db: Session, user_id: str):
    """
    Retrieve a user by ID from the database.

    Args:
        db (Session): The database session.
        user_id (str): The ID of the user.

    Returns:
        DbUser: The user data.
    """
    user = db.query(DbUser).filter(DbUser.id == user_id).first()
    if user is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"User with id {user_id} not found",
        )
    response = []
    response.append(user)
    return response


def update_user(db: Session, user_id: str, request: InUserBase) -> list[DbUser]:
    """
    Update a user's information in the database.

    Args:
        db (Session): The database session.
        user_id (str): The ID of the user.
        request (InUserBase): The updated user data.

    Returns:
        DbUser: The updated user data.
    """
    user = db.query(DbUser).filter(DbUser.id == user_id).first()
    if user is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"User with id {user_id} not found",
        )
    if request.email != user.email:
        existing_user = db.query(DbUser).filter(DbUser.email == request.email).first()
        if existing_user:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail='Email already registered',
            )
    try:
        logger.info('Validating email address: %s', request.email)
        validate_email(request.email)
    except EmailNotValidError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail='Invalid email address',
        ) from exc
    user.display_name = request.display_name
    user.email = request.email
    user.password = Hash.hash_password(request.password)
    db.commit()
    db.refresh(user)
    logger.info(
        'User updated: %s, display_name: %s, email: %s',
        user.id,
        user.display_name,
        user.email,
    )
    response = []
    response.append(user)
    return response


def delete_user(db: Session, user_id: str) -> list[DbUser]:
    """
    Delete a user by ID from the database.

    Args:
        db (Session): The database session.
        user_id (str): The ID of the user.

    Returns:
        DbUser: The deleted user data.
    """
    user = db.query(DbUser).filter(DbUser.id == user_id).first()
    if user is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"User with id {user_id} not found",
        )

    db.delete(user)
    db.commit()
    logger.info(
        'User deleted: %s, display_name: %s, email: %s',
        user.id,
        user.display_name,
        user.email,
    )
    response = []
    response.append(user)
    return response


def search_users(
    db: Session, current_user_pk: int, query: str, limit: int = 20
) -> list[DbUser]:
    """
    Search users by handle or display name, case-insensitive.
    Returns only public profiles and accepted friends.
    """
    if not query:
        return []

    # pylint: disable=import-outside-toplevel
    from app.db.db_friendship import friend_pks
    from app.services.visibility import VisibilityTier

    f_pks = friend_pks(db, current_user_pk)
    q_str = f"%{query}%"

    return (
        db.query(DbUser)
        .filter(
            or_(
                DbUser.display_name.ilike(q_str),
                DbUser.handle.ilike(q_str),
            ),
            or_(
                DbUser.visibility_profile == VisibilityTier.PUBLIC.value,
                DbUser.pk.in_(f_pks),
            ),
        )
        .limit(limit)
        .all()
    )
