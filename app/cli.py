"""Entrypoint Typer des commandes d'administration (`crm`).

Les groupes sont déclarés ici ; les commandes elles-mêmes arrivent avec les
lots correspondants (T2 pour `users`, T4 pour `sirene`, T5 pour `enrich`,
T6 pour `score`).
"""

import typer

from app.config import get_settings
from app.logging import configure_logging

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
