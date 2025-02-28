
import os
import jwt  # pyjwt
from fastapi import Depends, HTTPException, status
from fastapi.security import OAuth2PasswordBearer

oauth2_scheme = OAuth2PasswordBearer(tokenUrl="auth/login")

SECRET_KEY = os.getenv("JWT_SECRET_KEY", "some-secret")

class UserTokenData:
    """
    A simple class or pydantic model to represent the user data we store in the token.
    """
    def __init__(self, user_id: int, name: str, email: str):
        self.id = user_id
        self.name = name
        self.email = email

def get_current_user(token: str = Depends(oauth2_scheme)) -> UserTokenData:
    """
    Decodes the JWT token from the 'Authorization: Bearer <token>' header
    and returns user info (id, name, email).
    """
    try:
        payload = jwt.decode(token, SECRET_KEY, algorithms=["HS256"])
        user_id: int = payload.get("user_id")
        name: str = payload.get("name")
        email: str = payload.get("email")

        if user_id is None or name is None or email is None:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Invalid token",
            )
        return UserTokenData(user_id, name, email)

    except jwt.ExpiredSignatureError:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Token has expired"
        )
    except jwt.PyJWTError:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid token"
        )

