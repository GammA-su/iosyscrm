"""Entrypoint Typer des commandes d'administration (`crm`).

Les groupes sont déclarés ici ; les commandes elles-mêmes arrivent avec les
lots correspondants (T4 pour `sirene`, T5 pour `enrich`, T6 pour `score`).
"""

from typing import Annotated

import typer

from app.config import get_settings
from app.database.engine import session_scope
from app.logging import configure_logging
from app.models.collector import CollectorRun
from app.models.enums import USER_ROLE_VALUES
from app.models.user import User
from app.repositories import collector as collector_repo
from app.repositories import company as company_repo
from app.repositories import enrichment as enrichment_repo
from app.repositories import user as user_repo
from app.services import auth
from app.services.enrichment.orchestrator import EnrichmentOrchestrator
from app.services.scoring import load_rules, rescore_all, rescore_company
from app.services.sirene.client import SireneClient
from app.services.sirene.collector import (
    LAST_DISPOSITION_WATERMARK,
    LAST_TRAITEMENT_WATERMARK,
    SireneCollector,
)

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


def _echo_collector_run(run: CollectorRun) -> None:
    typer.echo(
        f"Run {run.id} {run.status} — vus={run.records_seen}, "
        f"nouveaux={run.records_new}, mis à jour={run.records_updated}, "
        f"rejetés={run.records_rejected}, appels API={run.api_calls}"
    )


@sirene_app.command("sync")
def sirene_sync() -> None:
    """Lance la synchronisation incrémentale pilotée par les watermarks."""
    settings = get_settings()
    with SireneClient(settings) as client, session_scope() as db:
        run = SireneCollector(settings, client=client).sync(db)
    _echo_collector_run(run)


@sirene_app.command("backfill")
def sirene_backfill(
    days: Annotated[
        int,
        typer.Option("--days", min=1, help="Nombre de jours de créations à recharger."),
    ] = 30,
) -> None:
    """Recharge une fenêtre de créations sans modifier les watermarks."""
    settings = get_settings()
    with SireneClient(settings) as client, session_scope() as db:
        run = SireneCollector(settings, client=client).backfill(db, days)
    _echo_collector_run(run)


@sirene_app.command("status")
def sirene_status() -> None:
    """Affiche les deux watermarks et les dix derniers runs."""
    with session_scope() as db:
        watermarks = {
            LAST_TRAITEMENT_WATERMARK: collector_repo.get_watermark(db, LAST_TRAITEMENT_WATERMARK),
            LAST_DISPOSITION_WATERMARK: collector_repo.get_watermark(
                db, LAST_DISPOSITION_WATERMARK
            ),
        }
        runs = collector_repo.list_runs(db, limit=10)

    typer.echo("Watermarks")
    for key, value in watermarks.items():
        typer.echo(f"  {key:<28} {value or '-'}")

    typer.echo("\n10 derniers runs")
    typer.echo(
        f"{'ID':>6}  {'TYPE':<20} {'STATUT':<8} {'VUS':>8} "
        f"{'NOUVEAUX':>9} {'MAJ':>8} {'REJETES':>8} {'API':>5}  FIN"
    )
    for run in runs:
        finished_at = run.finished_at.isoformat() if run.finished_at else "-"
        typer.echo(
            f"{run.id:>6}  {run.kind:<20} {run.status:<8} {run.records_seen:>8} "
            f"{run.records_new:>9} {run.records_updated:>8} {run.records_rejected:>8} "
            f"{run.api_calls:>5}  {finished_at}"
        )


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


@score_app.command("rebuild")
def score_rebuild(
    batch_size: Annotated[
        int,
        typer.Option("--batch-size", min=1, help="Nombre d'entreprises par lot."),
    ] = 1000,
) -> None:
    """Recalcule le score de toutes les entreprises."""
    with session_scope() as db:
        summary = rescore_all(db, batch_size=batch_size)

    typer.echo(
        f"{summary.companies} entreprise(s) scorée(s) — jeu de règles {summary.ruleset_hash}"
    )


