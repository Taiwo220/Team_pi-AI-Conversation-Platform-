from fastapi import FastAPI
from app.config import Settings, engine, Base
from auth.routers import router

# Load settings
settings = Settings()

# Create FastAPI app
app = FastAPI(title="FastAPI Auth Example")

# Root endpoint
@app.get("/")
def read_root():
    return {"message": "Hello, World!"}

# Include routers
app.include_router(router, prefix="/auth", tags=["auth"])

# Create database tables
@app.on_event("startup")
async def startup():
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)