from django.db import migrations, models
import django.db.models.deletion


def copy_legacy_transcripts(apps, schema_editor):
    SourceVideo = apps.get_model('scraper', 'SourceVideo')
    TranscriptChunk = apps.get_model('scraper', 'TranscriptChunk')
    ScrapedVideo = apps.get_model('scraper', 'ScrapedVideo')
    TranscriptEntry = apps.get_model('scraper', 'TranscriptEntry')

    for source_video in SourceVideo.objects.all():
        scraped_video, _ = ScrapedVideo.objects.get_or_create(
            youtube_video_id=source_video.youtube_video_id,
            defaults={'youtube_url': source_video.youtube_url},
        )
        TranscriptEntry.objects.bulk_create([
            TranscriptEntry(
                scraped_video=scraped_video,
                sequence=chunk.sequence,
                start_seconds=chunk.start,
                duration_seconds=chunk.duration,
                text=chunk.text,
            )
            for chunk in TranscriptChunk.objects.filter(source_video=source_video)
        ])


class Migration(migrations.Migration):
    dependencies = [('scraper', '0001_initial')]

    operations = [
        migrations.CreateModel(
            name='ScrapedVideo',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('youtube_video_id', models.CharField(max_length=11, unique=True)),
                ('youtube_url', models.URLField()),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('updated_at', models.DateTimeField(auto_now=True)),
            ],
            options={'ordering': ['-created_at']},
        ),
        migrations.CreateModel(
            name='TranscriptEntry',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('sequence', models.PositiveIntegerField()),
                ('start_seconds', models.FloatField()),
                ('duration_seconds', models.FloatField()),
                ('text', models.TextField()),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('scraped_video', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='entries', to='scraper.scrapedvideo')),
            ],
            options={'ordering': ['scraped_video', 'sequence']},
        ),
        migrations.AddConstraint(
            model_name='transcriptentry',
            constraint=models.UniqueConstraint(fields=('scraped_video', 'sequence'), name='unique_transcript_entry_sequence'),
        ),
        migrations.RunPython(copy_legacy_transcripts, migrations.RunPython.noop),
        migrations.DeleteModel(name='TranscriptChunk'),
        migrations.DeleteModel(name='SourceVideo'),
        migrations.DeleteModel(name='VideoProject'),
    ]