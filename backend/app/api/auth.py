import hashlib

from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
from redis.asyncio import Redis
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from starlette.concurrency import run_in_threadpool

from backend.app.config import get_settings
from backend.app.db import get_session
from backend.app.dependencies import csrf_protected, current_user
from backend.app.models import AdminUser
from backend.app.schemas import LoginRequest, UserResponse
from backend.app.security import create_session, new_csrf_token, verify_password

router = APIRouter(prefix="/auth", tags=["auth"])
settings = get_settings()


@router.post("/login", response_model=UserResponse)
async def login(
    payload: LoginRequest, response: Response, request: Request, session: AsyncSession = Depends(get_session)
) -> UserResponse:
    address = request.client.host if request.client else "local"
    fingerprint = hashlib.sha256(f"{address}:{payload.username.casefold()}".encode()).hexdigest()
    client = Redis.from_url(settings.redis_url, socket_timeout=2, socket_connect_timeout=2)
    try:
        attempts = await client.eval("local n=redis.call('incr',KEYS[1]); if n==1 then redis.call('expire',KEYS[1],300) end; return n", 1, f"auth:attempts:{fingerprint}")
    finally:
        await client.aclose()
    if attempts > 10:
        raise HTTPException(status_code=429, detail="Muitas tentativas. Aguarde cinco minutos.", headers={"Retry-After": "300"})
    user = await session.scalar(select(AdminUser).where(AdminUser.username == payload.username))
    if not user or not user.active or not await run_in_threadpool(verify_password, user.password_hash, payload.password):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid credentials")
    csrf = new_csrf_token()
    response.set_cookie(
        "money_extra_session",
        create_session(user.username),
        httponly=True,
        samesite="strict",
        secure=settings.is_production,
        max_age=12 * 3600,
    )
    response.set_cookie(
        "money_extra_csrf",
        csrf,
        httponly=False,
        samesite="strict",
        secure=settings.is_production,
        max_age=12 * 3600,
    )
    return UserResponse(username=user.username, csrf_token=csrf)


@router.post("/logout", status_code=204)
async def logout(response: Response, _: AdminUser = Depends(csrf_protected)) -> None:
    response.delete_cookie("money_extra_session")
    response.delete_cookie("money_extra_csrf")


@router.get("/me", response_model=UserResponse)
async def me(user: AdminUser = Depends(current_user)) -> UserResponse:
    return UserResponse(username=user.username, csrf_token="cookie")
