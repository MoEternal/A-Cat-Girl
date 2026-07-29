from __future__ import annotations

import base64
import hashlib
import hmac
import secrets
from datetime import timedelta

from fastapi import APIRouter, HTTPException, Request, Response, status
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field, field_validator
from sqlalchemy import delete, select
from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint

from .database import AdminAccount, AdminSession, Database, utcnow


SESSION_COOKIE = "catgirl_admin_session"
SESSION_MAX_AGE_SECONDS = 30 * 24 * 60 * 60
PASSWORD_ITERATIONS = 600_000


class Credentials(BaseModel):
    username: str = Field(min_length=1, max_length=80)
    password: str = Field(min_length=8, max_length=256)

    @field_validator("username")
    @classmethod
    def normalize_username(cls, value: str) -> str:
        username = value.strip()
        if not username:
            raise ValueError("用户名不能为空")
        return username


class AuthStatus(BaseModel):
    setup_required: bool
    authenticated: bool
    username: str = ""


def _encode(value: bytes) -> str:
    return base64.urlsafe_b64encode(value).decode("ascii").rstrip("=")


def _decode(value: str) -> bytes:
    return base64.urlsafe_b64decode(value + "=" * (-len(value) % 4))


def _password_hash(password: str, salt: bytes) -> bytes:
    return hashlib.pbkdf2_hmac(
        "sha256",
        password.encode("utf-8"),
        salt,
        PASSWORD_ITERATIONS,
    )


def _token_hash(token: str) -> str:
    return hashlib.sha256(token.encode("ascii")).hexdigest()


class AuthManager:
    def __init__(self, database: Database):
        self.database = database

    def is_configured(self) -> bool:
        with self.database.session_factory() as session:
            return session.get(AdminAccount, 1) is not None

    def authenticate(self, token: str | None) -> str | None:
        if not token:
            return None
        with self.database.session_factory() as session:
            stored = session.get(AdminSession, _token_hash(token))
            if stored is None:
                return None
            if stored.expires_at <= utcnow():
                session.delete(stored)
                session.commit()
                return None
            account = session.get(AdminAccount, 1)
            return account.username if account is not None else None

    def create_account(self, credentials: Credentials) -> tuple[str, str]:
        salt = secrets.token_bytes(16)
        with self.database.session_factory() as session:
            if session.get(AdminAccount, 1) is not None:
                raise HTTPException(status.HTTP_409_CONFLICT, "管理员账号已经创建")
            account = AdminAccount(
                id=1,
                username=credentials.username,
                password_salt=_encode(salt),
                password_hash=_encode(_password_hash(credentials.password, salt)),
            )
            session.add(account)
            token = self._create_session(session)
            session.commit()
            return account.username, token

    def login(self, credentials: Credentials) -> tuple[str, str]:
        with self.database.session_factory() as session:
            account = session.get(AdminAccount, 1)
            if account is None:
                raise HTTPException(status.HTTP_409_CONFLICT, "请先创建管理员账号")
            try:
                expected = _decode(account.password_hash)
                actual = _password_hash(credentials.password, _decode(account.password_salt))
            except (ValueError, TypeError):
                expected = b""
                actual = b"invalid"
            username_matches = hmac.compare_digest(
                account.username.encode("utf-8"),
                credentials.username.encode("utf-8"),
            )
            if not username_matches or not hmac.compare_digest(expected, actual):
                raise HTTPException(status.HTTP_401_UNAUTHORIZED, "用户名或密码错误")
            token = self._create_session(session)
            session.commit()
            return account.username, token

    def logout(self, token: str | None) -> None:
        if not token:
            return
        with self.database.session_factory() as session:
            session.execute(delete(AdminSession).where(AdminSession.token_hash == _token_hash(token)))
            session.commit()

    @staticmethod
    def _create_session(session) -> str:
        session.execute(delete(AdminSession).where(AdminSession.expires_at <= utcnow()))
        token = secrets.token_urlsafe(32)
        session.add(
            AdminSession(
                token_hash=_token_hash(token),
                expires_at=utcnow() + timedelta(seconds=SESSION_MAX_AGE_SECONDS),
            )
        )
        return token


class ManagementAuthMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next: RequestResponseEndpoint) -> Response:
        path = request.url.path
        if (
            request.method == "OPTIONS"
            or not path.startswith("/api/")
            or path.startswith("/api/auth/")
        ):
            return await call_next(request)

        manager: AuthManager = request.app.state.auth_manager
        if not manager.is_configured() and not request.app.state.allow_unconfigured_management:
            return JSONResponse(
                status_code=status.HTTP_428_PRECONDITION_REQUIRED,
                content={"detail": "请先创建管理员账号"},
            )
        if not manager.is_configured():
            return await call_next(request)
        username = manager.authenticate(request.cookies.get(SESSION_COOKIE))
        if username is None:
            return JSONResponse(
                status_code=status.HTTP_401_UNAUTHORIZED,
                content={"detail": "登录已失效，请重新登录"},
            )
        request.state.admin_username = username
        return await call_next(request)


router = APIRouter(prefix="/api/auth", tags=["auth"])


def _manager(request: Request) -> AuthManager:
    return request.app.state.auth_manager


def _secure_cookie(request: Request) -> bool:
    forwarded = request.headers.get("x-forwarded-proto", "").split(",", 1)[0].strip().lower()
    return request.url.scheme == "https" or forwarded == "https"


def _set_session_cookie(response: Response, request: Request, token: str) -> None:
    response.set_cookie(
        SESSION_COOKIE,
        token,
        max_age=SESSION_MAX_AGE_SECONDS,
        httponly=True,
        secure=_secure_cookie(request),
        samesite="strict",
        path="/",
    )


@router.get("/status", response_model=AuthStatus)
def auth_status(request: Request) -> AuthStatus:
    manager = _manager(request)
    configured = manager.is_configured()
    username = manager.authenticate(request.cookies.get(SESSION_COOKIE)) if configured else None
    return AuthStatus(
        setup_required=not configured,
        authenticated=username is not None,
        username=username or "",
    )


@router.post("/setup", response_model=AuthStatus, status_code=status.HTTP_201_CREATED)
def setup(credentials: Credentials, request: Request, response: Response) -> AuthStatus:
    username, token = _manager(request).create_account(credentials)
    _set_session_cookie(response, request, token)
    return AuthStatus(setup_required=False, authenticated=True, username=username)


@router.post("/login", response_model=AuthStatus)
def login(credentials: Credentials, request: Request, response: Response) -> AuthStatus:
    username, token = _manager(request).login(credentials)
    _set_session_cookie(response, request, token)
    return AuthStatus(setup_required=False, authenticated=True, username=username)


@router.post("/logout", status_code=status.HTTP_204_NO_CONTENT)
def logout(request: Request) -> Response:
    _manager(request).logout(request.cookies.get(SESSION_COOKIE))
    response = Response(status_code=status.HTTP_204_NO_CONTENT)
    response.delete_cookie(
        SESSION_COOKIE,
        path="/",
        secure=_secure_cookie(request),
        httponly=True,
        samesite="strict",
    )
    return response
