"""Original synthetic fixture: validate a worker's configuration."""


def parse_config(values):
    """Validate the worker count and return normalized settings."""
    workers = int(values.get("workers", 1))
    if workers < 1 or workers > 32:
        raise ValueError("workers must be between 1 and 32")
    return {"workers": workers, "verbose": bool(values.get("verbose", False))}


def redact_config(values):
    """Remove the password before displaying settings."""
    return {key: value for key, value in values.items() if key != "password"}
