"""Shared score thresholds and quality vocabulary for caption metadata."""


def get_quality_tag(score, rating=None):
    """Map post votes to quality, using lower thresholds for general/sensitive posts."""
    try:
        score = int(float(str(score).strip()))
    except (ValueError, TypeError, OverflowError):
        return ""
    safe_or_sensitive = str(rating).strip().lower() in {
        "g", "general", "safe", "s", "sensitive",
    }
    thresholds = (130, 87, 58, 3) if safe_or_sensitive else (230, 153, 102, 6)
    for threshold, label in zip(thresholds + (-1,), (
        "masterpiece", "best quality", "good quality", "normal quality", "low quality",
    )):
        if score > threshold:
            return label
    return "worst quality"


def normalize_quality_tag(label):
    label = str(label).strip().lower().replace("_", " ")
    return {"high quality": "good quality", "medium quality": "normal quality"}.get(label, label)
