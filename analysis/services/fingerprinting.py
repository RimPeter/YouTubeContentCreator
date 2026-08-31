import hashlib
import json


def ordered_transcript_chunks(source_video):
    return list(source_video.transcript_chunks.order_by("sequence", "pk"))


def fingerprint_chunks(chunks):
    payload = [
        {
            "id": chunk.pk,
            "sequence": chunk.sequence,
            "start_seconds": chunk.start_seconds,
            "duration_seconds": chunk.duration_seconds,
            "text": chunk.text,
        }
        for chunk in chunks
    ]
    serialized = json.dumps(
        payload,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")
    return hashlib.sha256(serialized).hexdigest()


def fingerprint_source_video(source_video):
    return fingerprint_chunks(ordered_transcript_chunks(source_video))
