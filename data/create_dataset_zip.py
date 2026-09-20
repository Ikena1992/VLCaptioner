import zipfile
from pathlib import Path
from tqdm import tqdm  # progress bar

# Paths
SCRIPT_DIR = Path(__file__).parent.resolve()
images_folder = (SCRIPT_DIR / ".." / "done").resolve()
output_zip = images_folder.parent / "naturalLanguage_files.zip"

# Find final training-caption files.
files_to_zip = [
    file_path
    for extension in ("combined", "long", "short", "tag", "txt")
    for file_path in images_folder.glob(f"*.{extension}")
]

with zipfile.ZipFile(output_zip, "w", zipfile.ZIP_DEFLATED) as zipf:
    for file_path in tqdm(files_to_zip, desc="Zipping training-caption files"):
        zipf.write(file_path, arcname=file_path.name)

print(f"\nCreated zip file: {output_zip}")
