from pathlib import Path
import csv
import html
import sqlite3
import re
from collections import Counter
from tqdm import tqdm  # pip install tqdm
from caption_terminology import build_terminology_section
from cache_seed import ensure_runtime_cache

# -------------------------------------------------
# Configuration
# -------------------------------------------------
SCRIPT_DIR = Path(__file__).parent.resolve()
IMAGES_DIR = (SCRIPT_DIR / ".." / "images").resolve()

CHARACTER_DB = SCRIPT_DIR / "caches/danbooru_character_explanationsFromVLM.sqlite3"
WIKI_CACHE_DB = SCRIPT_DIR / "caches/cache_wiki.sqlite3"
SUPPORTED_EXTENSIONS = {".jpg", ".jpeg", ".png", ".webp", ".bmp"}

# Set this to True while testing prompt changes.
OVERWRITE_EXISTING_REQUESTS = False


def sanitize_torii_output(text: str) -> str:
    """Remove model-control/reasoning wrappers before embedding a Torii report."""
    text = html.unescape(text).replace("\\<", "<").replace("\\>", ">")
    text = text.replace("{", "").replace("}", "")
    text = re.sub(
        r"(?ims)^[ \t]*\\?#+[ \t]*<think>[ \t]*\r?\n.*?"
        r"^[ \t]*(?:\\?#+[ \t]*)?</think>[ \t]*(?:\r?\n|\Z)",
        "",
        text,
    )
    text = re.sub(r"(?im)^[ \t]*(?:\\?#+[ \t]*)?</?think>[ \t]*$", "", text)
    return text.strip()

SIMPLE_BACKGROUND_TAGS = {
    "simple background",
    "white background",
    "black background",
    "grey background",
    "gray background",
    "red background",
    "blue background",
    "green background",
    "yellow background",
    "pink background",
    "purple background",
    "orange background",
    "brown background",
    "aqua background",
    "silver background",
    "gold background",
}

MULTI_CHARACTER_TAGS = {
    "2girls", "2boys", "3girls", "3boys",
    "4girls", "4boys", "5girls", "5boys",
    "6+girls", "6+boys", "multiple girls", "multiple boys",
}

TEXT_EXACT_TAGS = {
    "text",
    "signature",
    "username",
    "speech bubble",
    "body writing",
    "clothes writing",
    "patreon username",
    "artist name",
    "sign",
    "holding sign",
    "chinese zodiac",
}

TEXT_PARTIAL_KEYWORDS = {
    "text",
    "name",
    "writing",
    "username",
    "sign",
}

CHARACTER_EXCLUSIONS = {
    "gilberta (arknights)": {"angelina (arknights)"},
    "laevatain (arknights)": {"surtr (arknights)"},
    "female endministrator (arknights)": {"endministrator (arknights)"},
    "male endministrator (arknights)": {"endministrator (arknights)"},
    "ardelia_(arknights)": {"eyjafjalla_(arknights)"},
}


# -------------------------------------------------
# Helpers
# -------------------------------------------------
def normalize_tag(tag: str) -> str:
    """Normalize Danbooru-style tags for reliable matching."""
    tag = tag.strip().lower().replace("_", " ")
    tag = re.sub(r"\s+", " ", tag)
    return tag


def normalize_character_key(name: str) -> str:
    """Normalize character names for matching, while keeping parenthetical series names."""
    name = name.strip().lower().replace("_", " ")
    name = re.sub(r"\s+", " ", name)
    return name


def display_character_name(name: str) -> str:
    """Remove parenthetical series labels and make names readable."""
    name = re.sub(r"[\s_]*\([^)]*\)", "", name)
    name = name.replace("_", " ")
    name = re.sub(r"\s+", " ", name)
    return name.strip()


def clean_explanation(text: str) -> str:
    """Remove unwanted trailing metadata from character explanations."""
    text = re.split(r"\bh[45]\b|Voiced by", text, flags=re.IGNORECASE)[0]
    text = re.sub(r"\s+", " ", text)
    return text.strip()


