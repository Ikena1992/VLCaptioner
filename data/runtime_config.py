from pathlib import Path
import os


PROJECT_DIR = Path(__file__).parent.parent.resolve()
CONFIG_FILE = PROJECT_DIR / "config.txt"


def pipeline_images():
    """Allow a worker to process an isolated download batch."""
    return Path(os.environ.get("VLCAPTIONER_IMAGES", str(PROJECT_DIR / "images"))).resolve()


def read_settings(path):
    if not path.exists():
        raise FileNotFoundError(f"Configuration file not found: {path}")
    settings = {}
    for line_number, raw_line in enumerate(
        path.read_text(encoding="utf-8-sig").splitlines(), start=1
    ):
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        if "=" not in line:
            raise ValueError(f"Invalid line {line_number} in {path}: {raw_line}")
        key, value = line.split("=", 1)
        settings[key.strip().upper()] = value.strip()
    return settings


def load_setting(setting_name, default=None):
    value = read_settings(CONFIG_FILE).get(setting_name, default)
    if value is None or not str(value).strip():
        raise ValueError(f"Missing {setting_name} in {CONFIG_FILE}")
    return str(value).strip(), CONFIG_FILE


def load_positive_int(setting_name, default=None):
    value, config = load_setting(setting_name, default)
    try:
        result = int(value)
    except ValueError as error:
        raise ValueError(f"{setting_name} must be a positive integer") from error
    if result < 1:
        raise ValueError(f"{setting_name} must be at least 1")
    return result, config


def load_bool(setting_name, default="false"):
    value, config = load_setting(setting_name, default)
    if value.lower() not in {"true", "false", "1", "0", "yes", "no"}:
        raise ValueError(f"{setting_name} must be true or false")
    return value.lower() in {"true", "1", "yes"}, config


def refiner_defaults():
    """Keep the setup UI usable before config exists or while it is invalid."""
    try:
        return {"refiner_thinking": load_bool("REFINER_THINKING")[0],
                "refiner_json": load_bool("REFINER_JSON")[0]}
    except (OSError, ValueError):
        return {"refiner_thinking": False, "refiner_json": False}


def merge_batch_options(saved, selected):
    """Upgrade older batches with current defaults for newly introduced options."""
    if not isinstance(saved, dict):
        raise ValueError("Invalid saved caption options for this batch.")
    merged = dict(saved)
    for key in ("refiner_thinking", "refiner_json"):
        merged.setdefault(key, selected[key])
    if any(not isinstance(merged.get(key), bool) for key in selected):
        raise ValueError("Invalid saved caption options for this batch.")
    return {key: merged[key] for key in selected}
