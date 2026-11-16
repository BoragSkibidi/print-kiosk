import os
import tomllib
from pathlib import Path


def load_config():
    path = Path(os.environ.get("PISO_CONFIG", "config.toml"))
    with open(path, "rb") as f:
        cfg = tomllib.load(f)
    for kind, rule in cfg["price"].items():
        if rule["pesos"] <= 0 or rule["pages"] <= 0:
            raise ValueError(f"price.{kind}: pesos and pages must be > 0")
    return cfg
