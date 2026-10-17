"""Compile pixel-coordinate image layouts into MOSS-Image prompts."""
import json
import math
from decimal import Decimal, InvalidOperation
from fractions import Fraction

PROMPT = """Describe the image as a MOSS-Image layout DRAFT. Return JSON only:
{"width":800,"height":400,"summary":{"meta":[],"canvas":"single","caption":"Overall scene and style"},
"items":[{"label":"object","caption":"Visible object","bbox":[0,0,100,100]}],
"bbox_coord_space":"abs","bbox_format":"xyxy"}
Set width and height to the inference preview dimensions specified below.
Boxes are [x_min,y_min,x_max,y_max] in PREVIEW pixels from the TOP LEFT.
Use only the shown preview coordinate space; Python scales boxes to the original
image after validation. x is within 0..width and y within 0..height. Do not center, normalize,
reorder or round coordinates: Python will convert them for the model prompt.
Describe overall composition, subjects, actions, setting, colors, lighting and style
in summary.caption. Optional summary fields: meta (medium/genre string array),
canvas (single, multi_panel, collage), palette (color string array).
Use a nonempty flat items array, labels object, character, or text. Every item needs
caption and bbox. Optional item fields: tags and palette (string arrays).
Text items require text containing exact visible wording, original script,
punctuation and line breaks. Do not translate, paraphrase, romanize, normalize or
invent unreadable wording. Describe typography, alignment, size, color and spacing
in caption. Exclude watermarks. Group coherent visible units; split only when
independent placement matters. Overlap and occlusion are allowed. All boxes use
the full canvas, including text on objects and elements in panels. Do not create
children, parent, id or nested coordinate systems. Boxes are placement annotations,
not drawn borders. Correct reference claims unsupported by visible evidence.
"""


def _read(text, *, exact_numbers=False):
    def unique(pairs):
        result = {}
        for key, value in pairs:
            if key in result:
                raise ValueError(f"Duplicate JSON key: {key}")
            result[key] = value
        return result
    def constant(value):
        raise ValueError(f"Invalid JSON number: {value}")
    try:
        return json.loads(text, object_pairs_hook=unique, parse_constant=constant,
                          parse_float=Decimal if exact_numbers else float)
    except RecursionError as error:
        raise ValueError("Layout JSON is nested too deeply") from error
    except InvalidOperation as error:
        raise ValueError("Invalid layout number") from error


def _fields(value, allowed, required):
    if not isinstance(value, dict) or set(value) - allowed or required - set(value):
        raise ValueError("Invalid layout fields")


def _text(value):
    if not isinstance(value, str) or not value.strip():
        raise ValueError("Layout descriptions and visible text must be nonempty strings")
    try:
        value.encode("utf-8")
    except UnicodeEncodeError as error:
        raise ValueError("Layout text contains invalid Unicode") from error


def _strings(value):
    if not isinstance(value, list):
        raise ValueError("Layout metadata must be string arrays")
    for item in value:
        _text(item)


def box_to_pixels(box, width, height, *, coord_space="abs", bbox_format="xyxy",
                  preview_size=None):
    """Validate edges before rounding; return an enclosing original-pixel xyxy box.

    Coordinates describe pixel edges: the full canvas is [0, 0, width, height],
    not width-1 / height-1. Exact decimal ratios prevent floating-point scaling
    from adding a pixel at integral edges or collapsing very narrow boxes.
    """
    if any(type(n) is not int or n <= 0 for n in (width, height)):
        raise ValueError("Canvas dimensions must be positive integers")
    if (not isinstance(box, list) or len(box) != 4
            or any(type(n) not in (int, float, Decimal)
                   or (type(n) is float and not math.isfinite(n))
                   or (type(n) is Decimal and not n.is_finite()) for n in box)):
        raise ValueError("Boxes require four finite numbers")
    a, b, c, d = box
    if bbox_format == "xyxy":
        x1, y1, x2, y2 = a, b, c, d
    elif bbox_format == "yxyx":
        y1, x1, y2, x2 = a, b, c, d
    else:
        raise ValueError("Unknown box coordinate order")
    if coord_space == "abs_centralized":
        pw, ph = width, height
        valid = (-Fraction(width, 2) <= x1 < x2 <= Fraction(width, 2)
                 and -Fraction(height, 2) <= y1 < y2 <= Fraction(height, 2))
    elif coord_space == "abs":
        if preview_size is None:
            pw, ph = width, height
        else:
            if (not isinstance(preview_size, (tuple, list)) or len(preview_size) != 2
                    or any(type(n) is not int or n <= 0 for n in preview_size)):
                raise ValueError("Preview dimensions must be positive integers")
            pw, ph = preview_size
        valid = 0 <= x1 < x2 <= pw and 0 <= y1 < y2 <= ph
    else:
        raise ValueError("Unknown box coordinate space")
    if not valid:
        raise ValueError("Layout box is reversed, empty or outside the canvas")
    x1, y1, x2, y2 = (Fraction(str(n)) for n in (x1, y1, x2, y2))
    if coord_space == "abs_centralized":
        x1, x2 = x1 + Fraction(width, 2), x2 + Fraction(width, 2)
        y1, y2 = y1 + Fraction(height, 2), y2 + Fraction(height, 2)
    return (math.floor(x1 * width / pw), math.floor(y1 * height / ph),
            math.ceil(x2 * width / pw), math.ceil(y2 * height / ph))