def _load_sqlite_explanations(
    database_path: Path,
    table: str,
    explanation_column: str,
) -> dict[str, str]:
    """
    Load character explanations from a SQLite table.

    Uses several lookup keys per character so names can match whether they contain
    underscores, spaces, or parenthetical series labels.
    """
    database_path = ensure_runtime_cache(database_path)
    if not database_path.exists():
        return {}

    data = {}
    with sqlite3.connect(database_path) as database:
        rows = database.execute(
            f"SELECT tag, {explanation_column} FROM {table}"
        )

        for key, raw_explanation in rows:
            if not key or not raw_explanation:
                continue

            key = key.strip()
            explanation = clean_explanation(raw_explanation.strip())
            if not key or not explanation:
                continue

            lookup_keys = {
                key.lower(),
                normalize_character_key(key),
                display_character_name(key).lower(),
                normalize_character_key(display_character_name(key)),
            }

            for lookup_key in lookup_keys:
                if lookup_key:
                    data[lookup_key] = explanation

    return data


def load_character_explanations(database_path: Path) -> dict[str, str]:
    """Load VLM-generated character references from SQLite."""
    return _load_sqlite_explanations(
        database_path, "character_explanations", "standard"
    )


def load_wiki_explanations(database_path: Path) -> dict[str, str]:
    """Load Danbooru wiki character references from SQLite."""
    return _load_sqlite_explanations(database_path, "wiki_cache", "body")


def load_local_metadata(image_path: Path) -> tuple[str | None, str | None]:
    csv_file = image_path.with_suffix(".csv")
    if not csv_file.exists():
        return None, None

    with open(csv_file, encoding="utf-8") as f:
        reader = csv.DictReader(f)
        for row in reader:
            return (
                row.get("characters", "").strip(),
                row.get("artists", "").strip(),
            )

    return None, None


def get_local_franchise(image_path: Path) -> str:
    """
    Return the sidecar CSV copyright value for a Franchise line.

    Empty values and values containing "original" are intentionally ignored.
    """
    csv_file = image_path.with_suffix(".csv")
    if not csv_file.exists():
        return ""

    with open(csv_file, encoding="utf-8") as f:
        reader = csv.DictReader(f)

        if not reader.fieldnames or "copyright" not in reader.fieldnames:
            return ""

        for row in reader:
            copyright_value = row.get("copyright", "").strip()

            if not copyright_value or "original" in copyright_value.lower():
                return ""

            return copyright_value

    return ""


def pick_best_character_variants(raw_names: list[str]) -> list[str]:
    groups = {}

    for name in raw_names:
        base = display_character_name(name).lower()

        if base not in groups:
            groups[base] = []

        groups[base].append(name)

    def score(name: str) -> tuple[int, int]:
        return (
            name.count("("),
            len(name),
        )

    best = []
    for variants in groups.values():
        best_variant = max(variants, key=score)
        best.append(best_variant)

    return best


def apply_character_exclusions(character_names: list[str]) -> list[str]:
    normalized_exclusions = {
        normalize_character_key(required): {
            normalize_character_key(excluded)
            for excluded in excluded_names
        }
        for required, excluded_names in CHARACTER_EXCLUSIONS.items()
    }

    lower_names = {
        normalize_character_key(name)
        for name in character_names
    }

    names_to_remove = set()

    for required, excluded_names in normalized_exclusions.items():
        if required in lower_names:
            names_to_remove.update(excluded_names)

    return [
        name for name in character_names
        if normalize_character_key(name) not in names_to_remove
    ]


