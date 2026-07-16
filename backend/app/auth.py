"""Database-backed authentication.

- Users live in the `users` table; passwords are scrypt-hashed with a per-user
  salt (stdlib hashlib — no new dependency).
- A login issues a random opaque token stored in `auth_sessions`; the browser
  holds it in an HttpOnly cookie. Auth state is entirely in the DB, so it
  survives restarts and can be revoked by deleting the row.
- First run has no users: `register` is allowed only until the first owner
  account exists, then it is closed (login-only).
"""
import hashlib
import hmac
import secrets
import uuid
from datetime import datetime, timedelta, timezone

from sqlalchemy.orm import Session

from app import db

COOKIE_NAME = "adstudio_session"
SESSION_TTL = timedelta(days=30)
_SCRYPT = dict(n=2**14, r=8, p=1, dklen=32)


def _now() -> datetime:
    return datetime.now(timezone.utc)


def hash_password(password: str, salt: bytes | None = None) -> tuple[str, str]:
    salt = salt or secrets.token_bytes(16)
    dk = hashlib.scrypt(password.encode(), salt=salt, **_SCRYPT)
    return salt.hex(), dk.hex()


def verify_password(password: str, salt_hex: str, hash_hex: str) -> bool:
    _, dk = hash_password(password, bytes.fromhex(salt_hex))
    return hmac.compare_digest(dk, hash_hex)


def user_count(session: Session) -> int:
    return session.query(db.UserRow).count()


def create_user(session: Session, email: str, password: str) -> db.UserRow:
    email = email.strip().lower()
    salt, pw_hash = hash_password(password)
    user = db.UserRow(user_id=f"usr_{uuid.uuid4().hex[:12]}", email=email,
                      salt=salt, password_hash=pw_hash)
    session.add(user)
    session.commit()
    return user


def authenticate(session: Session, email: str, password: str) -> db.UserRow | None:
    user = session.query(db.UserRow).filter_by(email=email.strip().lower()).first()
    if user and verify_password(password, user.salt, user.password_hash):
        return user
    return None


def create_session(session: Session, user_id: str) -> str:
    token = secrets.token_urlsafe(32)
    session.add(db.SessionRow(token=token, user_id=user_id,
                              expires_at=_now() + SESSION_TTL))
    session.commit()
    return token


def user_for_token(session: Session, token: str | None) -> db.UserRow | None:
    if not token:
        return None
    row = session.get(db.SessionRow, token)
    if row is None:
        return None
    expires = row.expires_at
    if expires.tzinfo is None:                # sqlite returns naive datetimes
        expires = expires.replace(tzinfo=timezone.utc)
    if expires < _now():
        session.delete(row)
        session.commit()
        return None
    return session.get(db.UserRow, row.user_id)


def revoke(session: Session, token: str | None) -> None:
    if not token:
        return
    row = session.get(db.SessionRow, token)
    if row is not None:
        session.delete(row)
        session.commit()
