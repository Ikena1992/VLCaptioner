from dataset_files import TRAINING_TEXT_EXTENSIONS
from file_io import atomic_path
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
    for extension in TRAINING_TEXT_EXTENSIONS
    for file_path in images_folder.glob(f"*{extension}")
]

with atomic_path(output_zip) as temporary:
    with zipfile.ZipFile(temporary, "w", zipfile.ZIP_DEFLATED) as zipf:
        for file_path in tqdm(files_to_zip, desc="Zipping training-caption files"):
            zipf.write(file_path, arcname=file_path.name)

print(f"\nCreated zip file: {output_zip}")
