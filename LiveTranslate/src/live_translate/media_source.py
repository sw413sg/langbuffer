"""Selección explícita del contenido; no conserva historial de enlaces."""
import re


_BROADCAST = re.compile(
    r'https://(?:www\.)?(?:x\.com|twitter\.com)/i/broadcasts/([A-Za-z0-9]{1,64})/?'
    r'(?:\?[^\s#]*)?(?:#[^\s]*)?', re.IGNORECASE)
_STATUS = re.compile(
    r'https://(?:www\.)?(?:x\.com|twitter\.com)/([A-Za-z0-9_]{1,15})/status/(\d{1,25})/?'
    r'(?:\?[^\s#]*)?(?:#[^\s]*)?', re.IGNORECASE)
_TWITCH = re.compile(
    r'https://(?:(?:www|m)\.)?twitch\.tv/([A-Za-z0-9_]{1,25})/?'
    r'(?:\?[^\s#]*)?(?:#[^\s]*)?', re.IGNORECASE)
_TWITCH_PAGES = {'directory', 'downloads', 'jobs', 'login', 'logout', 'p',
                 'search', 'settings', 'signup', 'subscriptions', 'turbo',
                 'videos', 'wallet', 'inventory', 'drops', 'friends', 'following'}
_KICK = re.compile(
    r'https://(?:www\.)?kick\.com/([A-Za-z0-9_-]{1,64})/?'
    r'(?:\?[^\s#]*)?(?:#[^\s]*)?', re.IGNORECASE)
_KICK_PAGES = {'auth', 'browse', 'categories', 'dashboard', 'following', 'login',
               'search', 'signup', 'video', 'videos'}


def broadcast_url(value):
    """Admite solo enlaces públicos de retransmisiones de X."""
    match = _BROADCAST.fullmatch(value.strip())
    if not match:
        raise ValueError('Pega un enlace https://x.com/i/broadcasts/…')
    return 'https://x.com/i/broadcasts/' + match[1]


def x_content_url(value):
    """Normaliza una retransmisión o una publicación de X con video."""
    value = value.strip()
    match = _BROADCAST.fullmatch(value)
    if match:
        return 'https://x.com/i/broadcasts/' + match[1]
    match = _STATUS.fullmatch(value)
    if match:
        return f'https://x.com/{match[1]}/status/{match[2]}'
    raise ValueError('Pega un enlace de retransmisión o de una publicación con video de X.')


def x_content_kind(value):
    """Clasifica una URL ya admitida sin consultar la red."""
    return 'broadcast' if _BROADCAST.fullmatch(x_content_url(value)) else 'status'


def content_url(value):
    """Fuentes públicas autorizadas: X y canales en directo compatibles."""
    value = value.strip()
    from urllib.parse import urlsplit
    if (urlsplit(value).hostname or '').lower() in {
            'youtube.com', 'www.youtube.com', 'm.youtube.com', 'youtu.be',
            'facebook.com', 'www.facebook.com', 'm.facebook.com', 'web.facebook.com', 'fb.watch'}:
        from .source_extension.registry import normalize_url
        source = normalize_url(value)
        if source.platform.value == 'facebook' and '/watch/live' in urlsplit(value).path:
            return source.canonical_url.replace('/watch/', '/watch/live/')
        return source.canonical_url
    match = _TWITCH.fullmatch(value)
    if match and match[1].lower() not in _TWITCH_PAGES:
        return 'https://www.twitch.tv/' + match[1].lower()
    match = _KICK.fullmatch(value)
    if match and match[1].lower() not in _KICK_PAGES:
        return 'https://kick.com/' + match[1].lower()
    try:
        return x_content_url(value)
    except ValueError:
        raise ValueError('Pega un directo de Kick/Twitch o una retransmisión/publicación con video de X.') from None


def content_kind(value):
    selected = content_url(value)
    if selected.startswith('https://www.youtube.com/'):
        return 'youtube_live'
    if selected.startswith('https://www.facebook.com/'):
        return 'facebook'
    if _TWITCH.fullmatch(selected):
        return 'twitch_live'
    if _KICK.fullmatch(selected):
        return 'kick_live'
    return x_content_kind(selected)


def content_platform(value):
    kind = content_kind(value)
    return {'twitch_live': 'twitch', 'kick_live': 'kick',
            'youtube_live': 'youtube', 'facebook': 'facebook'}.get(kind, 'x')
