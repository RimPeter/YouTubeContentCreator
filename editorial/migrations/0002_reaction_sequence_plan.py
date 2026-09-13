from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion
import django.core.validators


class Migration(migrations.Migration):
    dependencies = [("analysis", "0005_analysissegment_editorial_recommendation"), ("editorial", "0001_initial"), migrations.swappable_dependency(settings.AUTH_USER_MODEL)]

    operations = [
        migrations.CreateModel(name="ReactionSequencePlan", fields=[
            ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
            ("version", models.PositiveIntegerField()),
            ("status", models.CharField(choices=[("draft", "Draft"), ("stale", "Stale"), ("superseded", "Superseded")], default="draft", max_length=16)),
            ("input_fingerprint", models.CharField(max_length=64, validators=[django.core.validators.RegexValidator(message="Enter a lowercase SHA-256 digest.", regex="^[0-9a-f]{64}$")])),
            ("overall_thesis", models.TextField(validators=[django.core.validators.MaxLengthValidator(2000)])), ("audience_angle", models.TextField(validators=[django.core.validators.MaxLengthValidator(2000)])), ("planned_conclusion", models.TextField(validators=[django.core.validators.MaxLengthValidator(2000)])),
            ("created_at", models.DateTimeField(auto_now_add=True)), ("updated_at", models.DateTimeField(auto_now=True)),
            ("created_by", models.ForeignKey(null=True, on_delete=django.db.models.deletion.SET_NULL, related_name="reaction_sequence_plans_created", to=settings.AUTH_USER_MODEL)),
            ("project", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="reaction_sequence_plans", to="scraper.videoproject")),
        ], options={"ordering": ["project", "-version"]}),
        migrations.CreateModel(name="ReactionSequenceSection", fields=[
            ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
            ("order", models.PositiveIntegerField()),
            ("role", models.CharField(choices=[("thesis", "Thesis"), ("evidence", "Evidence"), ("tension", "Tension"), ("reflection", "Reflection"), ("add_on", "Practical add-on"), ("conclusion", "Conclusion")], max_length=16)),
            ("source_title", models.CharField(max_length=255)), ("transcript_snapshot", models.TextField()), ("recommendation_snapshot", models.JSONField(default=dict)),
            ("bridge", models.TextField(blank=True, validators=[django.core.validators.MaxLengthValidator(2000)])), ("research_required", models.BooleanField(default=False)), ("research_reason", models.TextField(blank=True, validators=[django.core.validators.MaxLengthValidator(2000)])),
            ("plan", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="sections", to="editorial.reactionsequenceplan")),
            ("selection", models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name="reaction_sequence_sections", to="analysis.segmentselection")),
        ], options={"ordering": ["plan", "order"]}),
        migrations.AddConstraint(model_name="reactionsequenceplan", constraint=models.UniqueConstraint(fields=("project", "version"), name="reaction_plan_project_version_uniq")),
        migrations.AddConstraint(model_name="reactionsequenceplan", constraint=models.CheckConstraint(condition=models.Q(("version__gte", 1)), name="reaction_plan_version_gte_1")),
        migrations.AddConstraint(model_name="reactionsequencesection", constraint=models.UniqueConstraint(fields=("plan", "order"), name="reaction_plan_section_order_uniq")),
        migrations.AddConstraint(model_name="reactionsequencesection", constraint=models.UniqueConstraint(fields=("plan", "selection"), name="reaction_plan_section_selection_uniq")),
        migrations.AddConstraint(model_name="reactionsequencesection", constraint=models.CheckConstraint(condition=models.Q(("order__gte", 1)), name="reaction_plan_section_order_gte_1")),
    ]
