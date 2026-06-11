import mimetypes
import os

from django.conf import settings
from django.core.files.storage import FileSystemStorage
from django.core.mail import EmailMultiAlternatives
from django.core.signing import BadSignature, SignatureExpired, TimestampSigner
from django.template.loader import render_to_string
from django.urls import reverse
from django.utils.html import strip_tags

from .models import CorreoEnvioLog, EventoAuditoria, PagoLote

signer = TimestampSigner()


def firmar_token(pago_id: int) -> str:
    return signer.sign(str(pago_id))


def validar_token(token: str, max_age=60 * 60 * 24 * 7):
    try:
        valor = signer.unsign(token, max_age=max_age)
        return True, int(valor)
    except SignatureExpired:
        return False, "token_expirado"
    except BadSignature:
        return False, "token_invalido"


def firmar_token_lote(lote_id: int) -> str:
    return signer.sign(f"lote:{lote_id}")


def validar_token_lote(token: str, max_age=60 * 60 * 24 * 7):
    try:
        valor = signer.unsign(token, max_age=max_age)
        if not str(valor).startswith("lote:"):
            return False, "token_invalido"
        return True, int(str(valor).split(":", 1)[1])
    except SignatureExpired:
        return False, "token_expirado"
    except BadSignature:
        return False, "token_invalido"


def _attach_fieldfile(email, ff):
    filename = os.path.basename(ff.name)
    mime, _ = mimetypes.guess_type(filename)
    mime = mime or "application/octet-stream"
    if isinstance(getattr(ff, "storage", None), FileSystemStorage):
        email.attach_file(ff.path, mimetype=mime)
    else:
        ff.open("rb")
        try:
            content = ff.read()
        finally:
            ff.close()
        email.attach(filename, content, mime)


def _log_envio(*, tipo, factura=None, pago=None, lote=None, enviado_a="", asunto="", exito=False, detalle="", request=None):
    CorreoEnvioLog.objects.create(
        tipo=tipo,
        factura=factura,
        pago=pago,
        lote=lote,
        enviado_a=enviado_a or "",
        asunto=asunto or "",
        exito=bool(exito),
        detalle=detalle or "",
    )
    from .services.audit import registrar_evento

    registrar_evento(
        EventoAuditoria.TIPO_CORREO_ENVIADO,
        factura=factura,
        pago=pago,
        lote=lote,
        request=request,
        metadata={
            "tipo": tipo,
            "enviado_a": enviado_a,
            "asunto": asunto,
            "exito": bool(exito),
            "detalle": detalle,
        },
    )
    from .services.provider_notifications import notificar_correo_enviado

    notificar_correo_enviado(factura=factura, pago=pago, lote=lote, request=request, exito=bool(exito))


def enviar_recibo_pago(request, pago):
    factura = pago.factura
    proveedor = factura.proveedor
    destinatario = (proveedor.email or "").strip()
    if not destinatario:
        return False, "Proveedor sin email"
    if not (pago.comprobante and pago.comprobante.name):
        return False, "Pago sin comprobante"

    token = firmar_token(pago.id)
    path_rel = reverse("pago_confirmar", args=[token])
    confirm_url = request.build_absolute_uri(path_rel) if request else settings.SITE_URL.rstrip("/") + path_rel
    static_base = settings.SITE_URL.rstrip("/") + settings.STATIC_URL
    logo_url = static_base + "cartera/img/logo-email.png"
    saldo_restante = max((factura.valor_factura or 0) - (factura.total_pagado or 0), 0)
    ctx = {
        "proveedor": proveedor,
        "factura": factura,
        "pago": pago,
        "saldo": saldo_restante,
        "url_confirmacion": confirm_url,
        "logo_url": logo_url,
    }

    pdv_nombre = getattr(factura.punto_venta, "nombre", "PDV")
    asunto = f"Recibo de pago – Factura {factura.numero_factura} ({pdv_nombre})"
    cuerpo_txt = render_to_string("cartera/emails/recibo_pago.txt", ctx) or ""
    cuerpo_html = render_to_string("cartera/emails/recibo_pago.html", ctx) or ""
    if not cuerpo_txt.strip():
        cuerpo_txt = strip_tags(cuerpo_html) or f"Recibo de pago\n\nConfirma aquí: {confirm_url}"

    email = EmailMultiAlternatives(
        subject=asunto,
        body=cuerpo_txt,
        from_email=getattr(settings, "DEFAULT_FROM_EMAIL", "cartera@fogonylena.net"),
        to=[destinatario],
    )
    email.attach_alternative(cuerpo_html, "text/html")

    try:
        _attach_fieldfile(email, pago.comprobante)
        email.send(fail_silently=False)
        _log_envio(tipo="individual", factura=factura, pago=pago, enviado_a=destinatario, asunto=asunto, exito=True, detalle="Enviado", request=request)
        return True, "Enviado"
    except Exception as e:
        _log_envio(tipo="individual", factura=factura, pago=pago, enviado_a=destinatario, asunto=asunto, exito=False, detalle=str(e), request=request)
        return False, f"Error adjuntando o enviando el comprobante: {e}"


def enviar_recibo_lote(request, lote: PagoLote):
    proveedor = lote.proveedor
    destinatario = (proveedor.email or "").strip()
    if not destinatario:
        return False, "Proveedor sin email"
    if not (lote.comprobante and lote.comprobante.name):
        return False, "Lote sin comprobante"

    token = firmar_token_lote(lote.id)
    path_rel = reverse("pago_lote_confirmar", args=[token])
    confirm_url = request.build_absolute_uri(path_rel) if request else settings.SITE_URL.rstrip("/") + path_rel
    static_base = settings.SITE_URL.rstrip("/") + settings.STATIC_URL
    logo_url = static_base + "cartera/img/logo-email.png"
    pagos = list(lote.pagos.select_related("factura", "factura__punto_venta").all())
    facturas = [p.factura for p in pagos]
    total = sum((f.valor_factura or 0) for f in facturas)
    ctx = {
        "proveedor": proveedor,
        "lote": lote,
        "facturas": facturas,
        "total": total,
        "url_confirmacion": confirm_url,
        "logo_url": logo_url,
    }

    asunto = f"Recibo de pago – Lote #{lote.id} – {proveedor.nombre}"
    cuerpo_txt = render_to_string("cartera/emails/recibo_pago_lote.txt", ctx) or ""
    cuerpo_html = render_to_string("cartera/emails/recibo_pago_lote.html", ctx) or ""
    if not cuerpo_txt.strip():
        cuerpo_txt = strip_tags(cuerpo_html) or f"Recibo de pago\n\nConfirma aquí: {confirm_url}"

    email = EmailMultiAlternatives(
        subject=asunto,
        body=cuerpo_txt,
        from_email=getattr(settings, "DEFAULT_FROM_EMAIL", "cartera@fogonylena.net"),
        to=[destinatario],
    )
    email.attach_alternative(cuerpo_html, "text/html")

    try:
        _attach_fieldfile(email, lote.comprobante)
        email.send(fail_silently=False)
        for pago in pagos:
            _log_envio(tipo="lote", factura=pago.factura, pago=pago, lote=lote, enviado_a=destinatario, asunto=asunto, exito=True, detalle="Enviado", request=request)
        return True, "Enviado"
    except Exception as e:
        for pago in pagos:
            _log_envio(tipo="lote", factura=pago.factura, pago=pago, lote=lote, enviado_a=destinatario, asunto=asunto, exito=False, detalle=str(e), request=request)
        return False, f"Error adjuntando o enviando el comprobante: {e}"
