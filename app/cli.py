"""Entrypoint Typer des commandes d'administration (`crm`).

Les groupes sont déclarés ici ; les commandes elles-mêmes arrivent avec les
lots correspondants (T4 pour `sirene`, T5 pour `enrich`, T6 pour `score`).
"""

from typing import Annotated

import typer

from app.config import get_settings
from app.database.engine import session_scope
from app.logging import configure_logging
from app.models.enums import USER_ROLE_VALUES
from app.models.user import User
from app.repositories import user as user_repo
from app.services import auth

app = typer.Typer(
    name="crm",
    help="Administration du CRM de prospection IOSYS.",
    no_args_is_help=True,
)

sirene_app = typer.Typer(help="Collecte SIRENE.", no_args_is_help=True)
users_app = typer.Typer(help="Gestion des utilisateurs.", no_args_is_help=True)
score_app = typer.Typer(help="Moteur de scoring.", no_args_is_help=True)
enrich_app = typer.Typer(help="Enrichissement des entreprises.", no_args_is_help=True)


@app.callback()
def main() -> None:
    """Initialise la configuration et les logs pour toute commande."""
    configure_logging(get_settings())


@sirene_app.callback()
def sirene_main() -> None:
    """Commandes de collecte SIRENE."""


@users_app.callback()
def users_main() -> None:
    """Commandes de gestion des utilisateurs."""


def _resolve_role(role: str, admin: bool) -> str:
    """Rôle effectif d'un compte. `--admin` est le raccourci de la section 9."""
    effective = "admin" if admin else role
    if effective not in USER_ROLE_VALUES:
        raise typer.BadParameter(
            f"Rôle inconnu : {effective}. Valeurs possibles : {', '.join(USER_ROLE_VALUES)}."
        )
    return effective


def _prompt_password() -> str:
    """Demande un mot de passe en saisie masquée, avec confirmation."""
    password: str = typer.prompt("Mot de passe", hide_input=True, confirmation_prompt=True)
    if not password:
        raise typer.BadParameter("Le mot de passe ne peut pas être vide.")
    return password


@users_app.command("create")
def users_create(
    email: Annotated[str, typer.Option("--email", help="Adresse email de connexion.")],
    name: Annotated[str, typer.Option("--name", help="Nom complet affiché.")],
    role: Annotated[
        str, typer.Option("--role", help="admin | commercial | viewer.")
    ] = "commercial",
    admin: Annotated[bool, typer.Option("--admin", help="Raccourci pour --role admin.")] = False,
) -> None:
    """Crée un utilisateur, mot de passe demandé en saisie masquée."""
    effective_role = _resolve_role(role, admin)
    address = auth.normalize_email(email)

    with session_scope() as db:
        if user_repo.get_by_email(db, address) is not None:
            typer.echo(f"Un compte existe déjà pour {address}.", err=True)
            raise typer.Exit(code=1)

        password = _prompt_password()
        user_repo.add(
            db,
            User(
                email=address,
                password_hash=auth.hash_password(password),
                full_name=name,
                role=effective_role,
            ),
        )

    typer.echo(f"Utilisateur {address} créé avec le rôle {effective_role}.")


@users_app.command("list")
def users_list() -> None:
    """Liste les utilisateurs."""
    with session_scope() as db:
        users = user_repo.list_all(db)
        rows = [
            (
                user.email,
                user.full_name,
                user.role,
                "actif" if user.is_active else "désactivé",
                user.last_login_at.isoformat() if user.last_login_at else "-",
            )
            for user in users
        ]

    if not rows:
        typer.echo("Aucun utilisateur. Créez-en un avec `crm users create`.")
        return

    for email, full_name, role, state, last_login in rows:
        typer.echo(f"{email:<32} {full_name:<24} {role:<12} {state:<10} {last_login}")


@users_app.command("passwd")
def users_passwd(
    email: Annotated[str, typer.Option("--email", help="Compte à modifier.")],
) -> None:
    """Change le mot de passe d'un utilisateur et révoque ses sessions."""
    address = auth.normalize_email(email)

    with session_scope() as db:
        user = user_repo.get_by_email(db, address)
        if user is None:
            typer.echo(f"Aucun compte pour {address}.", err=True)
            raise typer.Exit(code=1)

        password = _prompt_password()
        user.password_hash = auth.hash_password(password)
        revoked = auth.revoke_all_sessions_for_user(db, user)

    typer.echo(f"Mot de passe de {address} modifié. {revoked} session(s) révoquée(s).")


@users_app.command("deactivate")
def users_deactivate(
    email: Annotated[str, typer.Option("--email", help="Compte à désactiver.")],
) -> None:
    """Désactive un utilisateur et révoque ses sessions."""
    address = auth.normalize_email(email)

    with session_scope() as db:
        user = user_repo.get_by_email(db, address)
        if user is None:
            typer.echo(f"Aucun compte pour {address}.", err=True)
            raise typer.Exit(code=1)

        user.is_active = False
        revoked = auth.revoke_all_sessions_for_user(db, user)

    typer.echo(f"Compte {address} désactivé. {revoked} session(s) révoquée(s).")


@score_app.callback()
def score_main() -> None:
    """Commandes de calcul des scores."""


@enrich_app.callback()
def enrich_main() -> None:
    """Commandes d'enrichissement."""


app.add_typer(sirene_app, name="sirene")
app.add_typer(users_app, name="users")
app.add_typer(score_app, name="score")
app.add_typer(enrich_app, name="enrich")


if __name__ == "__main__":  # pragma: no cover
    app()
