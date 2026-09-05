"""LedgerSight entry point.

ledgersight                        → TUI (interactive wizard, business)
ledgersight --profile personal     → TUI (personal report)
ledgersight --cli ...              → headless CLI mode (business)
ledgersight --cli --profile personal ... → headless CLI mode (personal)
"""

import sys

VALID_PROFILES = ("business", "personal")


def _parse_profile(argv: list[str]) -> str:
    """Extract and remove --profile <name>; default to business."""
    profile = "business"
    if "--profile" in argv:
        idx = argv.index("--profile")
        argv.pop(idx)
        if idx < len(argv):
            profile = argv.pop(idx)
        else:
            print("Error: --profile requires a value (business|personal).", file=sys.stderr)
            sys.exit(1)
    if profile not in VALID_PROFILES:
        print(f"Error: unknown profile '{profile}'. Valid: {', '.join(VALID_PROFILES)}", file=sys.stderr)
        sys.exit(1)
    return profile


def main() -> None:
    profile = _parse_profile(sys.argv)

    if "--cli" in sys.argv:
        sys.argv.remove("--cli")
        if profile == "personal":
            from ledgersight.personal.cli import main as cli_main
        else:
            from ledgersight.business.cli import main as cli_main

        cli_main()
    else:
        from ledgersight.tui.app import run

        run(profile=profile)


if __name__ == "__main__":
    main()
