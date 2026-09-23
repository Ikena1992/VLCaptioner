"""Shared score thresholds and quality vocabulary for caption metadata."""


def get_quality_tag(score):
    """Return no label when a score is missing or invalid."""
    try:
        score = int(float(str(score).strip()))
    except (ValueError, TypeError, OverflowError):
        return ""
    for threshold, label in (
        (180, "masterpiece"),
        (120, "best quality"),
        (80, "good quality"),
        (5, "normal quality"),
        (-1, "low quality"),
    ):
        if score > threshold:
            return label
    return "worst quality"


def normalize_quality_tag(label):
    label = str(label).strip().lower().replace("_", " ")
    return {"high quality": "good quality", "medium quality": "normal quality"}.get(label, label)