def lookup_character_explanation(
    raw_name: str,
    display_name: str,
    char_explanations: dict[str, str],
    char_explanations_fallback: dict[str, str],
) -> str:
    lookup_keys = [
        raw_name.lower(),
        normalize_character_key(raw_name),
        display_name.lower(),
        normalize_character_key(display_name),
    ]

    for lookup_key in lookup_keys:
        explanation = char_explanations.get(lookup_key)
        if explanation:
            return explanation

    for lookup_key in lookup_keys:
        explanation = char_explanations_fallback.get(lookup_key)
        if explanation:
            return explanation

    return ""


def load_character_data(
    image_path: Path,
    char_explanations: dict[str, str],
    char_explanations_fallback: dict[str, str],
) -> tuple[list[str], list[tuple[str, str]]]:
    """
    Returns:
    - character_names: display names used in the short-description instruction
    - character_descriptions: display name + general description pairs
    """
    characters_raw, _ = load_local_metadata(image_path)

    if not characters_raw:
        return [], []

    split_names = [
        c.strip()
        for c in characters_raw.split(",")
        if c.strip()
    ]

    split_names = apply_character_exclusions(split_names)
    raw_names = pick_best_character_variants(split_names)

    seen = set()
    character_names = []
    character_descriptions = []

    for raw_name in raw_names:
        display_name = display_character_name(raw_name)
        display_key = display_name.lower()

        if not display_name or display_key in seen:
            continue

        seen.add(display_key)
        character_names.append(display_name)

        explanation = lookup_character_explanation(
            raw_name=raw_name,
            display_name=display_name,
            char_explanations=char_explanations,
            char_explanations_fallback=char_explanations_fallback,
        )

        if explanation:
            character_descriptions.append((display_name, explanation))

    return character_names, character_descriptions


def detect_text(raw_tags_set: set[str]) -> bool:
    return any(
        tag in TEXT_EXACT_TAGS
        or any(keyword in tag for keyword in TEXT_PARTIAL_KEYWORDS)
        for tag in raw_tags_set
    )


def get_sentence_rules(
    character_count: int,
    simple_back: bool,
    has_multi: bool,
    raw_tags_set: set[str],
) -> list[str]:

    if character_count == 1 and simple_back:
        return ["Use 1-2 sentences, more only if needed for visible text. "]

    if character_count == 1:
        return ["Use 1-3 sentences, more only if needed for visible text. "]

    if character_count >= 5:
        return ["Use 4-6 sentences, more only if needed for visible text. "]

    if character_count >= 3:
        return ["Use 3-5 sentences, more only if needed for visible text. "]

    if character_count > 1 and simple_back:
        return ["Use 2-3 sentences, more only if needed for visible text. "]

    if character_count > 1:
        return ["Use 2-4 sentences, more only if needed for visible text. "]

    if "solo" in raw_tags_set and not has_multi and simple_back:
        return ["Use 1-2 sentences, more only if needed for visible text. "]

    if "solo" in raw_tags_set and not has_multi:
        return ["Use 1-3 sentences, more only if needed for visible text. "]

    if has_multi:
        return [
            "Use 3-5 sentences, more only if needed for visible text. ",
            "Use visible features to distinguish between different characters. ",
        ]

    if "1boy" in raw_tags_set and "1girl" in raw_tags_set:
        return [
            "Use 2-3 sentences, more only if needed for visible text. ",
            "Use visible features to distinguish between different characters. ",
        ]

    if simple_back:
        return ["Use 1-2 sentences, more only if needed for visible text. "]

    if "no humans" not in raw_tags_set:
        return ["Use 1-3 sentences, more only if needed for visible text. "]

    return []


def build_character_help(character_names: list[str]) -> str:
    if len(character_names) == 1:
        return (
            "Use \"# Scene\" and \"# Characters\" to locate the character. "
            "Name of the character in the image, make sure to use is: "
            f"{', '.join(character_names)}. "
        )

    if len(character_names) > 1:
        return (
            "Use \"# Scene\" and \"# Characters\" to locate the characters. "
            "Here are name tags for the characters in the image, make sure to use them: "
            f"{', '.join(character_names)}. "
            "Start with the characters on the left and go to the right. "
        )

    return (
        "Use \"# Scene\" and \"# Characters\" to locate characters in the image. "
        "Start with the characters on the left and go to the right. "
    )


