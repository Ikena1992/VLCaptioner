"""Character tag selection shared by the Torii and refiner prompts."""

import re


def normalize_character_key(name: str) -> str:
    return " ".join(name.lower().replace("_", " ").split())


def display_character_name(name: str) -> str:
    unqualified = re.sub(r"[\s_]*\([^)]*\)", "", name)
    return " ".join(unqualified.replace("_", " ").split())


CHARACTER_EXCLUSIONS = {
    "gilberta (arknights)": "angelina (arknights)",
    "laevatain (arknights)": "surtr (arknights)",
    "female endministrator (arknights)": "endministrator (arknights)",
    "male endministrator (arknights)": "endministrator (arknights)",
    "ardelia (arknights)": "eyjafjalla (arknights)",
}


def select_character_tags(raw_tags: str) -> list[str]:
    """Select one tag per display name, retaining the most specific variant."""
    tags = [tag.strip() for tag in raw_tags.split(",") if tag.strip()]
    keys = {normalize_character_key(tag) for tag in tags}
    excluded = {older for newer, older in CHARACTER_EXCLUSIONS.items() if newer in keys}
    variants = {}
    for tag in tags:
        if normalize_character_key(tag) in excluded:
            continue
        name = display_character_name(tag).lower()
        if name:
            variants.setdefault(name, []).append(tag)
    return [max(group, key=lambda tag: (tag.count("("), len(tag))) for group in variants.values()]


def clean_explanation(text: str) -> str:
    """Remove trailing wiki headings and voice credits from explanations."""
    description = re.split(r"\bh[45]\b|Voiced by", text, flags=re.IGNORECASE)[0]
    return " ".join(description.split())
