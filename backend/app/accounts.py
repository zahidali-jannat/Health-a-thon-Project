"""Create the clinic team's shared sign-in (a desk login with no phone) on an existing database.

    python -m app.accounts add-team                       # named after the clinic (clinic_profile)
    python -m app.accounts add-team --name "Front desk"

Prints the Clinician ID and a one-time password ONCE - only its hash is stored. Change it in Settings -> Account.
Doctors and individual team members sign up themselves on the sign-in page.
"""

import argparse

from . import db, repo, security
from .settings import get_settings


def add_team(conn, name: str | None = None) -> tuple[dict, str]:
    password = security.new_temp_password()
    user = repo.create_clinician(conn, name or repo.clinic_name(conn), None, password, role="care_team")
    repo.audit(conn, repo.clinician_actor(user), "ACCOUNT_CREATED", resource_type="clinical_user", resource_id=user["id"],
               detail="Clinic team sign-in created from the command line")
    conn.commit()
    return user, password


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest="command", required=True)
    team = sub.add_parser("add-team", help="create a clinic-team sign-in")
    team.add_argument("--name", help="account name (default: the clinic's name)")
    args = parser.parse_args()
    conn = db.connect(get_settings().db_path)
    db.migrate(conn)
    user, password = add_team(conn, args.name)
    print(f"Clinic team sign-in created for {get_settings().db_path}\n")
    print(f"  Name           {user['full_name']}")
    print(f"  Clinician ID   {user['clinician_code']}")
    print(f"  Password       {password}   (shown once - change it in Settings -> Account)")


if __name__ == "__main__":
    main()
