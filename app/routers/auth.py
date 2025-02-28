from fastapi import APIRouter, Form, Request, Depends
from starlette.responses import RedirectResponse
from sqlalchemy.orm import Session
from config.dependencies import get_db
from models.user import User
from utils import hash_password, verify_password, get_form_signupdata, get_form_logindata
from fastapi.templating import Jinja2Templates

router = APIRouter()
templates = Jinja2Templates(directory="templates")

@router.get("/")
def signup_page(request: Request):
    return templates.TemplateResponse("signup.html", {"request": request, "user": None})

@router.post("/signup")
def signup(request: Request, fields: dict = Depends(get_form_signupdata), db = Depends(get_db)):
    hashed_password = hash_password(fields["password"])

    # check if user already exists
    existing_user = db.query(User).filter(User.email == fields["email"]).first()
    if existing_user:
        return templates.TemplateResponse("signup.html", {"request": request, "error": "User already exists"})
    
    new_user = User(
        username=fields["username"],
        email=fields["email"],
        password=hashed_password
    )
    db.add(new_user)
    db.commit()

    return RedirectResponse("/login", status_code=302)


@router.get("/login")
def login_page(request: Request):
    return templates.TemplateResponse("login.html", {"request": request, "user": None})

@router.post("/login")
def login(request: Request, fields: dict = Depends(get_form_logindata), db = Depends(get_db)):
    user = db.query(User).filter(User.email == fields["email"]).first()

    if not user or not verify_password(fields["password"], user.password):
        return templates.TemplateResponse("login.html", {"request": request, "error": "Invalid credentials"})
    
    response = RedirectResponse("/dashboard", status_code=302)
    response.set_cookie(key="user", value=user.email, httponly=True)

    return response    


# @router.get("/dashboard")
# def dashboard(request: Request, db: Session = Depends(get_db)):
#     user_email = request.cookies.get("user")
#     if not user_email:
#         return RedirectResponse("/login")

#     user = db.query(User).filter(User.email == user_email).first()
#     if not user:
#         return RedirectResponse("/login")

#     characters = db.query(Character).all()  # Fetch characters from database

#     return templates.TemplateResponse("dashboard.html", {"request": request, "user": user, "characters": characters})


@router.get("/logout")
def logout(response: RedirectResponse):
    response = RedirectResponse("/login", status_code=302)
    response.delete_cookie("user")
    return response
