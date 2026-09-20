"""Generate character reference descriptions with the configured Ollama model."""
import csv
import hashlib
import sqlite3
from pathlib import Path

from ollama_client import OllamaClient
from cache_seed import ensure_runtime_cache
from runtime_config import load_positive_int, load_setting
from tqdm import tqdm

SCRIPT_DIR = Path(__file__).parent.resolve()
IMAGES = (SCRIPT_DIR / ".." / "images").resolve()
CACHE = SCRIPT_DIR / "caches" / "danbooru_character_explanationsFromVLM.sqlite3"
CACHE.parent.mkdir(parents=True, exist_ok=True)
OUTPUT = CACHE
PROMPT = (
    "Write exactly one concise paragraph describing only the character's stable "
    "visual identity: hair or fur, eyes, skin or fur color, clothing, accessories, "
    "body type, and distinctive permanent markings. Prefer visible full-body or "
    "upper-body traits, especially the face. Do not describe pose, action, "
    "background, setting, camera angle, composition, emotion, text, temporary "
    "details, or what the character is doing. Do not guess; mention only clearly "
    "visible traits. Output only the paragraph."
)
KEYWORDS = {"nude", "nipples", "pussy", "penis", "anus", "sex", ",cum", "lying",
            "multiple views", "bikini", "swimsuit", "undressing", "masturbation",
            "from below", "upside-down", "facial", "one breast out", "lingerie",
            "close-up", "from behind", "spread legs", "2boys", "2girls", "3boys",
            "3girls", "multiple girls", "multiple boys", "comic"}
INVALID_OUTPUT_TERMS = {
    "maybe", "possibly", "appears to", "standing", "sitting", "lying", "background",
    "in a room", "outdoors", "looking at the viewer", "smiling", "frowning",
    "holding", "doing", "text", "nude", "naked", "bikini", "underwear",
}
def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()

def init_db():
    ensure_runtime_cache(CACHE)
    with sqlite3.connect(CACHE) as db:
        db.execute(
            "CREATE TABLE IF NOT EXISTS character_explanations ("
            "tag TEXT PRIMARY KEY, standard TEXT NOT NULL, "
            "image_hash TEXT NOT NULL, model TEXT NOT NULL)"
        )
        # A rejected model response must not make the same image run again on
        # every pipeline invocation.  Keep attempts scoped to both the model
        # and image content so a changed model or replacement image can still
        # be tried normally.
        db.execute(
            "CREATE TABLE IF NOT EXISTS character_description_attempts ("
            "tag TEXT NOT NULL, image_hash TEXT NOT NULL, model TEXT NOT NULL, "
            "PRIMARY KEY (tag, image_hash, model))"
        )

def read_metadata(path):
    with path.with_suffix(".csv").open(newline="", encoding="utf-8-sig") as f:
        row = next(csv.DictReader(f), {})
    return row.get("characters", "").strip(), row.get("general", "").strip()


def normalize_description(description):
    had_multiple_lines = "\n" in description
    description = " ".join(description.split())
    if (not description or had_multiple_lines or len(description.split()) > 100
            or any(term in description.lower() for term in INVALID_OUTPUT_TERMS)):
        return None
    return description

def main():
    url, _ = load_setting("OLLAMA_URL")
    model, _ = load_setting("OLLAMA_MODEL")
    timeout, _ = load_positive_int("OLLAMA_TIMEOUT_SECONDS", "300")
    client = OllamaClient(url, timeout)
    init_db()
    with sqlite3.connect(CACHE) as db:
        existing = {row[0] for row in db.execute("SELECT tag FROM character_explanations")}
        attempted = {
            (row[0], row[1])
            for row in db.execute(
                "SELECT tag, image_hash FROM character_description_attempts "
                "WHERE model = ?",
                (model,),
            )
        }
    candidates = {}
    for image in sorted(IMAGES.glob("*.*")):
        if image.suffix.lower() not in {".jpg", ".jpeg", ".png", ".webp", ".bmp"} or not image.with_suffix(".csv").exists():
            continue
        characters, general = read_metadata(image)
        if not characters or ", " in characters or characters in existing or "solo" not in general.lower():
            continue
        if any(keyword in general.lower() for keyword in KEYWORDS):
            continue
        candidates.setdefault(characters, []).append(image)
    pending_candidates = {}
    exhausted = 0
    for tag, images in candidates.items():
        pending = [(image, digest(image)) for image in images]
        pending = [item for item in pending if (tag, item[1]) not in attempted]
        if not pending:
            exhausted += 1
            continue
        pending_candidates[tag] = pending

    tqdm.write(
        f"Character descriptions: {len(existing)} cached, "
        f"{len(pending_candidates)} pending, {exhausted} previously exhausted"
    )
    if pending_candidates:
        client.ensure_model(model, tqdm.write)
    for tag, pending in tqdm(pending_candidates.items(), desc="Character descriptions"):
        for image, image_hash in pending:
            raw_description, _ = client.chat(
                model, PROMPT, image,
                {"temperature": 0.0, "num_predict": 256, "num_ctx": 4096},
            )
            description = normalize_description(raw_description)
            with sqlite3.connect(CACHE) as db:
                db.execute(
                    "INSERT OR IGNORE INTO character_description_attempts "
                    "(tag, image_hash, model) VALUES (?, ?, ?)",
                    (tag, image_hash, model),
                )
                if description is not None:
                    db.execute(
                        "INSERT OR IGNORE INTO character_explanations "
                        "(tag, standard, image_hash, model) VALUES (?, ?, ?, ?)",
                        (tag, description, image_hash, model),
                    )
            if description is None:
                tqdm.write(
                    f"Skipped unsuitable character description for {image.name}; "
                    "trying another candidate"
                )
                continue
            break
    if exhausted:
        tqdm.write(
            f"Skipped {exhausted} character tag(s) whose eligible images were "
            "already attempted without a usable description"
        )

if __name__ == "__main__":
    main()
