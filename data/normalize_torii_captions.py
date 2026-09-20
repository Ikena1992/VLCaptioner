from pathlib import Path
from tqdm import tqdm

SCRIPT_DIR = Path(__file__).parent.resolve()
IMAGES_DIR = (SCRIPT_DIR / ".." / "images").resolve()

# Phrases to replace
replacements = {
    "A digital illustration of a": "A",
    "A digital illustration shows a": "A",
    "A close-up illustration shows a": "A",
    "A digital illustration shows ": "",
    "A digital illustration. ": "",
    "The image shows a": "A",
    "The image shows ": "",
    "Two characters are in the image.": "",
    "vaginal opening": "pussy",
    "her genitals visible": "her pussy",
    "her genitals": "her pussy",
    "her vagina": "her pussy",
    "vulva": "pussy",
    "vagina ": "pussy ",
    "vagina.": "pussy.",
    "vagina,": "pussy,",
    "circumcised penis": "penis",
    "semen": "cum",
    " His penis is erect and uncircumcised.": "",
    "her buttocks": "her ass",
    "a red, round object inside": "her cevix",
    "buttocks": "ass",
    "her exposed genitalia": "her pussy",
    "his erect, penis": "his penis",
    "circumcised, erect penis": "penis",
    "male genital": "penis",
    "female genital": "pussy",
    "her genitalia visible": "her pussy visible",
    "her genitalia": "her pussy",
    "A white liquid": "Cum",
    "white liquid cum": "cum",
    "white liquid": "cum",
    "White liquid": "Cum",
    "cum cum": "cum",
    "vaginal intercourse": "vaginal sex",
    "__CHARACTERS__": "",
    "The female character, identified as ": "",
    "anal intercourse": "anal sex",
    "vaginal area is open": "pussy is spread open",
    "If you want me to describe anything else or need more details, just say!": "",
    "If you want me to describe anything else or need more details, just ask!": "",
    '""': '"'
}

# Sort phrases by length (longest first) to prevent overlap issues
sorted_replacements = sorted(
    replacements.items(),
    key=lambda x: len(x[0]),
    reverse=True
)

txt_files = list(IMAGES_DIR.glob("*.toriiOutput"))

for filepath in tqdm(txt_files, desc="Processing files", unit="file"):
    content = filepath.read_text(encoding="utf-8")

    for old, new in sorted_replacements:
        content = content.replace(old, new)

    content = content.replace("{", "").replace("}", "")

    content = content.strip()

    cleaned_lines = []
    for line in content.splitlines():
        stripped_line = line.strip()
        if stripped_line in {">", "# <format>", "<format>", "</format>"}:
            continue
        if stripped_line.startswith("## # "):
            line = "# " + stripped_line[len("## # "):]
        cleaned_lines.append(line)
    content = "\n".join(cleaned_lines).strip()

    if content.startswith('"'):
        content = content[1:]
    if content.endswith('"'):
        content = content[:-1]

    filepath.write_text(content, encoding="utf-8")

print("Done.")
