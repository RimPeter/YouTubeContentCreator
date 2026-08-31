from django.db import migrations, models


def use_video_ids_as_existing_titles(apps, schema_editor):
    ScrapedVideo = apps.get_model('scraper', 'ScrapedVideo')
    for scraped_video in ScrapedVideo.objects.all():
        scraped_video.video_title = scraped_video.youtube_video_id
        scraped_video.save(update_fields=['video_title'])


class Migration(migrations.Migration):
    dependencies = [('scraper', '0002_scrape_history_models')]

    operations = [
        migrations.AddField(
            model_name='scrapedvideo',
            name='video_title',
            field=models.CharField(default='', max_length=255),
            preserve_default=False,
        ),
        migrations.RunPython(use_video_ids_as_existing_titles, migrations.RunPython.noop),
    ]