import secrets

from fastapi import Cookie, Depends, Header, HTTPException, Request, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.app.db import get_session
from backend.app.models import AdminUser
from backend.app.security import read_session


async def current_user(
    money_extra_session: str | None = Cookie(default=None),
    session: AsyncSession = Depends(get_session),
) -> AdminUser:
    username = read_session(money_extra_session) if money_extra_session else None
    if not username:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Not authenticated")
    user = await session.scalar(
        select(AdminUser).where(AdminUser.username == username, AdminUser.active.is_(True))
    )
    if not user:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Not authenticated")
    return user


async def csrf_protected(
    request: Request,
    x_csrf_token: str | None = Header(default=None),
    csrf_cookie: str | None = Cookie(default=None, alias="money_extra_csrf"),
    user: AdminUser = Depends(current_user),
) -> AdminUser:
    if request.method not in {"GET", "HEAD", "OPTIONS"} and (
        not x_csrf_token or not csrf_cookie or not secrets.compare_digest(x_csrf_token, csrf_cookie)
    ):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Invalid CSRF token")
    return user
