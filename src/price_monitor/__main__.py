"""Allow ``python -m price_monitor``."""

from price_monitor.cli import app

if __name__ == "__main__":
    app(prog_name="price-monitor")
