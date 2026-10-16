"""Gestione utenti da riga di comando.

Esempi:
    python manage_users.py list
    python manage_users.py credits mario@example.com 10
    python manage_users.py admin mario@example.com on
"""

import argparse
import sys

from services import auth_service


def main() -> int:
    parser = argparse.ArgumentParser(description="Gestione utenti e crediti di CV Architect.")
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("list", help="Elenca gli utenti.")
    credits = sub.add_parser("credits", help="Imposta i crediti di un utente.")
    credits.add_argument("email")
    credits.add_argument("amount", type=int)
    admin = sub.add_parser("admin", help="Attiva o disattiva i permessi di admin.")
    admin.add_argument("email")
    admin.add_argument("state", choices=["on", "off"])
    args = parser.parse_args()

    auth_service.init_db()
    try:
        if args.command == "list":
            for u in auth_service.list_users():
                role = "admin" if u.is_admin else f"{u.credits} crediti"
                print(f"{u.id:>4}  {u.email:<40} {role:<12} {u.created_at}")
        elif args.command == "credits":
            if args.amount < 0:
                parser.error("I crediti non possono essere negativi.")
            auth_service.set_credits(args.email, args.amount)
            print(f"Crediti di {args.email}: {args.amount}")
        else:
            auth_service.set_admin(args.email, args.state == "on")
            print(f"Admin {args.state} per {args.email}")
    except auth_service.AuthError as exc:
        print(exc, file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
