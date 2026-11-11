"""Command line entry point.

    instamap                 start the web app (default) and open it in your browser
    instamap login           log in to Instagram in a browser window
    instamap collections     list your saved collections
    instamap run NAME        headless: sync collection NAME, analyse everything, then exit
"""

from __future__ import annotations

import argparse
import logging
import sys
import threading
import webbrowser

from .config import settings


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="instamap", description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest="cmd")
    serve = sub.add_parser("serve", help="start the web app (default)")
    serve.add_argument("--no-browser", action="store_true", help="don't open a browser tab")
    sub.add_parser("login", help="log in to Instagram")
    sub.add_parser("collections", help="list saved collections")
    run = sub.add_parser("run", help="sync and analyse one collection without the web app")
    run.add_argument("collection", help='collection name, or "all" for every saved post')
    parser.add_argument("-v", "--verbose", action="store_true")
    args = parser.parse_args(argv)

    logging.basicConfig(level=logging.DEBUG if args.verbose else logging.INFO,
                        format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    settings.ensure_dirs()

    if args.cmd == "login":
        from .instagram import browser_login
        browser_login(settings.browser_profile, settings.session_file)
        print("Logged in. Session saved to", settings.session_file)
        return 0

    if args.cmd in ("collections", "run"):
        from .db import Database
        from .worker import Worker
        worker = Worker(Database(settings.db_path), settings)
        cols = worker.client().collections()
        if args.cmd == "collections":
            for c in cols:
                print(f"{c['name']}  ({c['count'] if c['count'] is not None else '?'} posts)  id={c['id']}")
            return 0
        wanted = args.collection.lower()
        match = next((c for c in cols if c["id"] == wanted or (c["name"] or "").lower() == wanted
                      or (wanted == "all" and c["id"] == "all")), None)
        if not match:
            print("No collection called", args.collection, file=sys.stderr)
            return 1
        worker.db.set("collection", match)
        worker.db.reset_stuck_posts()
        print(f"{worker.sync()} new posts")
        worker.process_queue()
        print(worker.db.post_counts())
        return 0

    import uvicorn
    from .server import create_app
    url = f"http://{settings.host}:{settings.port}"
    if not getattr(args, "no_browser", False):
        threading.Timer(1.5, lambda: webbrowser.open(url)).start()
    print(f"InstaMap running at {url}  (Ctrl+C to stop; analysis carries on while this is running)")
    uvicorn.run(create_app(), host=settings.host, port=settings.port, log_level="warning")
    return 0


if __name__ == "__main__":
    sys.exit(main())
