from sqlalchemy import Column, Integer, String, TIMESTAMP, DateTime, ForeignKey, Text
from sqlalchemy.orm import relationship
from sqlalchemy.sql import func
from database import Base


class User(Base):
    __tablename__ = "users"

    id = Column(Integer, primary_key=True, nullable=False)
    username = Column(String, nullable=False)
    email = Column(String, nullable=False, unique=True)
    password = Column(String, nullable=False)
    chats = relationship("ChatHistory", back_populates="user")
    Column(TIMESTAMP, server_default=func.current_timestamp(), nullable=False)
