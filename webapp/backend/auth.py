"""
Accounts and role-scoped access for the A-OPDT web application.

Two roles exist because the twin serves two audiences with genuinely different
needs, not merely different layouts. A researcher wants every measured field,
the estimator's own uncertainty, and which stress rules fired. A farmer wants to
know whether the crop is in trouble and what to do about it. Mixing the two
produces an interface that serves neither, so the API returns different payloads
per role rather than one payload the client filters.

Users live in MongoDB, which docker-compose already provides for the twin.
"""
from __future__ import annotations

import os
import uuid
from datetime import datetime, timedelta, timezone
from typing import Literal, Optional

import bcrypt
from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from jose import JWTError, jwt
from pydantic import BaseModel, EmailStr, Field
from pymongo import MongoClient
from pymongo.errors import OperationFailure, PyMongoError

Role = Literal["researcher", "farmer"]

def _mongo_uri() -> str:
    """Where accounts live.

    Falls back to the twin's own configured database rather than a bare
    localhost default. Without this, auth silently ignored A_OPDT_ENV_FILE: the
    twin ran against Atlas while accounts looked for an unauthenticated
    localhost, and the failure surfaced as "database unreachable" when the
    database was running and merely wanted credentials.

    Resolved at connect time, not import time. app.py loads the env file after
    importing this module, so reading it at import would capture the value from
    before the file was loaded - the same ordering trap this is meant to fix.
    """
    return (
        os.getenv("AOPDT_MONGO_URI")
        or os.getenv("DT_MONGO__URI")
        or "mongodb://localhost:27017"
    )
MONGO_DB = os.getenv("AOPDT_MONGO_DB", "aopdt")

# A generated fallback keeps a fresh checkout working, but it rotates on every
# restart, which invalidates issued tokens. Set AOPDT_SECRET_KEY in any
# deployment that should survive a restart.
SECRET_KEY = os.getenv("AOPDT_SECRET_KEY") or uuid.uuid4().hex
ALGORITHM = "HS256"
TOKEN_TTL_HOURS = 12

# bcrypt hashes at most 72 bytes and raises above that rather than truncating,
# so the limit is enforced here where it can be reported to the user.
MAX_PASSWORD_BYTES = 72

# Connect lazily. Touching MongoDB at import time meant the whole API failed to
# load when the database was merely not started yet, which is the normal state
# of a fresh checkout. Now the app starts and only account operations fail, with
# a message that says so.
_client: Optional[MongoClient] = None
_index_ready = False


def _users_collection():
    global _client, _index_ready
    if _client is None:
        _client = MongoClient(_mongo_uri(), serverSelectionTimeoutMS=5000)
    collection = _client[MONGO_DB].users
    if not _index_ready:
        try:
            collection.create_index("email", unique=True)
            _index_ready = True
        except PyMongoError:
            pass          # retried on the next call; surfaced by the operation itself
    return collection

_bearer = HTTPBearer(auto_error=False)


class RegisterRequest(BaseModel):
    email: EmailStr
    password: str = Field(min_length=8)
    name: str = Field(min_length=1, max_length=120)
    role: Role
    plot_name: Optional[str] = Field(default=None, max_length=120)


class LoginRequest(BaseModel):
    email: EmailStr
    password: str


class UserOut(BaseModel):
    user_id: str
    email: EmailStr
    name: str
    role: Role
    plot_name: Optional[str] = None


class AuthResponse(BaseModel):
    token: str
    user: UserOut


def _mongo_unavailable(exc: PyMongoError) -> HTTPException:
    """Turn a driver error into a 503 that names the actual problem.

    An unreachable server and a server refusing the credentials both surface as
    PyMongoError, and reporting the second as the first sends you to restart a
    container that was running the whole time.
    """
    if isinstance(exc, OperationFailure):
        return HTTPException(
            status.HTTP_503_SERVICE_UNAVAILABLE,
            "Account database rejected our credentials. Set AOPDT_MONGO_URI (or "
            "DT_MONGO__URI) to a URI that includes a username and password.",
        )
    return HTTPException(
        status.HTTP_503_SERVICE_UNAVAILABLE,
        "Account database is unreachable. Start it with "
        "`docker compose up -d mongodb` from the a-opdt directory.",
    )


