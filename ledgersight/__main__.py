"""LedgerSight entry point.

    ledgersight              → TUI (interactive wizard)
    ledgersight --cli ...    → headless CLI mode
"""

import sys


def main() -> None:
    if "--cli" in sys.argv:
        sys.argv.remove("--cli")
        from ledgersight.business.cli import main as cli_main

        cli_main()
    else:
        from ledgersight.tui.app import run

        run()


if __name__ == "__main__":
    main()
