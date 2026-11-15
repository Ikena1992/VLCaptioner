"""Find recently created Danbooru character tags missing from a dataset."""
from dataclasses import dataclass
from datetime import date, datetime, timezone

from danbooru_client import danbooru_get
from booru_downloader import SCORE_FILTER, DatasetCountError, dataset_snapshot, local_scan_status


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

    def __post_init__(self):
        if date.fromisoformat(self.since).isoformat() != self.since:
            raise ValueError('Use YYYY-MM-DD for the creation date.')
        if any(type(n) is not int or n < 1 for n in (self.min_posts, self.target)):
            raise ValueError('Minimum posts and target must be positive whole numbers.')
        if self.source not in ('Danbooru', 'Gelbooru', 'Both') or self.scope not in ('Absent only', 'Below target'):
            raise ValueError('Choose a valid source and dataset rule.')
        if self.score_filter and not SCORE_FILTER.fullmatch(self.score_filter):
            raise ValueError('Use a score filter such as score:>=10, or leave it blank.')
        if self.rating_targets is not None and (len(self.rating_targets) != 4 or
                any(type(n) is not int or n < 0 for n in self.rating_targets) or not any(self.rating_targets)):
            raise ValueError('Set a nonnegative target for each rating, with at least one above zero.')
        if type(self.exclude_variants) is not bool:
            raise ValueError('Variant exclusion must be enabled or disabled.')


def character_parent(name, cancel, categories):
    """Find an active direct implication to another verified Character tag.

    None means no evidence found, not proof that this is a main character tag.
    Category lookups are shared within one discovery run, never across runs.
    """
    cursor = None
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
            if type(identifier) is not int or identifier < 1 or (cursor is not None and identifier >= cursor):
                raise ValueError('Tag implication pagination did not advance.')
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
                categories[parent] = match.get('category') if match else None
            if categories[parent] == 4:
                return parent
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
        if not isinstance(version.get('created_at'), str):
            raise ValueError('Tag history has no valid timestamp.')
        timestamp = datetime.fromisoformat(version['created_at'].replace('Z', '+00:00'))
        if timestamp.tzinfo is None:
            timestamp = timestamp.replace(tzinfo=timezone.utc)
        timestamps.append(timestamp.astimezone(timezone.utc))
    return min(timestamps)


def local_counts(root, cancel, log):
    from dataset_manager import scan_dataset
    folders = [p for p in (root / 'images', root / 'done', root / 'done1') if p.exists()]
    dataset = scan_dataset(folders, progress=lambda text: log(local_scan_status(text)),
                           cancelled=cancel.is_set, tag_fallback=True)
    if dataset is None:
        return None
    if dataset.warnings:
        raise DatasetCountError('Cannot reliably count images/ and done/. Fix unreadable tag files or folders first.')
    if dataset.missing or dataset.empty:
        log(f'{dataset.missing + dataset.empty:,} images have no usable tags and cannot be matched to character targets.')
    log('Local dataset: building unique image counts by tag and rating…')
    snapshot = dataset_snapshot(dataset)
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
            identifier = int(tag['id'])
            if identifier < 1:
                raise ValueError('Invalid character tag ID.')
            # Sites may return a page overlapping the preceding one. Ignore
            # the overlap, but require genuinely older IDs to move the cursor.
            if identifier in seen or (cursor is not None and identifier >= cursor):
                continue
            ids.append(identifier)
            seen.add(identifier)
            if (int(tag['category']) != 4 or int(tag['post_count']) < options.min_posts
                    or tag.get('is_deprecated', False)):
                continue
            name = tag['name']
            created = datetime.fromisoformat(tag['created_at'].replace('Z', '+00:00'))
            if created.tzinfo is None:
                created = created.replace(tzinfo=timezone.utc)
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
            if ((not ratings if ratings is not None else current >= options.target)
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
            if options.exclude_variants:
                log(f'Danbooru API: checking character implications for {name}…')
                parent = character_parent(name, cancel, parent_categories)
                if cancel.is_set():
                    return None
                if parent:
                    log(f'Skipped likely variant {name}: implies character tag {parent}.')
                    continue
            results.append({'name': name, 'post_count': int(tag['post_count']),
                            'created_at': tag['created_at'], 'local_count': current,
                            'target': options.target, 'score_filter': options.score_filter,
                            'rating_targets': ratings,
                            'earliest_history_at': earliest.isoformat() if earliest else None})
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
            for site in sources:
                label = {'g': 'general', 's': 'sensitive', 'q': 'questionable', 'e': 'explicit'}.get(rating)
                query = ' '.join(filter(None, (result['name'], f'rating:{label}' if label else '',
                                              result.get('score_filter', ''),
                                              'order:score' if site == 'Danbooru' else 'sort:score')))
                rows.append(dict(source=site, tags=query, target=str(target)))
    return rows


def character_tags_text(results):
    r"""Export unique raw character names, escaping _( as in beauty\_(nikke)."""
    names = dict.fromkeys(result['name'] for result in results)
    return ', '.join(name.replace('_(', r'\_(') for name in names) + '\n'
