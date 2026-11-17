"""Find recently created Danbooru character tags missing from a dataset."""
from dataclasses import dataclass
from datetime import date, datetime, timezone, timedelta

from danbooru_client import danbooru_get
from booru_downloader import SCORE_FILTER, scan_target_counts


def utc_today():
    return datetime.now(timezone.utc).date()


def utc_timestamp(value):
    if not isinstance(value, str):
        raise ValueError('Expected a timestamp from Danbooru.')
    timestamp = datetime.fromisoformat(value.replace('Z', '+00:00'))
    if timestamp.tzinfo is None:
        timestamp = timestamp.replace(tzinfo=timezone.utc)
    return timestamp.astimezone(timezone.utc)


@dataclass(frozen=True)
class CharacterOptions:
    since: str = '2025-01-01'
    min_posts: int = 100
    target: int = 200
    source: str = 'Both'
    scope: str = 'Below target'
    score_filter: str = ''
    rating_targets: tuple | None = None
    exclude_variants: bool = False
    oldest_post_since: str = ''
    official_art_target: int = 0

    def __post_init__(self):
        if date.fromisoformat(self.since).isoformat() != self.since:
            raise ValueError('Use YYYY-MM-DD for the creation date.')
        if any(type(n) is not int or n < 1 for n in (self.min_posts, self.target)):
            raise ValueError('Minimum posts and target must be positive whole numbers.')
        if self.source not in ('Danbooru', 'Gelbooru', 'Both') or self.scope not in ('Absent only', 'Below target'):
            raise ValueError('Choose a valid source and dataset rule.')
        if self.score_filter and not SCORE_FILTER.fullmatch(self.score_filter):
            raise ValueError('Use a score filter such as score:>=10, or leave it blank.')
        if type(self.official_art_target) is not int or self.official_art_target < 0:
            raise ValueError('Official art target must be a nonnegative whole number.')
        if self.rating_targets is not None and (len(self.rating_targets) != 4 or
                any(type(n) is not int or n < 0 for n in self.rating_targets) or
                not (any(self.rating_targets) or self.official_art_target)):
            raise ValueError('Set a nonnegative target for each rating, with at least one above zero.')
        if type(self.exclude_variants) is not bool:
            raise ValueError('Variant exclusion must be enabled or disabled.')
        if self.oldest_post_since:
            oldest_date = date.fromisoformat(self.oldest_post_since)
            if oldest_date.isoformat() != self.oldest_post_since or oldest_date > utc_today():
                raise ValueError('Oldest image date must be YYYY-MM-DD and no later than today (UTC).')


def earliest_character_post(name, cancel=None):
    """Read the lowest-ID accessible post, including deleted records when allowed."""
    if cancel is not None and cancel.is_set():
        return None
    with danbooru_get('https://danbooru.donmai.us/posts.json', params={
            'tags': f'{name} status:any order:id_asc', 'limit': 1}, timeout=30) as response:
        response.raise_for_status()
        posts = response.json()
    if cancel is not None and cancel.is_set():
        return None
    return oldest_post_timestamp(posts, name)


def oldest_post_timestamp(posts, name):
    """Validate matching posts before trusting any timestamps as age evidence."""
    if not isinstance(posts, list) or any(not isinstance(p, dict) for p in posts):
        raise ValueError('Unexpected oldest image response.')
    if not posts:
        return None
    timestamps = []
    for post in posts:
        if not isinstance(post.get('tag_string'), str) or name not in post['tag_string'].split():
            raise ValueError('Oldest image does not contain the requested character tag.')
        timestamps.append(utc_timestamp(post.get('created_at')))
    return min(timestamps)


