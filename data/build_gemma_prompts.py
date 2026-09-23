from source_tag_file import read_source_tags
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
    "speech bubble",
    "thought bubble",
    "artist name",
    "character name",
    "copyright name",
    "company name",
    "commissioner name",
    "name tag",
    "sign",
    "holding sign",
    "road sign",
    "open sign",
    "sign around neck",
}

TEXT_WORDS = {
    "text",
    "writing",
    "username",
    "signature",
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
        or not TEXT_WORDS.isdisjoint(tag.split())
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
        "# Character references\n"
        "Use these descriptions to match names in <ground_truth_characters> "
        "to subjects in the image. Do not introduce other names from these "
        "references. Describe the hairstyle, clothing, accessories, and colors "
        "visible in the image, even when they differ from these descriptions.\n"
    ]

    for character_name, description in character_descriptions:
        lines.append(f"## {character_name}\n")
        lines.append(f"{description}\n\n")

    return "".join(lines)


def build_character_authority_policy(character_names: list[str]) -> str:
    if character_names:
        names = "\n".join(character_names)
        return (
            "# Character names\n"
            "This list is authoritative and contains every permitted "
            "character name. Include each name exactly as listed at least "
            "once in the caption. Use these names instead of generic terms "
            "for the corresponding subjects:\n"
            "<ground_truth_characters>\n"
            f"{names}\n"
            "</ground_truth_characters>\n"
            "Discard names in the Torii report that are not in this list. "
            "Use generic terms for subjects without a listed name. Match "
            "listed names to subjects using visible traits, spatial position, "
            "and character references when supplied; correct any mismatched "
            "names in the Torii report.\n\n"
        )

    return (
        "# Character names\n"
        "No character names are supplied for this image.\n"
        "<ground_truth_characters>\nNone\n</ground_truth_characters>\n"
        "Discard every character name in the Torii report and describe "
        "subjects using appropriate generic terms. Do not use character "
        "names in the caption.\n\n"
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
        "Write a factual scene description for training an image model. ",
        "Treat the supplied tags in <ground_truth_tags> as authoritative "
        "facts, not a complete list of scene details. A missing tag does "
        "not mean the corresponding detail is absent. "
        "Preserve the Torii report's descriptions of nudity, exposed anatomy, "
        "sexual poses and actions, penetration, bodily contact, fluids, sex "
        "toys, and restraints unless they contradict the tags. Do not remove "
        "these details just because you cannot recognize them in the image. "
        "Use the image to correct and expand the remaining scene details. ",
        "Describe details supported by the image, the tags, or the Torii "
        "report according to those priorities. Do not invent hidden anatomy, "
        "clothing, objects, or actions. Describe visible crops and overlaps "
        "without guessing what lies behind them. ",
        "Use direct, specific wording for actions and anatomy. State who does "
        "what to whom, including the body parts and contact specified by the "
        "tags or Torii report. Do not soften these facts, replace them with "
        "vague terms, or qualify them with 'seems', 'possibly', or 'suggests'. ",
        "Use the framing term 'close-up' only when the exact ground-truth tag "
        "'close-up' appears in <ground_truth_tags>. Otherwise, do not use "
        "that term, even if the image or Torii report suggests tight framing. ",
        "Write connected natural prose. Integrate relevant tags into sentences "
        "instead of listing keywords. Keep each attribute attached to the "
        "correct subject and describe spatial and physical relationships clearly. ",
        "State scene facts directly. Do not refer to the tags, metadata, "
        "character references, Torii report, instructions, or source priorities "
        "in the caption. Do not begin with 'The image' or 'This image'. ",
        "Do not call the scene an image, anime, artwork, or illustration. "
        "Omit quality labels such as 'masterpiece', '8k', or 'high-resolution', "
        "subjective praise, emotional interpretation, and flowery language. ",
        "Omit statements about absent text, speech bubbles, logos, props, or "
        "other elements, even if the Torii report includes them. Describe "
        "plain backgrounds and empty space by their visible color and layout. ",
        "Each sentence should add information. Omit repeated facts, concluding "
        "summaries, and statements that nothing else is present. ",
        "Output only the caption, followed by END_CAPTION, then stop. Do not "
        "include headings, bullet lists, Markdown, explanations, notes, or "
        "quotation marks around the whole caption. ",
    ]

    if has_multi or len(character_names) > 1:
        common_rules.append(
            "Distinguish characters by name or visible features and clearly "
            "describe their positions and interactions. "
        )

    if has_text:
        common_rules.append(
            "For writing included in the caption, transcribe clearly legible "
            "text exactly in double quotation marks and identify its location. "
            "For visible but unreadable writing, "
            "describe its placement and appearance without guessing the "
            "wording. When writing is absent, omit the topic entirely. Follow "
            "the writing coverage specified in the caption task below. "
        )

    if not character_names:
        if "furry" in raw_tags_set:
            common_rules.append("Refer to the unnamed character as a furry. ")
        if "loli" in raw_tags_set:
            common_rules.append(
                'The supplied tags include "loli"; use the exact word "loli". '
            )
        if "shota" in raw_tags_set:
            common_rules.append(
                'The supplied tags include "shota"; use the exact word "shota". '
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

    short_rules = [
        "Write one compact paragraph, usually 40-70 words. Never exceed 85 "
        "words unless required character names or visible text make that "
        "impossible. Include only the scene's essential facts: main subjects, "
        "defining appearance, central action or pose, and the setting and "
        "relationships needed to understand the scene. ",
    ]
    short_rules.extend(
        get_sentence_rules(
            character_count=len(character_names),
            simple_back=simple_back,
            has_multi=has_multi,
            raw_tags_set=raw_tags_set,
        )
    )
    short_rules.append(
        "If <long_caption> is supplied, summarize it according to the source "
        "priorities above without adding facts. Preserve the central actions, "
        "exposed anatomy, and involved subjects specified by the tags or Torii "
        "report; state them before secondary appearance or setting details. "
        "Omit secondary clothing details, minor props, decorations, lighting "
        "nuances, and extra spatial relations unless needed to distinguish the "
        "scene. Mention writing only when central to the scene. Use full "
        "sentences rather than compressed adjective chains. "
    )

    long_rules = [
        "Write a thorough, detailed description of the full scene in natural "
        "paragraphs. Include all supported details useful for reconstructing "
        "the subjects and composition. Let scene complexity determine length; "
        "do not compress the description into a summary. ",
        "Begin with the main subjects, central action or pose, camera angle, "
        "and framing. Locate subjects and objects across the frame and in "
        "depth where relevant. State actions specified by the tags or Torii "
        "report before secondary clothing, lighting, or background details. ",
        "Describe each subject's hair, face, body traits, clothing layers, "
        "accessories, expression, gaze, pose, body orientation, and limb and "
        "hand placement where supported. Keep each subject's details together "
        "and distinguish subjects by name or visible features. ",
        "Describe actions and relationships precisely: contact, overlap, "
        "occlusion, support, containment, and relative positions. Use the "
        "viewer's left and right for placement in the frame. If referring to "
        "a subject's own left or right, say so explicitly. Use visible relative "
        "sizes and distances without inventing exact measurements. ",
        "Describe held objects, furniture, architecture, landscape, decorations, "
        "effects, lighting, shadows, reflections, and visible writing where "
        "present. Locate each element relative to the frame or a nearby "
        "subject. Include its relevant colors, shapes, patterns, orientation, "
        "and count. Describe cropped and partly hidden elements only to the "
        "extent supported by the supplied sources. ",
        "Retain the specific anatomical details, actions, contact, fluids, "
        "and related objects supplied by the tags and Torii report according "
        "to the source priorities above. Omit details that are too small or "
        "ambiguous to identify and are not established by those sources. ",
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
            for t in read_source_tags(tag_file.read_text(encoding="utf-8")).split(",")
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
