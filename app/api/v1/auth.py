from fastapi import APIRouter, Depends, HTTPException, status, Response
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from app.db.session import get_db
from app.models.user import User
from app.schemas.user import UserCreate, RegisterResponse
from app.core.security import get_password_hash, create_access_token, create_refresh_token

router = APIRouter()

@router.post("/register", response_model=RegisterResponse, status_code=status.HTTP_201_CREATED)
async def register_user(user_in: UserCreate, response: Response, db: AsyncSession = Depends(get_db)):
    # 1. Check for existing email
    result = await db.execute(select(User).where(User.email == user_in.email))
    if result.scalars().first():
        raise HTTPException(status_code=400, detail="Email already registered")
    
    # 2. Hash password with Argon2id
    new_user = User(
        email=user_in.email,
        full_name=user_in.full_name,
        password_hash=get_password_hash(user_in.password) 
    )
    
    db.add(new_user)
    await db.commit()
    await db.refresh(new_user)
    
    # 3. Generate tokens
    access_token = create_access_token(subject=str(new_user.id))
    refresh_token = create_refresh_token(subject=str(new_user.id))
    
    # 4. Set httpOnly cookie for refresh token
    response.set_cookie(
        key="refresh_token",
        value=refresh_token,
        httponly=True,
        secure=False,  # Use true for HTTPS in production
        samesite="lax"
    )
    
    return {
        "user": new_user,
        "access_token": access_token
    }