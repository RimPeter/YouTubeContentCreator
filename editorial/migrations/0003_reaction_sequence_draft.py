from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion
import django.core.validators


class Migration(migrations.Migration):
    dependencies = [("editorial", "0002_reaction_sequence_plan"), migrations.swappable_dependency(settings.AUTH_USER_MODEL)]
    operations = [
        migrations.AddField(model_name="reactionsequenceplan", name="review_attestation", field=models.JSONField(blank=True, default=dict)),
        migrations.AddField(model_name="reactionsequenceplan", name="reviewed_at", field=models.DateTimeField(blank=True, null=True)),
        migrations.AddField(model_name="reactionsequenceplan", name="reviewed_by", field=models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name="reaction_sequence_plans_reviewed", to=settings.AUTH_USER_MODEL)),
        migrations.AlterField(model_name="reactionsequenceplan", name="status", field=models.CharField(choices=[("draft", "Draft"), ("ready", "Ready for drafting"), ("stale", "Stale"), ("superseded", "Superseded")], default="draft", max_length=16)),
        migrations.CreateModel(name="ReactionSequenceDraft", fields=[
            ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")), ("version", models.PositiveIntegerField()),
            ("status", models.CharField(choices=[("draft", "Draft"), ("failed", "Failed"), ("stale", "Stale"), ("superseded", "Superseded")], default="draft", max_length=16)),
            ("input_fingerprint", models.CharField(max_length=64, validators=[django.core.validators.RegexValidator(message="Enter a lowercase SHA-256 digest.", regex="^[0-9a-f]{64}$")])), ("opening", models.TextField(validators=[django.core.validators.MaxLengthValidator(4000)])), ("conclusion", models.TextField(validators=[django.core.validators.MaxLengthValidator(4000)])), ("combined_script", models.TextField(validators=[django.core.validators.MaxLengthValidator(40000)])), ("rationale", models.TextField(validators=[django.core.validators.MaxLengthValidator(4000)])),
            ("provider", models.CharField(blank=True, max_length=100)), ("provider_model", models.CharField(blank=True, max_length=100)), ("prompt_version", models.CharField(blank=True, max_length=64)), ("used_fallback", models.BooleanField(default=False)),
            ("created_at", models.DateTimeField(auto_now_add=True)), ("updated_at", models.DateTimeField(auto_now=True)),
            ("created_by", models.ForeignKey(null=True, on_delete=django.db.models.deletion.SET_NULL, related_name="reaction_sequence_drafts_created", to=settings.AUTH_USER_MODEL)),
            ("plan", models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name="reaction_drafts", to="editorial.reactionsequenceplan")),
            ("project", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="reaction_sequence_drafts", to="scraper.videoproject")),
        ], options={"ordering": ["plan", "-version"]}),
        migrations.CreateModel(name="ReactionSequenceDraftSection", fields=[
            ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")), ("order", models.PositiveIntegerField()), ("reaction_text", models.TextField(validators=[django.core.validators.MaxLengthValidator(8000)])), ("bridge", models.TextField(blank=True, validators=[django.core.validators.MaxLengthValidator(2000)])),
            ("draft", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="sections", to="editorial.reactionsequencedraft")),
            ("plan_section", models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name="draft_sections", to="editorial.reactionsequencesection")),
        ], options={"ordering": ["draft", "order"]}),
        migrations.AddConstraint(model_name="reactionsequencedraft", constraint=models.UniqueConstraint(fields=("plan", "version"), name="reaction_sequence_draft_version_uniq")),
        migrations.AddConstraint(model_name="reactionsequencedraft", constraint=models.CheckConstraint(condition=models.Q(("version__gte", 1)), name="reaction_sequence_draft_version_gte_1")),
        migrations.AddConstraint(model_name="reactionsequencedraftsection", constraint=models.UniqueConstraint(fields=("draft", "order"), name="reaction_sequence_draft_section_order_uniq")),
        migrations.AddConstraint(model_name="reactionsequencedraftsection", constraint=models.UniqueConstraint(fields=("draft", "plan_section"), name="reaction_sequence_draft_plan_section_uniq")),
        migrations.AddConstraint(model_name="reactionsequencedraftsection", constraint=models.CheckConstraint(condition=models.Q(("order__gte", 1)), name="reaction_sequence_draft_section_order_gte_1")),
    ]
