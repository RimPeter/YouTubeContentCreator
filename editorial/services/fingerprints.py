from production.services.fingerprints import fingerprint_json


def source_clip_fingerprint(clip):
    return fingerprint_json(
        {
            "clip_id": clip.pk,
            "clip_version": clip.version,
            "clip_input": clip.input_fingerprint,
            "processed_checksum": clip.processed_asset.checksum_sha256,
            "selection_id": clip.selected_segment_id,
        }
    )


def research_fingerprint(package):
    return fingerprint_json(
        {
            "package_id": package.pk,
            "version": package.version,
            "source_clip": source_clip_fingerprint(package.source_clip),
            "question": package.research_question,
            "focus": package.editorial_focus,
            "evidence": [
                {
                    "id": evidence.pk,
                    "title": evidence.title,
                    "publisher": evidence.publisher,
                    "url": evidence.source_url,
                    "classification": evidence.classification,
                    "finding": evidence.finding,
                    "relevance": evidence.relevance,
                    "verification": evidence.verification_status,
                }
                for evidence in package.evidence_sources.order_by("pk")
                if evidence.verification_status == "verified"
            ],
        }
    )


def reaction_fingerprint(package):
    return fingerprint_json(
        {
            "research": research_fingerprint(package),
            "algorithm": "reaction-draft-v1",
            "prompt": "reaction-draft-v1",
        }
    )