@score_app.command("explain")
def score_explain(
    siren: Annotated[str, typer.Option("--siren", help="SIREN de l'entreprise.")],
) -> None:
    """Détaille le score d'une entreprise, règle par règle."""
    with session_scope() as db:
        company = company_repo.get_by_siren(db, siren.strip())
        if company is None:
            typer.echo(f"Aucune entreprise pour le SIREN {siren}.", err=True)
            raise typer.Exit(code=1)

        rules = {rule.key: rule for rule in load_rules(db)}
        result = rescore_company(db, company.id)
        db.rollback()

        name = company.denomination or company.nom_complet or "-"

    if result is None:
        typer.echo(f"Aucun contexte de scoring pour le SIREN {siren}.", err=True)
        raise typer.Exit(code=1)

    typer.echo(f"{siren} — {name}")
    typer.echo(f"Score : {result.score}/100")
    if not result.has_enrichment_data:
        typer.echo("Aucun fait d'enrichissement : entreprise jamais analysée.")

    typer.echo(f"\n{'RÈGLE':<18} {'PRÉDICAT':<16} {'POINTS':>7}  PARAMÈTRES")
    for key, points in sorted(result.breakdown.items(), key=lambda item: -item[1]):
        rule = rules[key]
        typer.echo(f"{key:<18} {rule.predicate:<16} {points:>+7}  {rule.params}")
    if not result.breakdown:
        typer.echo("aucune règle déclenchée")


@enrich_app.callback()
def enrich_main() -> None:
    """Commandes d'enrichissement."""


@enrich_app.command("company")
def enrich_company(
    siren: Annotated[str, typer.Option("--siren", help="SIREN de l'entreprise à enrichir.")],
) -> None:
    """Enrichit une entreprise, fournisseur par fournisseur."""
    with session_scope() as db:
        company = company_repo.get_by_siren(db, siren.strip())
        if company is None:
            typer.echo(f"Aucune entreprise pour le SIREN {siren}.", err=True)
            raise typer.Exit(code=1)
        runs = EnrichmentOrchestrator().enrich_company(db, company)
        lines = [f"  {run.provider:<16} {run.status:<8} faits={run.facts_written}" for run in runs]

    typer.echo(f"SIREN {siren} — {len(lines)} fournisseur(s) exécuté(s)")
    for line in lines:
        typer.echo(line)


@enrich_app.command("batch")
def enrich_batch(
    size: Annotated[
        int | None,
        typer.Option("--size", min=1, help="Nombre d'entreprises à traiter."),
    ] = None,
) -> None:
    """Traite un lot d'entreprises selon la priorité de la section 6.6."""
    with session_scope() as db:
        summary = EnrichmentOrchestrator().enrich_batch(db, size)

    typer.echo(
        f"Lot terminé — sélectionnées={summary.selected}, "
        f"exécutions réussies={summary.succeeded}, en échec={summary.failed}"
    )


@enrich_app.command("stats")
def enrich_stats() -> None:
    """Affiche l'état de l'enrichissement."""
    with session_scope() as db:
        by_field = enrichment_repo.count_facts_by_field(db)
        by_source = enrichment_repo.count_facts_by_source(db)
        expired = enrichment_repo.count_expired_facts(db)
        runs = enrichment_repo.list_runs(db, limit=10)
        run_lines = [
            f"{run.id:>6}  {run.provider:<16} {run.status:<8} faits={run.facts_written}"
            for run in runs
        ]

    typer.echo("Faits valides par champ")
    for field_name, count in by_field:
        typer.echo(f"  {field_name:<28} {count:>6}")
    if not by_field:
        typer.echo("  aucun fait valide")

    typer.echo("\nFaits valides par fournisseur")
    for source, count in by_source:
        typer.echo(f"  {source:<28} {count:>6}")

    typer.echo(f"\nFaits expirés : {expired}")

    typer.echo("\n10 dernières exécutions")
    for line in run_lines:
        typer.echo(line)


app.add_typer(sirene_app, name="sirene")
app.add_typer(users_app, name="users")
app.add_typer(score_app, name="score")
app.add_typer(enrich_app, name="enrich")


if __name__ == "__main__":  # pragma: no cover
    app()
