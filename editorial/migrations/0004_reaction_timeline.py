from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion
import django.core.validators


class Migration(migrations.Migration):
    dependencies = [("editorial", "0003_reaction_sequence_draft"), migrations.swappable_dependency(settings.AUTH_USER_MODEL)]
    operations = [
        migrations.CreateModel(name="ReactionTimeline", fields=[
            ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")), ("version", models.PositiveIntegerField()),
            ("status", models.CharField(choices=[("draft", "Draft"), ("stale", "Stale")], default="draft", max_length=16)),
            ("input_fingerprint", models.CharField(max_length=64, validators=[django.core.validators.RegexValidator(message="Enter a lowercase SHA-256 digest.", regex="^[0-9a-f]{64}$")])),
            ("created_at", models.DateTimeField(auto_now_add=True)), ("updated_at", models.DateTimeField(auto_now=True)),
            ("created_by", models.ForeignKey(null=True, on_delete=django.db.models.deletion.SET_NULL, related_name="reaction_timelines_created", to=settings.AUTH_USER_MODEL)),
            ("project", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="reaction_timelines", to="scraper.videoproject")),
            ("reaction_draft", models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name="timelines", to="editorial.reactionsequencedraft")),
        ], options={"ordering": ["reaction_draft", "-version"]}),
        migrations.CreateModel(name="ReactionTimelineItem", fields=[
            ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")), ("order", models.PositiveIntegerField()),
            ("item_type", models.CharField(choices=[("source", "Source"), ("creator", "Creator")], max_length=12)), ("label", models.CharField(max_length=255)), ("transcript_text", models.TextField()),
            ("source_start_seconds", models.FloatField(blank=True, null=True)), ("source_end_seconds", models.FloatField(blank=True, null=True)),
            ("draft_section", models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.PROTECT, related_name="timeline_items", to="editorial.reactionsequencedraftsection")),
            ("plan_section", models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.PROTECT, related_name="timeline_items", to="editorial.reactionsequencesection")),
            ("timeline", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="items", to="editorial.reactiontimeline")),
        ], options={"ordering": ["timeline", "order"]}),
        migrations.AddConstraint(model_name="reactiontimeline", constraint=models.UniqueConstraint(fields=("reaction_draft", "version"), name="reaction_timeline_draft_version_uniq")),
        migrations.AddConstraint(model_name="reactiontimeline", constraint=models.CheckConstraint(condition=models.Q(("version__gte", 1)), name="reaction_timeline_version_gte_1")),
        migrations.AddConstraint(model_name="reactiontimelineitem", constraint=models.UniqueConstraint(fields=("timeline", "order"), name="reaction_timeline_item_order_uniq")),
        migrations.AddConstraint(model_name="reactiontimelineitem", constraint=models.CheckConstraint(condition=models.Q(("order__gte", 1)), name="reaction_timeline_item_order_gte_1")),
        migrations.AddConstraint(model_name="reactiontimelineitem", constraint=models.CheckConstraint(condition=models.Q(("source_end_seconds__isnull", True), ("source_end_seconds__gte", models.F("source_start_seconds")), _connector="OR"), name="reaction_timeline_time_order")),
    ]
