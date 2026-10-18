"""Canonical image formats and companion-file policies for dataset tools."""

from pathlib import Path

# Ordered so tools that choose the first matching image agree on precedence.
IMAGE_EXTENSIONS = ('.png', '.jpg', '.jpeg', '.webp', '.gif', '.bmp', '.tif', '.tiff', '.avif')
IMAGE_FILE_TYPES = frozenset(extension[1:] for extension in IMAGE_EXTENSIONS)
CONVERSION_EXTENSIONS = tuple(extension for extension in IMAGE_EXTENSIONS if extension != '.webp')
JPEG_EXTENSIONS = ('.jpg', '.jpeg')
CAPTION_EXTENSIONS = ('.short', '.long', '.tag', '.combined', '.naturallanguage')
FINAL_COMPANION_EXTENSIONS = ('.txt', *CAPTION_EXTENSIONS, '.json', '.review.json')
INTERMEDIATE_EXTENSIONS = ('.csv', '.toriiOutput')
COMPANION_EXTENSIONS = (*FINAL_COMPANION_EXTENSIONS, *INTERMEDIATE_EXTENSIONS)
TRAINING_TEXT_EXTENSIONS = ('.combined', '.long', '.short', '.tag', '.txt')
_COMPANION_SUFFIXES = tuple(sorted(COMPANION_EXTENSIONS, key=len, reverse=True))


def is_image(path):
    return Path(path).suffix.lower() in IMAGE_EXTENSIONS


def companion_extension(path):
    """Recognize compound sidecars before their shorter suffixes."""
    name = Path(path).name.lower()
    return next((suffix for suffix in _COMPANION_SUFFIXES if name.endswith(suffix.lower())), None)


def bundle_stem(path):
    """Preserve dotted basenames while grouping NAME.review.json with NAME."""
    path = Path(path)
    suffix = companion_extension(path)
    return path.name[:-len(suffix)] if suffix else path.stem


def group_dataset_files(paths, *, extra_extensions=()):
    """Group images and recognized companions using case-insensitive keys."""
    paths = [Path(path) for path in paths]
    image_stems = {path.stem.casefold() for path in paths if is_image(path) and path.is_file()}
    extra_extensions = {extension.lower() for extension in extra_extensions}
    groups = {}
    for path in paths:
        if path.is_file() and (is_image(path) or companion_extension(path)
                               or path.suffix.lower() in extra_extensions):
            # NAME.review.json is a layout for NAME.review.webp when that
            # exact image exists; otherwise it is NAME's review companion.
            stem = path.stem if path.stem.casefold() in image_stems else bundle_stem(path)
            groups.setdefault(stem.casefold(), []).append(path)
    return groups


def companion_paths(image, extensions=COMPANION_EXTENSIONS):
    """Return existing sidecars for an image, in policy order."""
    image = Path(image)
    paths = []
    for extension in extensions:
        path = image.with_suffix(extension)
        if not path.is_file():
            continue
        if extension.count('.') > 1 and any(path.with_suffix(suffix).is_file() for suffix in IMAGE_EXTENSIONS):
            continue  # The compound filename is another image's own sidecar.
        paths.append(path)
    return paths


def is_source_tag_file(path):
    """Ignore generated caption variants such as NAME.long.txt in tag scans."""
    path = Path(path)
    return (path.suffix.lower() == '.txt'
            and not path.stem.lower().endswith(CAPTION_EXTENSIONS))