def build_text_help(raw_tags_set: set[str], has_text: bool) -> str:
    if not has_text:
        return ""

    if "body writing" in raw_tags_set:
        return (
            "Use \"# Text\" to transcribe all image text content "
            "in double quotation marks (\"\"), along with its exact position, like which body part it is written on. "
        )

    return (
        "Use \"# Text\" to transcribe all image text content "
        "in double quotation marks (\"\"), along with its exact position. "
    )


def build_character_descriptions_section(
    character_names: list[str],
    character_descriptions: list[tuple[str, str]],
) -> str:
    if not character_descriptions:
        return ""

    lines = [
        "# Ground-truth character references\n"
        "These descriptions help match authorized characters to visible "
        "subjects. They cannot authorize or introduce additional names.\n"
    ]

    for character_name, description in character_descriptions:
        lines.append(f"## {character_name}\n")
        lines.append(f"{description}\n\n")

    return "".join(lines)


def build_character_authority_policy(character_names: list[str]) -> str:
    if character_names:
        names = "\n".join(character_names)
        return (
            "# Ground-truth character policy\n"
            "The per-image metadata is authoritative and always correct. "
            "The following list contains every character name permitted in "
            "the final captions:\n"
            "<ground_truth_characters>\n"
            f"{names}\n"
            "</ground_truth_characters>\n"
            "Character identities proposed by the Torii report are untrusted "
            "visual guesses. Never use a Torii-proposed name that is absent "
            "from <ground_truth_characters>. Treat such a subject as an "
            "original unnamed character and use an appropriate generic term. "
            "If Torii assigns an authorized name to the wrong visible subject, "
            "correct the assignment using the metadata references, visible "
            "traits, and spatial position.\n\n"
        )

    return (
        "# Ground-truth character policy\n"
        "The per-image metadata is authoritative and confirms that no known "
        "named character is present.\n"
        "<ground_truth_characters>\nNone\n</ground_truth_characters>\n"
        "Discard every character name proposed by the Torii report. Describe "
        "all subjects as original unnamed characters using appropriate generic "
        "terms. Do not use any character name in either final caption.\n\n"
    )


