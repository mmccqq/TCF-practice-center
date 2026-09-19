#!/usr/bin/env python3
"""Grant or revoke admin on a user account.

    backend/.venv/bin/python backend/make_admin.py you@example.com
    backend/.venv/bin/python backend/make_admin.py you@example.com --revoke
    backend/.venv/bin/python backend/make_admin.py --list

    # against Neon, same as the other backend scripts
    DATABASE_URL='postgresql+psycopg://...' backend/.venv/bin/python backend/make_admin.py you@example.com

There is no way to do this from the web app on purpose: the first admin has to
come from somewhere, and an endpoint that promotes people is a bigger target
than a command that needs database access.
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

from app.db import SessionLocal          # noqa: E402
from app.models import User              # noqa: E402


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("email", nargs="?", help="the account to change")
    ap.add_argument("--revoke", action="store_true", help="take admin away")
    ap.add_argument("--list", action="store_true", help="show every account")
    args = ap.parse_args()

    with SessionLocal() as db:
        if args.list or not args.email:
            for u in db.query(User).order_by(User.id).all():
                print(f"  {'ADMIN' if u.is_admin else '     '}  {u.email}")
            if not args.email and not args.list:
                ap.exit(2, "\npass an email to change one, or --list\n")
            return

        u = db.query(User).filter(User.email == args.email.strip().lower()).first()
        if u is None:
            # exact match only: a fuzzy match here could promote the wrong person
            sys.exit(f"no account for {args.email!r}. Sign up first, then run this.")
        u.is_admin = not args.revoke
        db.commit()
        print(f"{u.email} is {'no longer an admin' if args.revoke else 'now an admin'}")


if __name__ == "__main__":
    main()
