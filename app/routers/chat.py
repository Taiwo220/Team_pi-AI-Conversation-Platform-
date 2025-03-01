import os
import openai
import chromadb
from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession
from pydantic import BaseModel
from contextlib import asynccontextmanager
from sqlalchemy.exc import SQLAlchemyError
import logging
import asyncio
from .auth import get_current_user, UserTokenData
from ..models.conversation import Conversation
from ..models.message import Message
from ..models.character import Character
from ..config.dependencies import get_db
from dotenv import load_dotenv
import uuid

load_dotenv()

router = APIRouter()
logger = logging.getLogger(__name__)

client = openai.OpenAI()

chroma_client = chromadb.PersistentClient(path="./aicharacters_vector_storage")
collection = chroma_client.get_or_create_collection(name="conversations")


class MessageRequest(BaseModel):
    message: str


@asynccontextmanager
async def db_transaction(db: AsyncSession):
    """Async context manager for database transactions with proper error handling."""
    try:
        yield
        await db.commit()
    except SQLAlchemyError as e:
        await db.rollback()
        logger.error(f"Database error: {str(e)}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Database operation failed"
        )


async def store_message_embedding(conversation_id: int, message: str, role: str):
    """
    Stores the message as an embedding in ChromaDB, allowing vector-based retrieval.
    """
    embedding_response = client.embeddings.create(
        model="text-embedding-ada-002", input=message
    )
    embedding_vector = embedding_response.data[0].embedding

    unique_id = f"{conversation_id}-{role}-{uuid.uuid4()}"

    collection.add(
        ids=[unique_id],
        embeddings=[embedding_vector],
        documents=[message],
        metadatas=[{"conversation_id": conversation_id, "role": role}]
    )

async def retrieve_relevant_messages(conversation_id: int, query: str, top_k=8):
    """
    Retrieves the top-k most relevant past messages for context using ChromaDB's vector search,
    filtered to only include messages from the given conversation.
    Falls back to the most recent messages if no good matches are found.
    """
    query_embedding = client.embeddings.create(
        model="text-embedding-ada-002", input=query
    ).data[0].embedding

    results = collection.query(
        query_embeddings=[query_embedding],
        n_results=top_k * 2  # retrieve more than needed so we have enough after filtering
    )

    filtered_results = []
    if results and "documents" in results and results["documents"]:
        for i in range(len(results["documents"][0])):
            metadata = results["metadatas"][0][i]
            if metadata.get("conversation_id") == conversation_id:
                filtered_results.append({
                    "role": metadata.get("role"),
                    "content": results["documents"][0][i]
                })

    if not filtered_results:
        logger.warning(
            f"No relevant messages found for conversation {conversation_id} from vector search. "
            "Fetching latest messages as fallback."
        )
        # Fallback: Retrieve the last 'top_k' stored messages and filter by conversation_id
        recent_results = collection.peek(top_k * 2)
        filtered_recent = []
        for i in range(len(recent_results["documents"])):
            metadata = recent_results["metadatas"][i]
            if metadata.get("conversation_id") == conversation_id:
                filtered_recent.append({
                    "role": metadata.get("role"),
                    "content": recent_results["documents"][i]
                })
        return filtered_recent[:top_k]

    return filtered_results[:top_k]

def build_character_system_message(character: Character) -> str:
    """
    Make the AI *assume* the character's identity rather than describing the character.
    """
    lines = [
        f"My name is **{character.name}**."
    ]
    if character.nationality:
        lines.append(f"I am {character.nationality}.")
    if character.profession:
        lines.append(f"I work as {character.profession}.")
    if character.background:
        lines.append(f"Here's my story: {character.background}")
    if character.personality_traits:
        lines.append(f"People describe me as {character.personality_traits}.")
    if character.motivations:
        lines.append(f"What drives me? {character.motivations}.")
    if character.quirks_habits:
        lines.append(f"I have a few quirks: {character.quirks_habits}.")
    if character.example_sentences:
        lines.append(f"Here's how I talk: {character.example_sentences}")

    lines.append(
        f"I will respond as myself—**{character.name}**—in a way that aligns with my personality, experiences, and emotions. Before I respond, I will absolutely check if I have enough context and check if relevant details exist in past messages. If I am not sure, I will ask instead of responding immediately. "
        "I won't break character, and I will express myself naturally, since thiss is a real conversation."
    )

    return "\n".join(lines)


async def generate_ai_response(messages, model):
    """
    Generate AI response with custom retry functionality.
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
async def start_conversation(
    character_id: int,
    db: AsyncSession = Depends(get_db),
    current_user: UserTokenData = Depends(get_current_user)
):
    """
    Explicitly create a conversation session between current user and a given character.
    """
    try:
        char = await db.get(Character, character_id)
        if not char:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Character not found")

        conversation = Conversation(
            user_id=current_user.user_id,
            character_id=character_id
        )
        
        async with db_transaction(db):
            db.add(conversation)
            await db.flush()  # Flush to assign an ID
            await db.refresh(conversation)

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
async def get_conversation_history(
    conversation_id: int,
    db: AsyncSession = Depends(get_db),
    current_user: UserTokenData = Depends(get_current_user)
):
    """
    Return the entire message history of a conversation.
    """
    try:
        conversation = await db.get(Conversation, conversation_id)
        if not conversation:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Conversation not found")
        if conversation.user_id != current_user.user_id:
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Not your conversation")

        from sqlalchemy import select
        result = await db.execute(
            select(Message).where(Message.conversation_id == conversation_id).order_by(Message.created_at.asc())
        )
        messages = result.scalars().all()

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


@router.post("/message/{conversation_id}")
async def send_message(
    conversation_id: int,
    request: MessageRequest,
    db: AsyncSession = Depends(get_db),
    current_user: UserTokenData = Depends(get_current_user)
):
    """
    Uses a vector database to retrieve only relevant past messages instead of the entire chat history.
    """
    try:
        model = os.getenv("OPENAI_DEFAULT_MODEL")

        conversation = await db.get(Conversation, conversation_id)
        if not conversation:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Conversation not found")
        if conversation.user_id != current_user.user_id:
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="You do not own this conversation")

        character = await db.get(Character, conversation.character_id)
        if not character:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Character not found")

        async with db_transaction(db):
            user_msg_record = Message(
                conversation_id=conversation.id,
                role="user",
                content=request.message
            )
            db.add(user_msg_record)
            await db.flush()
        await store_message_embedding(conversation.id, request.message, "user")

        relevant_messages = await retrieve_relevant_messages(conversation.id, request.message)

        system_content = build_character_system_message(character)
        openai_messages = [{"role": "system", "content": system_content}]
        openai_messages.extend(relevant_messages)  # Add retrieved context
        openai_messages.append({"role": "user", "content": request.message})

        ai_content = await generate_ai_response(openai_messages, model)

        async with db_transaction(db):
            ai_msg_record = Message(
                conversation_id=conversation.id,
                role="assistant",
                content=ai_content
            )
            db.add(ai_msg_record)
            await db.flush()
        await store_message_embedding(conversation.id, ai_content, "assistant")

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