def build_caption_prompts(
    raw_tags: list[str],
    raw_tags_set: set[str],
    character_names: list[str],
    character_descriptions: list[tuple[str, str]],
    simple_back: bool,
    has_multi: bool,
    has_text: bool,
    franchise: str,
) -> dict[str, str]:

    common_rules = [
        "Write a standalone natural-language description of the visible scene "
        "that can serve as factual training data. A reader must be able to "
        "understand the scene without seeing the tags or source reports. ",
        "Use the supplied image as the primary visual source and the Torii "
        "report as a detailed secondary source. Correct Torii whenever it "
        "conflicts with the ground-truth metadata. ",
        "Use the framing term 'close-up' only when the exact ground-truth tag "
        "'close-up' appears in <ground_truth_tags>. If that tag is absent, do "
        "not use 'close-up' or describe the scene as a close-up, even if the "
        "image or Torii report suggests tight framing. ",
        "Use the ground-truth tags to confirm identities, counts, central "
        "actions, important relationships, defining traits, and preferred "
        "terminology. Integrate useful tag concepts into grammatical sentences; "
        "do not translate the full tag list, enumerate keywords, use tag-list "
        "syntax, or insert a tag that does not improve the description. ",
        "Describe visible content factually in connected prose and preserve "
        "spatial and physical relationships. Treat position as essential "
        "training data, not optional embellishment. Prefer relational statements "
        "such as who is doing what, where, and to whom over inventories of traits. ",
        "Do not mention that this is an image, anime, artwork, illustration, "
        "masterpiece, 8k, high-resolution, or a detailed description. ",
        "Avoid subjective praise, emotional interpretation, and purple prose. ",
        "Never mention ground truth, tags, metadata, instructions, prompts, "
        "rules, the Torii report, or what any source says, confirms, implies, "
        "or suggests. State the supported visual facts themselves directly. ",
        "Preserve all factual NSFW information present in the image, Torii "
        "report, or ground-truth tags. Never sanitize, censor, euphemize, or "
        "generalize explicit content. Treat visible nudity, exposed anatomy, "
        "sexual poses, sexual contact or acts, penetration, bodily fluids, "
        "sex toys, restraints, and interactions between specific body parts "
        "as important visual facts. Use direct, concrete wording. Do not add "
        "an explicit detail unless it is supported by the image, report, or "
        "ground-truth tags. ",
        "Use ground-truth tags to preserve central acts, positions, contact, "
        "and explicit anatomical states. The short and long tasks below define "
        "how much supporting detail each caption should retain. Integrate facts "
        "into the scene description rather than appending keywords or replacing "
        "specific concepts with vague descriptions. ",
        "Treat ground-truth sexual facts as confirmed, not uncertain visual "
        "guesses. Never qualify them with 'appears to', 'seems to', 'suggests', "
        "'implies', 'possibly', or similar hedging. Use an explicit relational "
        "statement that identifies who performs the act with whom, the exact "
        "act, the involved anatomy, and penetration or contact when specified. "
        "Do not substitute 'sexual activity', 'sexual contact', 'intimate act', "
        "or 'suggestive pose' for a more specific ground-truth term. ",
        "Output only the caption text with no heading, preamble, explanation, "
        "bullet list, Markdown, quotation around the whole caption, or notes. ",
        "After the caption, output END_CAPTION and stop. ",
    ]

    if character_names:
        common_rules.append(
            "Every authorized metadata character name must appear verbatim at "
            "least once in each caption. Use the "
            "authorized name instead of replacing that character with a generic "
            "term such as woman, girl, man, boy, person, or character. Do not use "
            "any character name proposed only by Torii. "
        )
    else:
        common_rules.append(
            "No named character is authorized. Discard all names proposed by "
            "Torii and describe every subject with an appropriate generic term. "
        )

    if has_multi or len(character_names) > 1:
        common_rules.append(
            "Distinguish characters by name or visible features and clearly "
            "describe their positions and interactions. "
        )

    if has_text:
        common_rules.append(
        "When visible text is important to the scene, transcribe it in double "
        "quotation marks and state its position. The short and long tasks below "
        "define how much text detail to retain. "
        )

    if not character_names:
        if "furry" in raw_tags_set:
            common_rules.append("Refer to the unnamed character as a furry. ")
        if "loli" in raw_tags_set:
            common_rules.append(
                'The ground-truth tag "loli" is present; use the exact word "loli". '
            )
        if "shota" in raw_tags_set:
            common_rules.append(
                'The ground-truth tag "shota" is present; use the exact word "shota". '
            )

    context = [
        "<torii_report>\n",
        "{torii_output}",
        "\n</torii_report>\n\n",
        "<ground_truth_tags>\n",
        ", ".join(raw_tags) if raw_tags else "None",
        "\n</ground_truth_tags>\n\n",
        build_character_authority_policy(character_names),
        build_terminology_section(raw_tags),
    ]

    if franchise:
        context.append(f"Ground-truth franchise: {franchise}\n\n")
    context.append(
        build_character_descriptions_section(character_names, character_descriptions)
    )
    context.append("# Caption rules\n")
    context.append("".join(common_rules))
    shared_context = "".join(context)

    short_rules = []

    if character_names:
        short_rules.append(
            "In this short caption, name every authorized character at least "
            f"once using these exact names: {', '.join(character_names)}. "
        )

    if "no humans" in raw_tags_set:
        short_rules.append("Write a compact, information-dense short caption. ")
    else:
        short_rules.append(
            "Write a compact, information-dense short caption. Prioritize the "
            "main subjects, their defining appearance, central action or pose, "
            "and the one or two spatial relationships needed to understand the scene. "
        )

    short_rules.extend(
        get_sentence_rules(
            character_count=len(character_names),
            simple_back=simple_back,
            has_multi=has_multi,
            raw_tags_set=raw_tags_set,
        )
    )

    short_rules.append(
        "Use one paragraph and usually 40-70 words. Never exceed 85 words unless "
        "that is strictly necessary to name every required character or transcribe "
        "visible text; even then, use the fewest words possible. Prefer 1-2 natural "
        "sentences for simple scenes. Treat the supplied long caption, when present, "
        "as a scene reference and summarize it without contradicting or introducing "
        "facts. Include only the scene's essential facts: the main subjects, defining "
        "visible traits, central action or pose, and setting when it changes the "
        "meaning. Omit secondary clothing details, minor props, decorative background "
        "elements, lighting nuances, and additional spatial relations unless they are "
        "needed to distinguish the scene. Mention visible text only when it is central "
        "to the scene. Retain only the tag concepts needed to identify the primary "
        "action and subjects. When the ground-truth tags contain explicit content, state "
        "the exact central NSFW act, exposed anatomy, and involved characters directly "
        "before spending words on clothing or background; these facts must not be "
        "omitted, generalized, or hedged. Avoid compressed adjective chains and "
        "comma-separated inventories. Do not "
        "begin with 'The image' or 'This image'. "
    )

    long_rules = [
        (
            "In this long caption, name every authorized character at least "
            f"once using these exact names: {', '.join(character_names)}. "
            if character_names
            else ""
        ),
        "Write the long caption as the exhaustive, reconstruction-oriented source "
        "description. It must cover the full scene rather than summarize it. Write in "
        "connected natural prose. The goal is to let a reader or generative model "
        "reconstruct the composition as closely as possible from the caption. "
        "Begin with a concise map of the whole composition: camera angle and "
        "framing, foreground/midground/background, and what occupies the left, "
        "center, right, top, and bottom of the frame. Then cover each important "
        "character's identity or generic designation, defining appearance, "
        "clothing, expression, gaze, pose, actions, held objects, interactions, "
        "background, lighting, and relevant visible text. Focus on details that "
        "distinguish this scene rather than exhaustively verbalizing every tag. ",
        "For every visible person or character, state their frame position, depth, "
        "facing direction, body orientation, posture, gaze, limb and hand placement, "
        "and position relative to other subjects and nearby objects. Explicitly "
        "describe contact, overlap, occlusion, containment, support, and who or what "
        "is in front of, behind, above, below, beside, between, inside, or touching "
        "something else. Use viewer-relative left and right for image placement; "
        "use a subject's left or right only when anatomy requires it and label it "
        "clearly. Do not invent exact measurements, but use approximate regions, "
        "distances, scale, and relative size when visible. ",
        "Describe every clearly visible scene element that materially affects "
        "reconstruction, including furniture, props, architecture, landscape, "
        "decorations, effects, shadows, reflections, and visible text. Anchor each "
        "item to an image region or nearby subject instead of listing objects "
        "without locations. State important colors, shapes, orientations, counts, "
        "patterns, and partially obscured or cropped elements. Explain empty space "
        "and background layout when those define the composition. ",
        "Give each subject enough individual detail to reproduce their appearance "
        "and role, including hair, face, expression, body traits, clothing layers, "
        "accessories, and held objects, while keeping each detail attached to its "
        "owner. Scale the caption to the scene, but omit a visible detail only when "
        "it is genuinely too small or ambiguous to describe reliably. Before "
        "finishing, mentally scan the frame from top-left to bottom-right and add "
        "any clearly visible person, item, spatial relation, crop, or background "
        "feature not yet covered. ",
        "Describe every supported explicit anatomical and sexual detail from "
        "the Torii report and ground-truth tags, including who does what to "
        "whom and the relevant body parts, poses, contact, fluids, and objects. ",
        "State confirmed ground-truth acts early and concretely before less "
        "important atmosphere, lighting, clothing, or background details. ",
        "Use natural paragraphs or one long paragraph. Avoid repetition, "
        "speculation, tag-list phrasing, and serial adjective inventories. ",
        "Do not begin with 'The image' or 'This image'. ",
    ]

    return {
        "short": shared_context + "\n# Task\n" + "".join(short_rules),
        "long": shared_context + "\n# Task\n" + "".join(long_rules),
    }


