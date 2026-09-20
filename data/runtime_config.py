from pathlib import Path


PROJECT_DIR = Path(__file__).parent.parent.resolve()
CONFIG_FILE = PROJECT_DIR / "config.txt"


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
