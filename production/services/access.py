from scraper.models import VideoProject


class ProductionServiceError(Exception):
    pass


class ProductionPermissionError(ProductionServiceError):
    pass


class ProductionLifecycleError(ProductionServiceError):
    pass


class ProductionValidationError(ProductionServiceError):
    pass


def ensure_project_access(project, user):
    if not user or not user.is_authenticated or not user.is_active or (
        project.owner_id != user.pk and not user.is_staff and not user.is_superuser
    ):
        raise ProductionPermissionError("You cannot access production data for this project.")


def ensure_production_allowed(project, user):
    ensure_project_access(project, user)
    if project.status != VideoProject.Status.APPROVED or project.is_locked:
        raise ProductionLifecycleError(
            "Production work requires an approved, unlocked, non-archived project."
        )
