"""Portable local assets for the supported inline Markdown subset (stdlib)."""
from pathlib import Path
import re
import shutil
from urllib.parse import urlsplit

LINK = re.compile(r'(!?\[[^\]\n]*\]\()([^\s)]+)(\))')


def local_assets(body, article):
    """Resolve only explicit assets/ references, without escaping that directory."""
    root = Path(article).resolve().parent / 'assets'
    found = {}
    for match in LINK.finditer(body):
        url = match.group(2)
        if urlsplit(url).scheme or url.startswith('//') or url.startswith('#'):
            continue
        if not url.startswith('assets/'):
            # Site-relative ordinary links are allowed; local images must be portable.
            if match.group(1).startswith('!'):
                raise ValueError('Local images must use assets/ paths or absolute HTTPS URLs')
            continue
        if not re.fullmatch(r'assets/[A-Za-z0-9_./-]+', url):
            raise ValueError('Asset paths must use simple ASCII names without URL parameters')
        path = root.parent / url
        resolved = path.resolve()
        if root.is_symlink() or any(p.is_symlink() for p in [path, *path.parents] if p != root.parent):
            raise ValueError('Asset symlinks are not supported')
        if root.resolve() not in resolved.parents or '..' in Path(url).parts:
            raise ValueError('Asset path escapes assets/')
        if not resolved.is_file():
            raise ValueError('Missing asset: ' + url)
        found[url] = resolved
    return found


def export_assets(body, article, folder, slug):
    if not re.fullmatch(r'[a-z0-9][a-z0-9-]*', slug):
        raise ValueError('File export slug must contain lowercase letters, digits or hyphens')
    sources = local_assets(body, article)
    urls, files = {}, []
    for url, source in sources.items():
        relative = slug + '-assets/' + url[len('assets/'):]
        target = Path(folder) / relative
        if any(p.is_symlink() for p in [target, *target.parents]):
            raise ValueError('Export destination symlinks are not supported')
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(source, target)
        urls[url] = relative
        files.append(str(target))
    return LINK.sub(lambda m: m.group(1) + urls.get(m.group(2), m.group(2)) + m.group(3), body), files
