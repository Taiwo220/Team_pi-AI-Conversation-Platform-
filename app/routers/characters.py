import os
import openai
import json
from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session
from pydantic import ValidationError
from typing import List, Any, Dict
from sqlalchemy import and_
from sqlalchemy.future import select
from .auth import get_current_user, UserTokenData
from ..config.dependencies import get_db
from ..models.character import Character
from ..schemas.character import (
    CharacterCreate,
    CharacterUpdate,
    CharacterOut
)

router = APIRouter()
openai.api_key = os.getenv("OPENAI_API_KEY")


# ----------------------------
# Helper Functions
# ----------------------------
def serialize_list_fields(data: Dict[str, Any]) -> Dict[str, Any]:
    """
    Convert Python list fields (personality_traits, quirks_habits, example_sentences)
    into JSON strings for storage in the DB.
    """
    list_fields = ["personality_traits", "quirks_habits", "example_sentences"]
    for field in list_fields:
        value = data.get(field)
        if isinstance(value, list):
            data[field] = json.dumps(value)
    return data

def deserialize_list_fields(char: Character) -> Character:
    """
    Convert JSON strings in DB back into Python lists so that
    Pydantic schemas (CharacterOut) show actual lists to the user.
    """
    if char.personality_traits:
        try:
            char.personality_traits = json.loads(char.personality_traits)
        except json.JSONDecodeError:
            pass  # If it's not valid JSON, leave as-is or handle error

    if char.quirks_habits:
        try:
            char.quirks_habits = json.loads(char.quirks_habits)
        except json.JSONDecodeError:
            pass

    if char.example_sentences:
        try:
            char.example_sentences = json.loads(char.example_sentences)
        except json.JSONDecodeError:
            pass

    return char


def deserialize_list_fields_many(chars: List[Character]) -> List[Character]:
    """
    Convenience method to deserialize multiple Character objects at once.
    """
    for c in chars:
        deserialize_list_fields(c)
    return chars

@router.get("/", response_model=List[CharacterOut])
async def get_characters(
    db: Session = Depends(get_db),
    current_user: UserTokenData = Depends(get_current_user)
):
    """
    Fetch only:
    - public (non-personal) characters (is_personal_character=False), or
    - personal characters owned by the current user.
    """
    print(current_user, current_user.user_id)
    stmt = select(Character).where(
        and_(
            Character.is_personal_character == False,
            Character.owner_id == current_user.user_id
        )
    )
    
    result = await db.execute(stmt)
    chars = result.scalars().all()
    
    # Convert JSON strings to Python lists before returning
    chars = deserialize_list_fields_many(chars)
    return chars

@router.get("/{character_id}", response_model=CharacterOut)
def get_character(
    character_id: int,
    db: Session = Depends(get_db),
    current_user: UserTokenData = Depends(get_current_user)
):
    char = db.query(Character).get(character_id)
    if not char:
        raise HTTPException(status_code=404, detail="Character not found")

    # Deserialize list fields
    deserialize_list_fields(char)
    return char

@router.post("/", response_model=CharacterOut, status_code=status.HTTP_201_CREATED)
def create_character(
    new_char: CharacterCreate,
    db: Session = Depends(get_db),
    current_user: UserTokenData = Depends(get_current_user)
):
    """
    Create a new character, personal to the user.
    """
    # Force personal & ownership
    new_char.is_personal_character = True
    new_char.owner_id = current_user.user_id

    # Convert the Pydantic model to a dict, then serialize list fields to JSON strings
    char_data = new_char.dict()
    char_data = serialize_list_fields(char_data)

    # Create and save
    char = Character(**char_data)
    db.add(char)
    db.commit()
    db.refresh(char)

    # Deserialize before returning, so the response matches the schema
    deserialize_list_fields(char)
    return char

@router.put("/{character_id}", response_model=CharacterOut)
def update_character(
    character_id: int,
    char_update: CharacterUpdate,
    db: Session = Depends(get_db),
    current_user: UserTokenData = Depends(get_current_user)
):
    """
    Update an existing character's fields, including potential list fields.
    """
    char = db.query(Character).get(character_id)
    if not char:
        raise HTTPException(status_code=404, detail="Character not found")

    # Ownership check if personal
    if char.is_personal_character and char.owner_id != current_user.user_id:
        raise HTTPException(status_code=403, detail="Not allowed to modify this character.")

    update_data = char_update.dict(exclude_unset=True)
    # Serialize any list fields so we store them as JSON
    update_data = serialize_list_fields(update_data)

    # Apply updates
    for field, value in update_data.items():
        setattr(char, field, value)

    db.commit()
    db.refresh(char)

    deserialize_list_fields(char)
    return char