# -------------------------------------------------
# Core Logic
# -------------------------------------------------
def build_gemma_prompts(
    image_path: Path,
    char_explanations: dict[str, str],
    char_explanations_fallback: dict[str, str],
    torii_output: str,
) -> dict[str, str] | None:
    tag_file = image_path.with_suffix(".txt")

    torii_content = sanitize_torii_output(torii_output)

    raw_tags = []
    if tag_file.exists():
        raw_tags = [
            t.strip()
            for t in tag_file.read_text(encoding="utf-8").split(",")
            if t.strip()
        ]

    if not raw_tags and not torii_content:
        return None

    raw_tags_normalized = [normalize_tag(t) for t in raw_tags]
    raw_tags_set = set(raw_tags_normalized)

    character_names, character_descriptions = load_character_data(
        image_path=image_path,
        char_explanations=char_explanations,
        char_explanations_fallback=char_explanations_fallback,
    )
    franchise = get_local_franchise(image_path)

    has_multi = any(tag in raw_tags_set for tag in MULTI_CHARACTER_TAGS)
    has_text = detect_text(raw_tags_set)
    simple_back = any(tag in raw_tags_set for tag in SIMPLE_BACKGROUND_TAGS)

    if not torii_content:
        torii_content = "No Torii report is available; use the supplied image directly."

    prompts = build_caption_prompts(
        raw_tags=raw_tags,
        raw_tags_set=raw_tags_set,
        character_names=character_names,
        character_descriptions=character_descriptions,
        simple_back=simple_back,
        has_multi=has_multi,
        has_text=has_text,
        franchise=franchise,
    )
    return {
        caption_kind: prompt.replace("{torii_output}", torii_content)
        for caption_kind, prompt in prompts.items()
    }


