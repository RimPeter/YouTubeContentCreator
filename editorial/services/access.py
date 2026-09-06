from contextlib import contextmanager

from django.db import transaction

from production.services.access import ensure_production_allowed


class EditorialServiceError(Exception):
    """Safe error suitable for display to an authenticated user."""

    def __init__(self, message, code="editorial_invalid"):
        self.code = code
        super().__init__(message)


def ensure_editorial_allowed(project, user):
    if not user or not user.is_active:
        raise EditorialServiceError("An active account is required for editorial work.", "editorial_not_allowed")
    try:
        ensure_production_allowed(project, user)
    except Exception as exc:
        raise EditorialServiceError(str(exc), "editorial_not_allowed") from exc


@contextmanager
def editorial_transaction(project_id, user, *, durable=False):
    """Serialize against project lifecycle changes before reading mutation inputs."""
    from scraper.services import lock_project

    with transaction.atomic(durable=durable):
        project = lock_project(project_id)
        ensure_editorial_allowed(project, user)
        yield project
