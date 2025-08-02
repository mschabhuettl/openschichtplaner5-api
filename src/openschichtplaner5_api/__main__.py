"""Entry point for running the API server directly."""

import uvicorn
import argparse
from pathlib import Path
from .api import create_api


def main():
    """Main entry point for the API server."""
    parser = argparse.ArgumentParser(description="Run Schichtplaner5 API server")
    parser.add_argument("--dir", required=True, help="DBF directory path")
    parser.add_argument("--host", default="127.0.0.1", help="Host to bind to")
    parser.add_argument("--port", type=int, default=8000, help="Port to bind to")
    parser.add_argument("--reload", action="store_true", help="Enable auto-reload")

    args = parser.parse_args()

    app = create_api(Path(args.dir))

    uvicorn.run(
        app,
        host=args.host,
        port=args.port,
        reload=args.reload
    )


if __name__ == "__main__":
    main()