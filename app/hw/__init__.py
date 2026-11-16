"""Pick mock or real hardware per part, from config [hardware].
Each part can be switched on its own as the parts arrive."""


def build(cfg):
    h = cfg["hardware"]
    if h["printer"] == "cups":
        from .real import CupsPrinter
        printer = CupsPrinter(cfg["printer"])
    elif h["printer"] == "file":
        from .mock import FilePrinter
        printer = FilePrinter(cfg["printer"])
    else:
        raise ValueError("hardware.printer must be 'file' or 'cups'")

    if h["scanner"] == "sane":
        from .real import SaneScanner
        scanner = SaneScanner(cfg["scanner"])
    elif h["scanner"] == "mock":
        from .mock import MockScanner
        scanner = MockScanner(cfg["scanner"])
    else:
        raise ValueError("hardware.scanner must be 'mock' or 'sane'")
    return printer, scanner


def start_coin_reader(cfg, on_coin):
    """gpio: real acceptor. mock: coins come from POST /api/dev/coin (kiosk keys 1/5/0)."""
    if cfg["hardware"]["coin"] == "gpio":
        from .real import GpioCoinReader
        return GpioCoinReader(cfg["coin"], on_coin)
    if cfg["hardware"]["coin"] != "mock":
        raise ValueError("hardware.coin must be 'mock' or 'gpio'")
    return None
