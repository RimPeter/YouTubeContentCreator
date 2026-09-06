from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):
    dependencies = [
        ("analysis", "0002_add_boundary_constraints"),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.AddField(
            model_name="segmentselection",
            name="retired_at",
            field=models.DateTimeField(blank=True, editable=False, null=True),
        ),
        migrations.AddField(
            model_name="segmentselection",
            name="retired_by",
            field=models.ForeignKey(
                blank=True,
                editable=False,
                null=True,
                on_delete=django.db.models.deletion.SET_NULL,
                related_name="segment_selections_retired",
                to=settings.AUTH_USER_MODEL,
            ),
        ),
        migrations.RemoveConstraint(
            model_name="segmentselection", name="selection_project_segment_uniq"
        ),
        migrations.RemoveConstraint(
            model_name="segmentselection", name="selection_project_order_uniq"
        ),
        migrations.AddConstraint(
            model_name="segmentselection",
            constraint=models.UniqueConstraint(
                fields=("project", "analysis_segment"),
                condition=models.Q(retired_at__isnull=True),
                name="selection_active_segment_uniq",
            ),
        ),
        migrations.AddConstraint(
            model_name="segmentselection",
            constraint=models.UniqueConstraint(
                fields=("project", "order"),
                condition=models.Q(retired_at__isnull=True),
                name="selection_active_order_uniq",
            ),
        ),
    ]
