import secrets
from datetime import UTC, datetime

from argon2 import PasswordHasher
from itsdangerous import BadSignature, SignatureExpired, URLSafeTimedSerializer

from backend.app.config import get_settings

settings = get_settings()
hasher = PasswordHasher()
serializer = URLSafeTimedSerializer(settings.app_secret_key, salt="money-extra-session")


def hash_password(password: str) -> str:
    return hasher.hash(password)


def verify_password(password_hash: str, password: str) -> bool:
    try:
        return hasher.verify(password_hash, password)
    except Exception:
        return False


def create_session(username: str) -> str:
    return serializer.dumps({"sub": username, "iat": datetime.now(UTC).isoformat()})


def read_session(token: str, max_age: int = 12 * 3600) -> str | None:
    try:
        payload = serializer.loads(token, max_age=max_age)
        return str(payload["sub"])
    except (BadSignature, SignatureExpired, KeyError):
        return None


def new_csrf_token() -> str:
    return secrets.token_urlsafe(32)