def character_parent(name, cancel, categories):
    """Find an active direct implication to another verified Character tag.

    None means no evidence found, not proof that this is a main character tag.
    Category lookups are shared within one discovery run, never across runs.
    """
    cursor = None
    seen = set()
    while not cancel.is_set():
        params = {'search[antecedent_name]': name, 'search[status]': 'active', 'limit': 100}
        if cursor is not None:
            params['page'] = f'b{cursor}'
        with danbooru_get('https://danbooru.donmai.us/tag_implications.json', params=params, timeout=30) as response:
            response.raise_for_status()
            relations = response.json()
        if cancel.is_set():
            return None
        if not isinstance(relations, list) or any(not isinstance(r, dict) for r in relations):
            raise ValueError('Unexpected tag implication response.')
        if not relations:
            return None
        ids = []
        for relation in relations:
            if cancel.is_set():
                return None
            identifier = relation.get('id')
            if type(identifier) is not int or identifier < 1:
                raise ValueError('Invalid tag implication ID.')
            if identifier in seen or (cursor is not None and identifier >= cursor):
                continue
            seen.add(identifier)
            ids.append(identifier)
            if relation.get('antecedent_name') != name or relation.get('status') != 'active':
                continue
            parent = relation.get('consequent_name')
            if not isinstance(parent, str) or not parent or parent == name:
                continue
            if parent not in categories:
                with danbooru_get('https://danbooru.donmai.us/tags.json',
                                  params={'search[name]': parent, 'limit': 1}, timeout=30) as response:
                    response.raise_for_status()
                    tags = response.json()
                if cancel.is_set():
                    return None
                if not isinstance(tags, list) or any(not isinstance(t, dict) for t in tags):
                    raise ValueError('Unexpected parent tag response.')
                match = next((t for t in tags if t.get('name') == parent), None)
                if match is not None and type(match.get('category')) is not int:
                    raise ValueError('Parent tag has no valid category.')
                categories[parent] = match.get('category') if match else None
            if categories[parent] == 4:
                return parent
        if not ids:
            raise ValueError('Tag implication pagination did not advance.')
        cursor = min(ids)
    return None


def earliest_tag_history(tag_id):
    """Read the earliest available version across names/categories for a tag ID."""
    with danbooru_get('https://danbooru.donmai.us/tag_versions.json', params={
            'search[tag_id]': tag_id, 'search[order]': 'created_at_asc', 'limit': 1}, timeout=30) as response:
        response.raise_for_status()
        versions = response.json()
    if not isinstance(versions, list) or any(not isinstance(v, dict) for v in versions):
        raise ValueError('Unexpected tag history response.')
    if not versions:
        return None
    timestamps = []
    for version in versions:
        if type(version.get('tag_id')) is not int or version['tag_id'] != tag_id:
            raise ValueError('Tag history belongs to a different tag or has an invalid ID.')
        timestamps.append(utc_timestamp(version.get('created_at')))
    return min(timestamps)


def local_counts(root, cancel, log):
    snapshot = scan_target_counts(root / 'images', root / 'done', cancel, log)
    if snapshot is not None and not cancel.is_set():
        log('Local image counts ready. Matching API character tags…')
    return snapshot


