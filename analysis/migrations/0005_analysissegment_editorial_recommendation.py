from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [("analysis", "0004_analysis_segment_review")]

    operations = [
        migrations.AddField(
            model_name="analysissegment",
            name="editorial_recommendation",
            field=models.JSONField(default=dict),
        ),
    ]
