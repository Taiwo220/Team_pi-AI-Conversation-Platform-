import sys
import json
from pathlib import Path

sys.path.append(".")

from app.config.db import SessionLocal
from app.models.character import Character

def main():
    data_file = Path("characters.json")
    if not data_file.is_file():
        print("Error: characters.json not found in project root.")
        sys.exit(1)

    try:
        with data_file.open("r", encoding="utf-8") as f:
            data = json.load(f)
            characters_data = data["characters"] 
    except Exception as e:
        print(f"Error reading JSON file: {e}")
        sys.exit(1)

    db = SessionLocal()
    try:
        for char_data in characters_data:
            new_char = Character(
                name=char_data["name"],
                nationality=char_data.get("nationality"),
                profession=char_data.get("profession"),
                description=char_data.get("description"),
                image_url=char_data.get("image_url"),
                background=char_data.get("background"),
                personality_traits=char_data.get("personality_traits"),
                motivations=char_data.get("motivations"),
                quirks_habits=char_data.get("quirks_habits"),
                example_sentences=char_data.get("example_sentences"),
                is_personal_character=False,
                owner_id=None
            )
            db.add(new_char)

        db.commit()
        print("Characters inserted successfully!")
    except Exception as e:
        db.rollback()
        print(f"Error inserting characters: {e}")
    finally:
        db.close()

if __name__ == "__main__":
    main()