def discover(options, root, cancel, log=lambda text: None):
    """Return complete results; cancellation returns None and never changes queues."""
    from dataset_manager import normalize
    if cancel.is_set():
        return None
    # Don't read the entire local dataset until a qualifying remote tag exists.
    counts = None
    results, seen = [], set()
    parent_categories = {}
    cursor = None
    page = 0
    cutoff = datetime.combine(date.fromisoformat(options.since), datetime.min.time(), timezone.utc)
    oldest_cutoff = (datetime.combine(date.fromisoformat(options.oldest_post_since), datetime.min.time(), timezone.utc)
                     if options.oldest_post_since else None)
    today_end = datetime.combine(utc_today(), datetime.min.time(), timezone.utc) + timedelta(days=1)
    while not cancel.is_set():
        params = {'search[category]': 4, 'search[created_at]': f'>={options.since}',
                  'search[post_count]': f'>={options.min_posts}',
                  'search[is_deprecated]': 'false', 'search[order]': 'date', 'limit': 100}
        if cursor is not None:
            params['page'] = f'b{cursor}'
        page += 1
        log(f'Danbooru API: requesting character tags, page {page:,}; {len(seen):,} tags checked…')
        with danbooru_get('https://danbooru.donmai.us/tags.json', params=params, timeout=30) as response:
            response.raise_for_status()
            tags = response.json()
        if cancel.is_set():
            return None
        if not isinstance(tags, list) or any(not isinstance(t, dict) for t in tags):
            raise ValueError('Unexpected character search response.')
        if not tags:
            return results
        ids = []
        for tag in tags:
            if cancel.is_set():
                return None
            identifier = tag.get('id')
            if type(identifier) is not int or identifier < 1:
                raise ValueError('Invalid character tag ID.')
            # Sites may return a page overlapping the preceding one. Ignore
            # the overlap, but require genuinely older IDs to move the cursor.
            if identifier in seen or (cursor is not None and identifier >= cursor):
                continue
            ids.append(identifier)
            seen.add(identifier)
            category, post_count = tag.get('category'), tag.get('post_count')
            deprecated = tag.get('is_deprecated', False)
            if type(category) is not int or type(post_count) is not int or post_count < 0 or type(deprecated) is not bool:
                raise ValueError('Invalid character tag metadata.')
            if category != 4 or post_count < options.min_posts or deprecated:
                continue
            name = tag.get('name')
            created = utc_timestamp(tag.get('created_at'))
            if created < cutoff:
                continue
            if not isinstance(name, str) or not name or any(c.isspace() for c in name):
                raise ValueError('Invalid character tag name.')
            if counts is None:
                counts = local_counts(root, cancel, log)
                if counts is None or cancel.is_set():
                    return None
            normalized_name = normalize(name)
            current = counts.get(normalized_name, 0)
            ratings = ({rating: target for rating, target in zip('gsqe', options.rating_targets)
                        if target > counts.get((normalized_name, rating), 0)}
                       if options.rating_targets is not None else None)
            official_target = (options.official_art_target if options.official_art_target >
                               counts.get((normalized_name, 'official_art'), 0) else 0)
            if (((not ratings if ratings is not None else current >= options.target) and not official_target)
                    or (options.scope == 'Absent only' and current)):
                continue
            log(f'Danbooru API: checking tag history for {name}…')
            earliest = earliest_tag_history(identifier)
            if cancel.is_set():
                return None
            if earliest is not None and earliest < cutoff:
                log(f'Skipped {name}: tag history starts {earliest.date()}, before {options.since}.')
                continue
            if earliest is None:
                log(f'{name}: no tag history available; keeping the candidate.')
            oldest_post = None
            if oldest_cutoff is not None:
                log(f'Danbooru API: checking oldest image for {name}…')
                oldest_post = earliest_character_post(name, cancel=cancel)
                if cancel.is_set():
                    return None
                if oldest_post is None:
                    log(f'Skipped {name}: no accessible post available to verify the oldest image date.')
                    continue
                if not oldest_cutoff <= oldest_post < today_end:
                    log(f'Skipped {name}: oldest image date {oldest_post.date()} is outside the selected range through today (UTC).')
                    continue
            if options.exclude_variants:
                log(f'Danbooru API: checking character implications for {name}…')
                parent = character_parent(name, cancel, parent_categories)
                if cancel.is_set():
                    return None
                if parent:
                    log(f'Skipped likely variant {name}: implies character tag {parent}.')
                    continue
            results.append({'name': name, 'post_count': post_count,
                            'created_at': tag['created_at'], 'local_count': current,
                            'target': options.target, 'score_filter': options.score_filter,
                            'rating_targets': ratings,
                            'official_art_target': official_target,
                            'earliest_history_at': earliest.isoformat() if earliest else None,
                            'oldest_post_at': oldest_post.isoformat() if oldest_post else None})
        if not ids:
            raise ValueError('Character search pagination did not advance.')
        cursor = min(ids)
        log(f'API character tags: {len(seen):,} checked; {len(results):,} missing or underfilled.')
    return None


def search_rows(results, source):
    sources = ('Danbooru', 'Gelbooru') if source == 'Both' else (source,)
    rows = []
    for result in results:
        ratings = result.get('rating_targets')
        for rating, target in (ratings.items() if ratings is not None else [(None, result['target'])]):
            if target <= 0 or (ratings is None and result.get('local_count', 0) >= target):
                continue
            for site in sources:
                label = {'g': 'general', 's': 'sensitive', 'q': 'questionable', 'e': 'explicit'}.get(rating)
                query = ' '.join(filter(None, (result['name'], f'rating:{label}' if label else '',
                                              result.get('score_filter', ''),
                                              'order:score' if site == 'Danbooru' else 'sort:score')))
                rows.append(dict(source=site, tags=query, target=str(target)))
        if target := result.get('official_art_target', 0):
            query = f"{result['name']} official_art order:score"
            rows.append(dict(source='Danbooru', tags=query, target=str(target)))
    return rows


def character_tags_text(results):
    r"""Export unique raw character names, escaping _( as in beauty\_(nikke)."""
    names = dict.fromkeys(result['name'] for result in results)
    return ', '.join(name.replace('_(', r'\_(') for name in names) + '\n'
