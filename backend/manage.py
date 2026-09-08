from __future__ import annotations

import argparse
import getpass

from sqlalchemy import select

from backend.app.database import SessionLocal, init_database
from backend.app.models import User
from backend.app.security import hash_password, normalize_email


def create_user(email: str, display_name: str, password: str) -> None:
    init_database()
    normalized_email = normalize_email(email)
    with SessionLocal() as db:
        if db.scalar(select(User).where(User.email == normalized_email)):
            raise SystemExit(f"Un compte existe déjà pour {normalized_email}")
        user = User(
            email=normalized_email,
            display_name=display_name.strip(),
            password_hash=hash_password(password),
        )
        db.add(user)
        db.commit()
        print(f"Compte créé: {user.email} ({user.id})")


def migrate() -> None:
    init_database()
    print("Base de données migrée.")


def main() -> None:
    parser = argparse.ArgumentParser(description="Dars Manager administration")
    subparsers = parser.add_subparsers(dest="command", required=True)
    create = subparsers.add_parser("create-user")
    create.add_argument("email")
    create.add_argument("--name", required=True)
    subparsers.add_parser("migrate")
    args = parser.parse_args()

    if args.command == "create-user":
        password = getpass.getpass("Mot de passe: ")
        confirmation = getpass.getpass("Confirmation: ")
        if password != confirmation:
            raise SystemExit("Les mots de passe ne correspondent pas")
        create_user(args.email, args.name, password)
    elif args.command == "migrate":
        migrate()


if __name__ == "__main__":
    main()
