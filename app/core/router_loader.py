import importlib
import pkgutil
from pathlib import Path

from fastapi import FastAPI

from app.core.logging import get_logger

logger = get_logger(__name__)

APPS_PACKAGE = "app.apps"
APPS_DIR = Path(__file__).parent.parent / "apps"


def auto_register_routers(app: FastAPI) -> None:
    """
    Scans app/apps/* for router.py files and registers each router automatically.
    Each router.py must define:
        router = APIRouter()
        PREFIX = "/my-prefix"          # optional, defaults to /<folder-name>
        TAGS   = ["my-tag"]            # optional, defaults to [folder-name]
    """
    for pkg in pkgutil.iter_modules([str(APPS_DIR)]):
        module_path = f"{APPS_PACKAGE}.{pkg.name}.router"
        try:
            module = importlib.import_module(module_path)
        except ModuleNotFoundError:
            logger.debug("No router.py in apps/%s — skipping", pkg.name)
            continue

        router = getattr(module, "router", None)
        if router is None:
            logger.warning("apps/%s/router.py has no `router` object — skipping", pkg.name)
            continue

        prefix = getattr(module, "PREFIX", f"/{pkg.name}")
        tags = getattr(module, "TAGS", [pkg.name])

        app.include_router(router, prefix=prefix, tags=tags)
        logger.info("Registered router: %s  →  %s", pkg.name, prefix)
