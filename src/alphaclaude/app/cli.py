"""CLI for the AlphaClaude FastAPI/Feishu application."""

from __future__ import annotations

from alphaclaude.paths import add_legacy_paths


def main() -> None:
    """Run the package application entrypoint."""
    add_legacy_paths()
    from alphaclaude.app.main import app
    import uvicorn

    uvicorn.run(app, host="0.0.0.0", port=8800, log_level="info")


if __name__ == "__main__":
    main()
