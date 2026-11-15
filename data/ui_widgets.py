"""Shared safeguards for editable application controls."""


def disable_scroll_changes(widget):
    """Keep keyboard/click bindings, but let wheel events reach page scrolling."""
    original = widget.winfo_class()
    protected = f'NoWheelChanges.{original}'
    if not widget.bind_class(protected):
        for sequence in widget.bind_class(original):
            if any(part in sequence for part in ('MouseWheel', 'TouchpadScroll', 'Button-4', 'Button-5')):
                continue
            widget.bind_class(protected, sequence, widget.bind_class(original, sequence))
    widget.bindtags(tuple(protected if tag == original else tag for tag in widget.bindtags()))
