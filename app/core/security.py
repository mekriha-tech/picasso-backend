import secrets
import hashlib
from datetime import datetime, timedelta, timezone
from passlib.context import CryptContext
from jose import jwt
from app.core.config import settings

# Argon2id password hashing exactly as requested in PRD
pwd_context = CryptContext(schemes=["argon2"], deprecated="auto")

def get_password_hash(password: str) -> str:
    return pwd_context.hash(password)

def create_access_token(subject: str) -> str:
    # Short-lived access token (15 min)
    expire = datetime.now(timezone.utc) + timedelta(minutes=settings.ACCESS_TOKEN_EXPIRE_MINUTES)
    to_encode = {"exp": expire, "sub": str(subject), "type": "access"}
    return jwt.encode(to_encode, settings.SECRET_KEY, algorithm="HS256")

# --- NEW OPAQUE TOKEN LOGIC ---

def generate_opaque_token() -> str:
    """Generates a 32-byte url-safe random string per PRD 3.9."""
    return secrets.token_urlsafe(32)

def hash_token(token: str) -> str:
    """Returns the SHA-256 hash of the token for database storage."""
    return hashlib.sha256(token.encode()).hexdigest()