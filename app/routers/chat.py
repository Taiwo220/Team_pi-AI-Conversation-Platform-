from fastapi import APIRouter, Form, Request, Depends
from starlette.responses import RedirectResponse
from config.dependencies import get_db
from models.user import ChatHistory, User
from fastapi.templating import Jinja2Templates

router = APIRouter()
templates = Jinja2Templates(directory="templates")

# Dictionary to store chatbot instances per user
chatbot_sessions = {}

@router.get("/chat")
def chat_page(request: Request):
    return templates.TemplateResponse("chat.html", {"request": request})

@router.post("/chat")
def chat(
    request: Request,
    message: str = Form(...),
    db=Depends(get_db)
):
    # Get user from cookie
    user_email = request.cookies.get("user")
    if not user_email:
        return RedirectResponse("/login", status_code=302)

    user_instance = db.query(User).filter(User.email == user_email).first()
    if not user_instance:
        return RedirectResponse("/login", status_code=302)

    # Check if chatbot session exists for user
    # if user_email not in chatbot_sessions:
    #     chatbot_sessions[user_email] = ChatModel(user_instance, db)

    chatbot = chatbot_sessions[user_email]
    response = chatbot.chat(message)

    context = {
        "request": request,
        "chat_history": db.query(ChatHistory).filter(ChatHistory.user == user_instance).order_by(ChatHistory.id.asc()).all(),
        "user": user_instance
    }

    return templates.TemplateResponse("chat.html", context)
