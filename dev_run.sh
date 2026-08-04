#!/usr/bin/env bash
set -e
cd "$(dirname "$0")"

# Activate venv if present
if [ -d venv ] && [ -f venv/bin/activate ]; then
    source venv/bin/activate
fi

# Ensure ledgerSight is installed in dev mode if not already
if ! python -c "import ledgersight" 2>/dev/null; then
    echo "==> Installing ledgersight in dev mode..."
    pip install -e ".[dev]"
    echo
fi

case "${1:-}" in
    --tui|tui)
        shift
        python -m ledgersight "$@"
        ;;
    --cli|cli)
        shift
        python -m ledgersight --cli "$@"
        ;;
    --test|test)
        shift
        python -m pytest tests/ -v "$@"
        ;;
    --lint|lint)
        echo "==> ruff check..."
        python -m ruff check ledgersight/ tests/ "$@"
        echo
        echo "==> ruff format --check..."
        python -m ruff format --check ledgersight/ tests/ "$@"
        echo
        echo "==> mypy..."
        python -m mypy ledgersight/ "$@"
        ;;
    --fmt|fmt)
        python -m ruff check --fix ledgersight/ tests/
        python -m ruff format ledgersight/ tests/
        ;;
    --install|install)
        pip install -e ".[dev]"
        ;;
    -h|--help|help)
        echo "Usage: ./dev_run.sh [COMMAND] [ARGS...]"
        echo
        echo "Commands:"
        echo "  --tui       Launch the TUI (default)"
        echo "  --cli       Run in headless CLI mode"
        echo "  --test      Run pytest"
        echo "  --lint      Run ruff + mypy checks"
        echo "  --fmt       Auto-format with ruff"
        echo "  --install   Install in dev mode"
        echo "  --help      Show this help"
        echo
        echo "Any unrecognized argument runs the TUI by default."
        ;;
    *)
        python -m ledgersight "$@"
        ;;
esac
