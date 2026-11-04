"""Serve only Django Ninja's packaged documentation assets on the dev API.

No shared Caddy changes, file-system aliases, collectstatic job, or CDN dependency.
Do not turn this into a general-purpose static file server.
"""
from __future__ import annotations

import mimetypes
from pathlib import Path

from django.http import FileResponse, Http404, HttpResponseNotAllowed
from ninja import __file__ as ninja_package_file

_ALLOWED_EXTENSIONS = frozenset({
    ".css", ".js", ".png", ".svg", ".ico", ".woff", ".woff2",
    ".ttf", ".eot", ".map",
})


def ninja_docs_asset(request, asset: str):
    """Serve only files inside the installed Ninja package's static/ninja tree."""
    if request.method not in {"GET", "HEAD"}:
        return HttpResponseNotAllowed(["GET", "HEAD"])
    if not asset or any(part in {"", ".", ".."} for part in asset.split("/")):
        raise Http404("Documentation asset not found")

    package = Path(ninja_package_file).resolve(strict=True).parent
    root = (package / "static" / "ninja").resolve(strict=True)
    target = (root / asset).resolve()
    if (
        not target.is_relative_to(root)
        or target.suffix.lower() not in _ALLOWED_EXTENSIONS
        or not target.is_file()
    ):
        raise Http404("Documentation asset not found")

    content_type, _ = mimetypes.guess_type(target.name)
    return FileResponse(target.open("rb"), content_type=content_type or "application/octet-stream")
