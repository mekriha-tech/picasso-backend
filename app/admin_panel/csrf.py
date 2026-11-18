"""CSRF protection for the admin panel's state-changing forms.

A random token is kept in the signed session cookie and embedded in every POST form as a hidden
`csrf_token` field (`{{ csrf_field(request) }}` in templates). POST routes depend on
`verify_csrf`, which rejects the request with 403 unless the submitted token matches. The login
form is exempt: there is no session to protect yet.
"""
import secrets

from fastapi import Form, HTTPException, Request
from markupsafe import Markup, escape

SESSION_KEY = "csrf_token"


def get_csrf_token(request: Request) -> str:
    token = request.session.get(SESSION_KEY)
    if not token:
        token = secrets.token_urlsafe(32)
        request.session[SESSION_KEY] = token
    return token


def csrf_field(request: Request) -> Markup:
    return Markup('<input type="hidden" name="csrf_token" value="{}">').format(
        escape(get_csrf_token(request))
    )


async def verify_csrf(request: Request, csrf_token: str = Form(default="")) -> None:
    expected = request.session.get(SESSION_KEY)
    if not expected or not secrets.compare_digest(expected.encode(), csrf_token.encode()):
        raise HTTPException(status_code=403, detail="Invalid or missing CSRF token.")
