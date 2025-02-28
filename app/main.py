from fastapi import FastAPI
from .config.db import Base, engine
from .routers import characters, chat
from .models import user, character, conversation, message
# from .routers import auth

# Optional: If you need any startup/shutdown events, you can define them here:
# from .events import startup, shutdown

app = FastAPI()

Base.metadata.create_all(bind=engine)

app.include_router(characters.router, prefix="/characters", tags=["characters"])
app.include_router(auth.router)
# app.include_router(auth.router, prefix="/auth", tags=["auth"])
app.include_router(chat.router, prefix="/chat", tags=["chat"])

@app.get("/")
def read_root():
    return {"message": "Welcome to the FastAPI + SQLite App!"}
