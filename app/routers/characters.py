import os
import openai
from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session
from pydantic import ValidationError
from typing import List

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

@router.get("/", response_model=List[CharacterOut])
def get_characters(
    db: Session = Depends(get_db),
    current_user: UserTokenData = Depends(get_current_user)
):
    """
    Fetch all characters.
    Now requires authentication. 
    If you want this public, remove `current_user` or handle permissions differently.
    """
    chars = db.query(Character).all()
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
    return char

@router.post("/", response_model=CharacterOut, status_code=status.HTTP_201_CREATED)
def create_character(
    new_char: CharacterCreate,
    db: Session = Depends(get_db),
    current_user: UserTokenData = Depends(get_current_user)
):
    """
    Create a new character.
    You can decide if all characters created this way are 'personal'
    or if you allow 'public' characters as well.
    """
    new_char.is_personal_character = True
    new_char.owner_id = current_user.id

    char = Character(**new_char.dict())
    db.add(char)
    db.commit()
    db.refresh(char)
    return char

@router.put("/{character_id}", response_model=CharacterOut)
def update_character(
    character_id: int,
    char_update: CharacterUpdate,
    db: Session = Depends(get_db),
    current_user: UserTokenData = Depends(get_current_user)
):
    char = db.query(Character).get(character_id)
    if not char:
        raise HTTPException(status_code=404, detail="Character not found")

    if char.is_personal_character and char.owner_id != current_user.id:
        raise HTTPException(status_code=403, detail="Not allowed to modify this character.")

    update_data = char_update.dict(exclude_unset=True)
    for field, value in update_data.items():
        setattr(char, field, value)

    db.commit()
    db.refresh(char)
    return char

@router.delete("/{character_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_character(
    character_id: int,
    db: Session = Depends(get_db),
    current_user: UserTokenData = Depends(get_current_user)
):
    char = db.query(Character).get(character_id)
    if not char:
        raise HTTPException(status_code=404, detail="Character not found")

    if char.is_personal_character and char.owner_id != current_user.id:
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
    We'll set the owner_id from the JWT token, ensuring it's personal to this user.
    """
    user_prompt = build_character_prompt(preferences)

    try:
        response = openai.ChatCompletion.create(
            model="gpt-3.5-turbo",
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

    new_char_data.is_personal_character = True
    new_char_data.owner_id = current_user.id

    char_model = Character(**new_char_data.dict())
    db.add(char_model)
    db.commit()
    db.refresh(char_model)

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
