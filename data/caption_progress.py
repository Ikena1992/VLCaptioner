"""Readable progress shared by caption generation stages."""

def readable_duration(seconds):
    seconds = max(0, round(seconds))
    hours, remainder = divmod(seconds, 3600)
    minutes, seconds = divmod(remainder, 60)
    if hours:
        return f"{hours}h {minutes:02d}m"
    if minutes:
        return f"{minutes}m {seconds:02d}s"
    return f"{seconds}s"


def caption_progress(label, processed, total, elapsed, skipped=0, truncated=0):
    average = elapsed / processed if processed else 0
    remaining = average * max(0, total - processed)
    percent = 100 * processed / total if total else 100
    message = (
        f"{label}: {processed:,} of {total:,} images processed ({percent:.0f}%)"
        f" | Elapsed: {readable_duration(elapsed)}"
        f" | Estimated remaining: {readable_duration(remaining)}"
        f" | Average: {average:.1f}s per image"
    )
    if skipped:
        message += f" | Skipped: {skipped:,}"
    if truncated:
        message += f" | Truncated outputs: {truncated:,}"
    return message

