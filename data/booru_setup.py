"""Small authenticated API checks without exposing secrets in error messages."""
import requests


def credential(settings, key):
    value = settings.get(key, '').strip().strip('\"\'')
    return '' if value.lower().startswith('your-') else value


def check_booru_access(site, settings):
    if site == 'Danbooru':
        keys = ('DANBOORU_LOGIN', 'DANBOORU_API_KEY')
        url = 'https://danbooru.donmai.us/profile.json'
    else:
        keys = ('GELBOORU_USER_ID', 'GELBOORU_API_KEY')
        url = 'https://gelbooru.com/index.php'
    values = [credential(settings, key) for key in keys]
    if not all(values):
        return f'• {site}: credentials not configured. Set {" and ".join(keys)} in config.txt.'
    headers = {'User-Agent': settings.get('DANBOORU_USER_AGENT', 'VLCaptioner/1.0') if site == 'Danbooru' else 'VLCaptioner/1.0', 'Accept': 'application/json'}
    options = dict(timeout=(4, 6), headers=headers)
    if site == 'Danbooru':
        options['auth'] = tuple(values)
    else:
        options['params'] = dict(page='dapi', s='tag', q='index', json=1, limit=1,
                                 names='1girl', user_id=values[0], api_key=values[1])
    try:
        with requests.get(url, **options) as response:
            if response.status_code in (401, 403):
                return f'• {site}: access denied (HTTP {response.status_code}). Check credentials and API permissions.'
            if response.status_code == 429:
                return f'• {site}: rate limited. Try Check again later.'
            if response.status_code != 200:
                return f'• {site}: API check failed (HTTP {response.status_code}). Try again later.'
            payload = response.json()
        if site == 'Danbooru':
            if not isinstance(payload, dict) or not payload.get('id') or str(payload.get('name', '')).casefold() != values[0].casefold():
                return '• Danbooru: could not verify the configured account. Check login and API key.'
            return '✓ Danbooru API is reachable with the configured credentials'
        valid = isinstance(payload, list) and all(isinstance(item, dict) for item in payload)
        if isinstance(payload, dict):
            valid = ('tag' in payload and isinstance(payload['tag'], list)
                     and all(isinstance(item, dict) for item in payload['tag']))
        if not valid:
            return '• Gelbooru: API access could not be confirmed. Check credentials and try again.'
        return '✓ Gelbooru API is reachable with the configured credentials'
    except requests.Timeout:
        return f'• {site}: connection timed out. Check connectivity and try again.'
    except requests.RequestException:
        # Exception text can contain the Gelbooru API key in its query URL.
        return f'• {site}: connection failed. Check connectivity and try again.'
    except ValueError:
        return f'• {site}: API returned an unexpected response. Check credentials and try again.'
