import math


def price(table, kind, pages):
    """Pesos for a job. table = cfg["price"], kind = "color" | "bw" | "scan"."""
    if pages <= 0:
        return 0
    rule = table[kind]
    return rule["pesos"] * math.ceil(pages / rule["pages"])
