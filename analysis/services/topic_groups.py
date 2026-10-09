"""Read-only overview groups; original segments and review decisions stay intact."""
from collections import Counter
from math import ceil, log, sqrt
import re


STOP_WORDS = set("""a an and are as at be been being but by can could did do does doing
for from had has have having he her here him his how i if in into is it its just like
me more most my no not of on one or our out she so some than that the their them then
there these they this those through to too up us was we were what when where which
who why will with would you your about all also any because both each even get gets
got going good know let lot make much need now only other really right said say says
see something still such take think thing things time very want way well yes yeah
actually kind mean okay sure don doesn didn isn won ve re ll uh um s t m d
cannot possible pretty first second third however look looks come comes back
start end different same part point put able every anything everything nothing
many few little big long new old better enough thought understand talking tell
guy somebody someone saying means example stuff happens happened keep trying
""".split())


def _keywords(row):
    segment = row["segment"]
    return Counter(word for word in re.findall(r"[^\W\d_]+", segment.source_text.casefold())
                   if len(word) > 2 and word not in STOP_WORDS)


def _inferred_groups(rows):
    """Cluster similar vocabulary without a provider call or database mutation."""
    counts = [_keywords(row) for row in rows]
    frequency = Counter(word for count in counts for word in count)
    vectors = []
    for count in counts:
        vector = {word: (1 + log(value)) * log(1 + len(rows) / frequency[word])
                  for word, value in count.items()}
        norm = sqrt(sum(value * value for value in vector.values())) or 1
        vectors.append({word: value / norm for word, value in vector.items()})
    similarities = {(i, j): sum(value * vectors[j].get(word, 0) for word, value in vectors[i].items())
                    for i in range(len(rows)) for j in range(i + 1, len(rows))}
    clusters = [[i] for i in range(len(rows))]
    target = max(1, ceil(sqrt(len(rows))))
    while len(clusters) > target:
        candidates = ((sum(similarities[tuple(sorted((a, b)))] for a in left for b in right)
                       / (len(left) * len(right)), i, j)
                      for i, left in enumerate(clusters) for j, right in enumerate(clusters) if i < j)
        score, i, j = max(candidates, default=(0, 0, 0))
        if score < 0.02:
            break
        clusters[i] += clusters.pop(j)
    groups = []
    for cluster in clusters:
        weights = Counter()
        for i in cluster:
            weights.update(vectors[i])
        terms = [word for word, _ in weights.most_common(3)]
        groups.append({"title": " / ".join(terms).capitalize() or "Other sections",
                       "rows": [rows[i] for i in sorted(cluster)], "suggested": True})
    return groups


def group_segment_rows(rows):
    """Assign each section once, preferring the most widely shared saved label."""
    positions = {row["segment"].pk: i for i, row in enumerate(rows)}
    rows = sorted(rows, key=lambda row: row["segment"].order)
    labels = []
    names = {}
    for row in rows:
        keys = []
        for label in row["segment"].topic_labels:
            name = " ".join(label.split())
            key = name.casefold()
            if key and key != "deterministic-fallback" and key not in keys:
                keys.append(key)
                names.setdefault(key, name)
        labels.append(keys)
    frequency = Counter(key for keys in labels for key in keys)
    grouped = {}
    unlabelled = []
    for row, keys in zip(rows, labels):
        if not keys:
            unlabelled.append(row)
            continue
        key = max(keys, key=lambda key: frequency[key])
        grouped.setdefault(key, {"title": names[key], "rows": [], "suggested": False})["rows"].append(row)
    groups = list(grouped.values()) + _inferred_groups(unlabelled)
    groups.sort(key=lambda group: min(positions[row["segment"].pk] for row in group["rows"]))
    for group in groups:
        group["rows"].sort(key=lambda row: positions[row["segment"].pk])
        group["selected_count"] = sum(bool(row["selection"]) for row in group["rows"])
        group["excluded_count"] = sum(
            not row["selection"] and getattr(row.get("review"), "decision", None) == "excluded"
            for row in group["rows"]
        )
        group["completed_count"] = group["selected_count"] + group["excluded_count"]
        group["completion_percent"] = round(100 * group["completed_count"] / len(group["rows"]))
    return groups
