from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.security import OAuth2PasswordRequestForm
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select  # Import select
from app.config import SessionLocal
from auth.models import User
from auth.schemas import UserCreate, UserLogin, Token
from auth.utils import hash_password, verify_password, create_access_token
from datetime import timedelta

router = APIRouter()

# Dependency to get the database session
async def get_db() -> AsyncSession:
    async with SessionLocal() as session:
        yield session

# Signup route
@router.post("/signup", response_model=Token)
async def signup(user: UserCreate, db: AsyncSession = Depends(get_db)):
    # Check if user already exists
    result = await db.execute(select(User).where(User.email == user.email))
    existing_user = result.scalars().first()
    if existing_user:
        raise HTTPException(status_code=400, detail="Email already registered")

    # Hash password and create user
    hashed_password = hash_password(user.password)
    new_user = User(email=user.email, hashed_password=hashed_password)
    db.add(new_user)
    await db.commit()
    await db.refresh(new_user)

    # Generate token
    access_token = create_access_token(data={"sub": user.email})
    return {"message":"Signup Successful", "access_token": access_token, "token_type": "bearer"}

# Login route
@router.post("/login", response_model=Token)
async def login(form_data: OAuth2PasswordRequestForm = Depends(), db: AsyncSession = Depends(get_db)):
    # Get user from database
    result = await db.execute(select(User).where(User.email == form_data.username))
    user = result.scalars().first()
    if not user or not verify_password(form_data.password, user.hashed_password):
        raise HTTPException(status_code=400, detail="Incorrect email or password")

    # Generate token
    access_token = create_access_token(data={"sub": user.email})
    return {"message":"Login Successful", "access_token": access_token, "token_type": "bearer"}