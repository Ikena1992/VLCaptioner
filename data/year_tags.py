"""Year tag derived from a booru post's upload timestamp."""

from datetime import datetime


def get_year_tag(created_at):
    if not created_at:
        return ""
    try:
        value = str(created_at).strip()
        try:
            year = datetime.fromisoformat(value.replace("Z", "+00:00")).year
        except ValueError:
            year = datetime.strptime(value, "%a %b %d %H:%M:%S %z %Y").year
    except (TypeError, ValueError):
        return ""
    return f"year {year}"