def _validate(value, width, height, draft=False):
    if any(type(n) is not int or n <= 0 for n in (width, height)):
        raise ValueError("Canvas dimensions must be positive integers")
    root = {"summary", "items", "bbox_coord_space", "bbox_format"}
    if draft:
        root |= {"width", "height"}
    _fields(value, root, root)
    expected = ("abs", "xyxy") if draft else ("abs_centralized", "yxyx")
    if (value["bbox_coord_space"], value["bbox_format"]) != expected:
        raise ValueError(f"Expected {expected[0]} / {expected[1]} coordinates")
    if draft and (type(value["width"]) is not int or type(value["height"]) is not int
                  or value["width"] != width or value["height"] != height):
        raise ValueError("Draft dimensions must match the inference canvas")
    summary = value["summary"]
    _fields(summary, {"caption", "meta", "canvas", "palette"}, {"caption"})
    _text(summary["caption"])
    if "canvas" in summary and summary["canvas"] not in ("single", "multi_panel", "collage"):
        raise ValueError("Invalid canvas type")
    for key in ("meta", "palette"):
        if key in summary:
            _strings(summary[key])
    if not isinstance(value["items"], list) or not value["items"]:
        raise ValueError("Layout items must be a nonempty flat array")
    for item in value["items"]:
        _fields(item, {"label", "caption", "bbox", "text", "tags", "palette"}, {"label", "caption", "bbox"})
        if item["label"] not in ("object", "character", "text"):
            raise ValueError("Invalid layout label")
        _text(item["caption"])
        if item["label"] == "text" and "text" not in item:
            raise ValueError("Text items require literal text")
        if "text" in item:
            _text(item["text"])
        for key in ("tags", "palette"):
            if key in item:
                _strings(item[key])
        box_to_pixels(item["bbox"], width, height, coord_space=expected[0],
                      bbox_format=expected[1])
    return value


def _dump(value):
    """Serialize validated JSON while retaining exact decimal coordinates."""
    if isinstance(value, Decimal):
        return str(value)
    if isinstance(value, dict):
        return "{" + ",".join(json.dumps(key, ensure_ascii=False) + ":" + _dump(item)
                              for key, item in value.items()) + "}"
    if isinstance(value, list):
        return "[" + ",".join(_dump(item) for item in value) + "]"
    return json.dumps(value, ensure_ascii=False, allow_nan=False, separators=(",", ":"))


def validate_json(text, width, height):
    """Validate compiled sidecars/cache entries without changing literal text."""
    return _dump(_validate(_read(text, exact_numbers=True), width, height))


def compile_layout(text, width, height, *, preview_size=None):
    """Scale preview xyxy pixels to the original oriented canvas, then center.

    Older direct callers can omit preview_size for original-pixel drafts.
    Separate x/y factors account for integer thumbnail dimension rounding.
    """
    if any(type(n) is not int or n <= 0 for n in (width, height)):
        raise ValueError("Canvas dimensions must be positive integers")
    if preview_size is None:
        preview_size = (width, height)
    if (not isinstance(preview_size, (tuple, list)) or len(preview_size) != 2
            or any(type(n) is not int or n <= 0 for n in preview_size)):
        raise ValueError("Preview dimensions must be positive integers")
    pw, ph = preview_size
    value = _validate(_read(text, exact_numbers=True), pw, ph, draft=True)
    for item in value["items"]:
        x1, y1, x2, y2 = box_to_pixels(item["bbox"], width, height,
                                      preview_size=preview_size)
        item["bbox"] = [y1 - height/2, x1 - width/2, y2 - height/2, x2 - width/2]
        item["bbox"] = [int(n) if n.is_integer() else n for n in item["bbox"]]
    del value["width"], value["height"]
    value["bbox_coord_space"] = "abs_centralized"
    value["bbox_format"] = "yxyx"
    return validate_json(json.dumps(value, ensure_ascii=False), width, height)


def write_sidecar(destination, text, *, overwrite=False):
    """Publish complete JSON atomically; replace existing files only on request."""
    import os
    import tempfile
    from pathlib import Path
    destination = Path(destination)
    fd, name = tempfile.mkstemp(prefix=".layout-", suffix=".tmp", dir=destination.parent)
    temporary = Path(name)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as output:
            output.write(text + "\n")
            output.flush()
            os.fsync(output.fileno())
        if overwrite:
            os.replace(temporary, destination)
            return
        try:
            # Windows rename refuses existing targets; POSIX rename replaces
            # them, so use an exclusive hard link on POSIX instead.
            if os.name == "nt":
                os.rename(temporary, destination)
            else:
                os.link(temporary, destination)
        except FileExistsError:
            pass
    finally:
        temporary.unlink(missing_ok=True)