def _find_one(collection, query: dict) -> Optional[dict]:
    """Query, turning a database problem into a clear 503 for the client."""
    try:
        return collection.find_one(query)
    except PyMongoError as exc:
        raise _mongo_unavailable(exc) from exc


def _hash(password: str) -> str:
    raw = password.encode("utf-8")
    if len(raw) > MAX_PASSWORD_BYTES:
        raise HTTPException(400, f"Password must be at most {MAX_PASSWORD_BYTES} bytes")
    return bcrypt.hashpw(raw, bcrypt.gensalt()).decode("utf-8")


def _verify(password: str, hashed: str) -> bool:
    raw = password.encode("utf-8")
    if len(raw) > MAX_PASSWORD_BYTES:
        return False
    try:
        return bcrypt.checkpw(raw, hashed.encode("utf-8"))
    except ValueError:
        return False


def _issue_token(user_id: str, role: str) -> str:
    now = datetime.now(timezone.utc)
    return jwt.encode(
        {"sub": user_id, "role": role,
         "iat": now, "exp": now + timedelta(hours=TOKEN_TTL_HOURS)},
        SECRET_KEY, algorithm=ALGORITHM,
    )


def _as_user_out(doc: dict) -> UserOut:
    return UserOut(user_id=doc["user_id"], email=doc["email"], name=doc["name"],
                   role=doc["role"], plot_name=doc.get("plot_name"))


def register(req: RegisterRequest) -> AuthResponse:
    users = _users_collection()
    if _find_one(users, {"email": req.email.lower()}):
        raise HTTPException(409, "An account with that email already exists")

    doc = {
        "user_id": uuid.uuid4().hex,
        "email": req.email.lower(),
        "name": req.name.strip(),
        "role": req.role,
        "plot_name": (req.plot_name or "").strip() or None,
        "password_hash": _hash(req.password),
        "created_at": datetime.now(timezone.utc),
    }
    try:
        users.insert_one(doc)
    except PyMongoError as exc:
        raise _mongo_unavailable(exc) from exc
    # Registration returns a token so the client lands on the dashboard rather
    # than bouncing the user to a login form they just filled in.
    return AuthResponse(token=_issue_token(doc["user_id"], doc["role"]),
                        user=_as_user_out(doc))


def login(req: LoginRequest) -> AuthResponse:
    doc = _find_one(_users_collection(), {"email": req.email.lower()})
    # One message for both cases, so the response cannot be used to discover
    # which email addresses have accounts.
    if not doc or not _verify(req.password, doc["password_hash"]):
        raise HTTPException(401, "Incorrect email or password")
    return AuthResponse(token=_issue_token(doc["user_id"], doc["role"]),
                        user=_as_user_out(doc))


def current_user(
    creds: Optional[HTTPAuthorizationCredentials] = Depends(_bearer),
) -> UserOut:
    if creds is None:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Not authenticated")
    try:
        payload = jwt.decode(creds.credentials, SECRET_KEY, algorithms=[ALGORITHM])
    except JWTError:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Invalid or expired token")

    doc = _find_one(_users_collection(), {"user_id": payload.get("sub")})
    if not doc:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Account no longer exists")
    return _as_user_out(doc)


def require_role(*allowed: str):
    """Guard a route so only the listed roles reach it."""
    def dependency(user: UserOut = Depends(current_user)) -> UserOut:
        if user.role not in allowed:
            raise HTTPException(status.HTTP_403_FORBIDDEN,
                                f"This view is for {' or '.join(allowed)} accounts")
        return user
    return dependency
