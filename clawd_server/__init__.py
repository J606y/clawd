"""clawd panel service. Entry: clawd_server.__main__.main(argv)."""

__all__ = ["main"]


def main(argv: list[str] | None = None) -> int:
    from clawd_server.__main__ import main as _main

    return _main(argv)
