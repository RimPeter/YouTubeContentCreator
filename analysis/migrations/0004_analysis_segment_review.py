from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):
    dependencies = [
        ("analysis", "0003_retire_selections"),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.CreateModel(
            name="AnalysisSegmentReview",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("decision", models.CharField(choices=[("selected", "Selected"), ("excluded", "Excluded"), ("removed", "Removed from selection")], max_length=12)),
                ("reason", models.TextField(blank=True)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("analysis_segment", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="reviews", to="analysis.analysissegment")),
                ("project", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="analysis_segment_reviews", to="scraper.videoproject")),
                ("reviewed_by", models.ForeignKey(null=True, on_delete=django.db.models.deletion.SET_NULL, related_name="analysis_segment_reviews_created", to=settings.AUTH_USER_MODEL)),
            ],
            options={"ordering": ["analysis_segment", "-created_at", "-pk"]},
        ),
        migrations.AddIndex(
            model_name="analysissegmentreview",
            index=models.Index(fields=["project", "analysis_segment", "-created_at"], name="analysis_review_lookup_idx"),
        ),
    ]
