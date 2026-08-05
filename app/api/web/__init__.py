"""Routes HTML rendues par Jinja2 (section 11).

L'instance `templates` est partagée par tous les routers web.
"""

from pathlib import Path

from fastapi.templating import Jinja2Templates

TEMPLATES_DIR = Path(__file__).resolve().parents[2] / "templates"

templates = Jinja2Templates(directory=str(TEMPLATES_DIR))
