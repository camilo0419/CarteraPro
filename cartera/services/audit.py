from datetime import date, datetime
from decimal import Decimal

from django.db import models

from cartera.models import EventoAuditoria


def _request_user(request):
    user = getattr(request, "user", None) if request else None
    if user and getattr(user, "is_authenticated", False):
        return user
    return None


def _client_ip(request):
    if not request:
        return None
    forwarded = request.META.get("HTTP_X_FORWARDED_FOR", "")
    if forwarded:
        return forwarded.split(",", 1)[0].strip() or None
    return request.META.get("REMOTE_ADDR") or None


def _user_agent(request):
    if not request:
        return ""
    return (request.META.get("HTTP_USER_AGENT") or "")[:1000]


def _json_safe(value):
    if isinstance(value, (str, int, float, bool)) or value is None:
        return value
    if isinstance(value, Decimal):
        return str(value)
    if isinstance(value, (date, datetime)):
        return value.isoformat()
    if isinstance(value, models.Model):
        return value.pk
    if isinstance(value, dict):
        return {str(k): _json_safe(v) for k, v in value.items()}
    if isinstance(value, (list, tuple, set)):
        return [_json_safe(v) for v in value]
    return str(value)


def registrar_evento(
    tipo,
    *,
    factura=None,
    pago=None,
    lote=None,
    usuario=None,
    request=None,
    metadata=None,
    ip_address=None,
    user_agent="",
):
    actor = usuario or _request_user(request)
    return EventoAuditoria.objects.create(
        tipo=tipo,
        factura=factura,
        pago=pago,
        lote=lote,
        usuario=actor,
        metadata=_json_safe(metadata or {}),
        ip_address=ip_address or _client_ip(request),
        user_agent=user_agent or _user_agent(request),
    )