# -------------------------------------------------
# Main
# -------------------------------------------------
if __name__ == "__main__":
    if not IMAGES_DIR.exists():
        raise FileNotFoundError(f"Images folder not found: {IMAGES_DIR}")

    char_explanations = load_character_explanations(CHARACTER_DB)
    char_explanations_fallback = load_wiki_explanations(WIKI_CACHE_DB)

    image_files = [
        img for img in IMAGES_DIR.iterdir()
        if img.suffix.lower() in SUPPORTED_EXTENSIONS
    ]

    print("Validating Gemma prompts...")

    results = Counter()

    for image in tqdm(image_files, desc="Processing images", unit="image"):
        torii_file = image.with_suffix(".toriiOutput")
        torii_output = (
            torii_file.read_text(encoding="utf-8").strip()
            if torii_file.exists()
            else ""
        )
        prompts = build_gemma_prompts(
            image,
            char_explanations,
            char_explanations_fallback,
            torii_output,
        )
        results["available" if prompts else "unavailable"] += 1

    created_count = results["available"]
    skipped_count = results["unavailable"]

    print(f"Done! {created_count} Gemma prompt(s) can be generated in memory; skipped {skipped_count}.")

    if skipped_count:
        print("Skip reasons:")
        for reason, count in sorted(results.items()):
            if reason != "available":
                print(f"  {reason}: {count}")
