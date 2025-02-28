import os
import openai
from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session
from pydantic import BaseModel
from contextlib import contextmanager
from sqlalchemy.exc import SQLAlchemyError
import logging
import asyncio
from .auth import get_current_user, UserTokenData
from ..models.conversation import Conversation
from ..models.message import Message
from ..models.character import Character
from ..config.dependencies import get_db
from dotenv import load_dotenv
load_dotenv()

router = APIRouter()

logger = logging.getLogger(__name__)

client = openai.OpenAI()

class MessageRequest(BaseModel):
    message: str

@contextmanager
def db_transaction(db: Session):
    """Context manager for database transactions with proper error handling"""
    try:
        yield
        db.commit()
    except SQLAlchemyError as e:
        db.rollback()
        logger.error(f"Database error: {str(e)}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Database operation failed"
        )

def build_character_system_message(character: Character) -> str:
    """
    Combine the character's full info into a single text block that instructs the model
    how to behave as that character.
    """
    lines = []
    lines.append(f"You are **{character.name}**, a fictional character.")
    if character.nationality:
        lines.append(f"Nationality: {character.nationality}")
    if character.profession:
        lines.append(f"Profession: {character.profession}")
    if character.background:
        lines.append(f"Background: {character.background}")
    if character.personality_traits:
        lines.append(f"Personality Traits: {character.personality_traits}")
    if character.motivations:
        lines.append(f"Motivations: {character.motivations}")
    if character.quirks_habits:
        lines.append(f"Quirks & Habits: {character.quirks_habits}")
    if character.example_sentences:
        lines.append(f"Example Sentences or Catchphrases: {character.example_sentences}")

    lines.append(
        "Please respond **in the voice of this character**, incorporating their background and personality. "
        "Stay consistent with their motivations and mannerisms. Avoid mentioning that you are an AI model."
    )

    return "\n".join(lines)

async def generate_ai_response(messages, model):
    """
    Generate AI response with custom retry functionality
    """
    max_retries = 3
    retry_count = 0
    base_wait_time = 2
    
    while True:
        try:
            response = client.chat.completions.create(
                model=model,
                messages=messages,
                temperature=0.7
            )
            return response.choices[0].message.content
            
        except openai.BadRequestError as e:
            logger.error(f"OpenAI BadRequest error: {str(e)}")
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Invalid request to AI provider: {str(e)}"
            )
            
        except (openai.APIError, openai.APIConnectionError, openai.RateLimitError) as e:
            retry_count += 1
            if retry_count > max_retries:
                logger.error(f"OpenAI API error after {max_retries} retries: {str(e)}")
                raise HTTPException(
                    status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                    detail=f"AI provider unavailable after multiple attempts: {str(e)}"
                )
                
            wait_time = base_wait_time * (2 ** (retry_count - 1))
            logger.warning(f"OpenAI API error (retry {retry_count}/{max_retries}): {str(e)}, waiting {wait_time}s")
            await asyncio.sleep(wait_time)
            
        except Exception as e:
            logger.error(f"Unexpected error with AI provider: {str(e)}")
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail=f"Error communicating with AI provider: {str(e)}"
            )

@router.post("/start/{character_id}")
def start_conversation(
    character_id: int,
    db: Session = Depends(get_db),
    current_user: UserTokenData = Depends(get_current_user)
):
    """
    Explicitly create a conversation session between current user and a given character.
    """
    try:
        char = db.query(Character).get(character_id)
        if not char:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Character not found")

        conversation = Conversation(
            user_id=current_user.id,
            character_id=character_id
        )
        
        with db_transaction(db):
            db.add(conversation)
            db.refresh(conversation)

        return {
            "conversation_id": conversation.id,
            "character_id": character_id,
            "message": "Conversation started."
        }
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error starting conversation: {str(e)}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to start conversation"
        )

@router.get("/history/{conversation_id}")
def get_conversation_history(
    conversation_id: int,
    db: Session = Depends(get_db),
    current_user: UserTokenData = Depends(get_current_user)
):
    """
    Return the entire message history of a conversation.
    """
    try:
        conversation = db.query(Conversation).get(conversation_id)
        if not conversation:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Conversation not found")
        if conversation.user_id != current_user.id:
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Not your conversation")

        messages = db.query(Message).filter(
            Message.conversation_id == conversation_id
        ).order_by(Message.created_at.asc()).all()

        return {
            "conversation_id": conversation_id,
            "messages": [
                {
                    "role": msg.role,
                    "content": msg.content,
                    "created_at": msg.created_at
                }
                for msg in messages
            ]
        }
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error retrieving conversation history: {str(e)}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to retrieve conversation history"
        )

@router.post("/message/{character_id}")
async def send_message(
    character_id: int,
    request: MessageRequest,
    db: Session = Depends(get_db),
    current_user: UserTokenData = Depends(get_current_user)
):
    """
    Send a user message to a character. If there's no open conversation, create one.
    Then pass the entire conversation history + a system message with character details to OpenAI,
    get the AI response, store it, and return it.
    """
    try:
        model = os.getenv("OPENAI_DEFAULT_MODEL")
        
        character = db.query(Character).get(character_id)
        if not character:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Character not found")

        with db_transaction(db):
            conversation = (
                db.query(Conversation)
                .filter(
                    Conversation.user_id == current_user.id,
                    Conversation.character_id == character_id
                )
                .first()
            )
            if not conversation:
                conversation = Conversation(
                    user_id=current_user.id,
                    character_id=character_id
                )
                db.add(conversation)
                db.flush()

            user_msg_record = Message(
                conversation_id=conversation.id,
                role="user",
                content=request.message
            )
            db.add(user_msg_record)
        
        messages = db.query(Message).filter(
            Message.conversation_id == conversation.id
        ).order_by(Message.created_at.asc()).all()

        system_content = build_character_system_message(character)
        openai_messages = [{"role": "system", "content": system_content}]
        
        for msg in messages:
            openai_messages.append({
                "role": msg.role,
                "content": msg.content
            })

        ai_content = await generate_ai_response(openai_messages, model)

        with db_transaction(db):
            ai_msg_record = Message(
                conversation_id=conversation.id,
                role="assistant",
                content=ai_content
            )
            db.add(ai_msg_record)

        return {
            "conversation_id": conversation.id,
            "user_message": request.message,
            "ai_message": ai_content,
            "model_used": model
        }
    
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error processing message: {str(e)}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to process message"
        )