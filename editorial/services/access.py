from production.services.access import ensure_production_allowed


class EditorialServiceError(Exception):
    """Safe error suitable for display to an authenticated user."""

    def __init__(self, message, code="editorial_invalid"):
        self.code = code
        super().__init__(message)


def ensure_editorial_allowed(project, user):
    try:
        ensure_production_allowed(project, user)
    except Exception as exc:
        raise EditorialServiceError(str(exc), "editorial_not_allowed") from exc
