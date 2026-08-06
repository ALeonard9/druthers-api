"""
This module provides hashing utilities for passwords using Argon2.
"""

from pwdlib import PasswordHash
from pwdlib.hashers.argon2 import Argon2Hasher

from app.config import get_settings


def _get_pwd_cxt() -> PasswordHash:
    params = get_settings().argon2_params
    return PasswordHash((Argon2Hasher(**params),))


pwd_cxt = _get_pwd_cxt()


class Hash:
    """
    A class that provides methods for hashing and verifying passwords.
    """

    @staticmethod
    def hash_password(password: str) -> str:
        """
        Hash a password using Argon2.

        Args:
            password (str): The plain password to hash.

        Returns:
            str: The hashed password.
        """
        return pwd_cxt.hash(password)

    @staticmethod
    def verify(hashed_password: str, plain_password: str) -> bool:
        """
        Verify a hashed password against a plain password.

        Args:
            hashed_password (str): The hashed password.
            plain_password (str): The plain password to verify.

        Returns:
            bool: True if the passwords match, False otherwise.
        """
        return pwd_cxt.verify(plain_password, hashed_password)