@router.patch("/{character_id}/avatar", response_model=CharacterOut)
def update_character_avatar(
    character_id: int,
    payload: dict,
    db: Session = Depends(get_db),
    current_user: UserTokenData = Depends(get_current_user)
):
    """
    Updates only the `image_url` for a personal character that the user owns.
    If the character is not personal or the user doesn't own it, raises 403.
    """
    char = db.query(Character).get(character_id)
    if not char:
        raise HTTPException(status_code=404, detail="Character not found")

    # Must be personal & owned by current user
    if not char.is_personal_character or char.owner_id != current_user.user_id:
        raise HTTPException(
            status_code=403,
            detail="You do not own this character or it's not personal."
        )

    new_image_url = payload.get("image_url")
    if not new_image_url:
        raise HTTPException(status_code=422, detail="No image_url provided.")

    char.image_url = new_image_url
    db.commit()
    db.refresh(char)

    deserialize_list_fields(char)
    return char

@router.delete("/{character_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_character(
    character_id: int,
    db: Session = Depends(get_db),
    current_user: UserTokenData = Depends(get_current_user)
):
    """
    Delete a character if the user owns it (if personal).
    """
    char = db.query(Character).get(character_id)
    if not char:
        raise HTTPException(status_code=404, detail="Character not found")

    if char.is_personal_character and char.owner_id != current_user.user_id:
        raise HTTPException(status_code=403, detail="Not allowed to delete this character.")

    db.delete(char)
    db.commit()
    return None

@router.post("/ai-generate", response_model=CharacterOut)
def generate_character_automatically(
    preferences: dict,
    db: Session = Depends(get_db),
    current_user: UserTokenData = Depends(get_current_user)
):
    """
    Generate a character dynamically using ChatGPT, then save to the database.
    We'll ensure it's personal to this user, and handle list fields as JSON.
    """
    user_prompt = build_character_prompt(preferences)

    try:
        response = openai.ChatCompletion.create(
            model="gpt-4o",
            messages=[
                {
                    "role": "system",
                    "content": (
                        "You are an expert at creating fictional characters. "
                        "You will receive some preferences and must return a JSON object **only**, "
                        "matching the following Pydantic schema (fields required; if a field isn't relevant, set it to an empty string or list). "
                        "Schema fields: name (str), nationality (str|None), profession (str|None), description (str|None), image_url (str|None), "
                        "background (str|None), personality_traits (list of strings|None), motivations (str|None), quirks_habits (list of strings|None), "
                        "example_sentences (list of strings|None), is_personal_character (bool), owner_id (int|None). "
                        "Return strictly valid JSON. No extra commentary, no markdown code fences."
                    )
                },
                {
                    "role": "user",
                    "content": user_prompt
                }
            ],
            temperature=0.7,
        )
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=f"OpenAI API error: {e}"
        )

    raw_content = response["choices"][0]["message"]["content"].strip()

    try:
        new_char_data = CharacterCreate.parse_raw(raw_content)
    except ValidationError as ve:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=f"Invalid JSON from AI: {ve}"
        )

    # Force personal & ownership
    new_char_data.is_personal_character = True
    new_char_data.owner_id = current_user.user_id

    # Convert to dict and JSON-serialize list fields
    data_dict = new_char_data.dict()
    data_dict = serialize_list_fields(data_dict)

    char_model = Character(**data_dict)
    db.add(char_model)
    db.commit()
    db.refresh(char_model)

    # Convert fields back to list for the response
    deserialize_list_fields(char_model)
    return char_model

def build_character_prompt(preferences: dict) -> str:
    prompt_parts = [
        "Based on the user preferences, create a fictional character with the following details:",
    ]
    for k, v in preferences.items():
        prompt_parts.append(f"{k}: {v}")

    prompt_parts.append(
        "Remember to strictly output JSON that fits the CharacterCreate schema. "
        "Do not include any text outside of the JSON."
    )

    return "\n".join(prompt_parts)
