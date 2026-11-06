import argparse

import uvicorn


def main() -> None:
    parser = argparse.ArgumentParser(prog="planthz", description="Run the planthz web application.")
    parser.add_argument("--port", type=int, default=8000)
    parser.add_argument("--reload", action="store_true", help="restart the server when the code changes")
    args = parser.parse_args()

    # Bound to localhost only: the application reads any folder the user types.
    uvicorn.run("planthz.web:app", host="127.0.0.1", port=args.port, reload=args.reload)
