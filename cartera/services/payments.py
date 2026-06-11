from decimal import Decimal

from django.core.exceptions import PermissionDenied, ValidationError
from django.db import transaction
from django.db.models import Sum
from django.utils import timezone

from cartera.models import EventoAuditoria, Factura, PAGO_LOTE_MONOPROVEEDOR_ERROR, Pago, PagoLote
from cartera.utils import enviar_recibo_lote, enviar_recibo_pago

from .audit import registrar_evento
from .provider_notifications import notificar_pago_registrado


def _decimal(value):
    if value is None:
        return Decimal("0")
    if isinstance(value, Decimal):
        return value
    return Decimal(str(value))


def recalcular_factura(factura: Factura, *, save=True) -> Factura:
    total = factura.pagos.aggregate(total=Sum("valor_pagado"))["total"] or Decimal("0")
    factura.total_pagado = total
    factura.estado = "pagada" if total >= _decimal(factura.valor_factura) else "pendiente"
    if save:
        factura.save(update_fields=["total_pagado", "estado"])
    return factura


def validar_lote_monoproveedor(*, factura: Factura, lote: PagoLote | None):
    if lote and factura.proveedor_id != lote.proveedor_id:
        raise ValidationError(PAGO_LOTE_MONOPROVEEDOR_ERROR)


@transaction.atomic
def crear_pago(
    *,
    factura: Factura,
    fecha_pago=None,
    valor_pagado=None,
    pagado_por="",
    comprobante=None,
    notas="",
    lote: PagoLote | None = None,
    usuario=None,
    request=None,
    registrar_auditoria=True,
) -> Pago:
    validar_lote_monoproveedor(factura=factura, lote=lote)
    pago = Pago.objects.create(
        factura=factura,
        fecha_pago=fecha_pago or timezone.localdate(),
        valor_pagado=_decimal(valor_pagado if valor_pagado is not None else factura.valor_factura),
        pagado_por=pagado_por or "",
        comprobante=comprobante,
        notas=notas or "",
        lote=lote,
    )
    factura = recalcular_factura(factura)
    if registrar_auditoria:
        registrar_evento(
            EventoAuditoria.TIPO_PAGO_CREADO,
            factura=factura,
            pago=pago,
            lote=lote,
            usuario=usuario,
            request=request,
            metadata={
                "valor_pagado": pago.valor_pagado,
                "fecha_pago": pago.fecha_pago,
                "pagado_por": pago.pagado_por,
                "lote_id": lote.pk if lote else None,
            },
        )
        notificar_pago_registrado(pago, request=request)
    return pago


@transaction.atomic
def eliminar_pago_seguro(pago: Pago, *, usuario=None, request=None) -> Factura:
    factura = pago.factura
    if pago.lote_id:
        raise ValidationError("No se puede eliminar por API un pago perteneciente a un lote.")
    if factura.confirmado_pago:
        raise ValidationError("No se puede eliminar un pago de una factura ya confirmada.")

    metadata = {
        "pago_id": pago.pk,
        "factura_id": factura.pk,
        "valor_pagado": pago.valor_pagado,
        "fecha_pago": pago.fecha_pago,
        "pagado_por": pago.pagado_por,
    }
    pago.delete()
    factura = recalcular_factura(factura)
    registrar_evento(
        EventoAuditoria.TIPO_PAGO_ELIMINADO,
        factura=factura,
        usuario=usuario,
        request=request,
        metadata=metadata,
    )
    return factura


def _es_pago_contado(pago: Pago) -> bool:
    return "auto-generado" in (pago.notas or "").lower()


def enviar_correo_pago_si_aplica(request, pago: Pago):
    if _es_pago_contado(pago):
        return False, "Pago de contado", "contado"
    if not (pago.comprobante and pago.comprobante.name):
        return False, "Pago sin comprobante", "sin_comprobante"
    destinatario = ((pago.factura.proveedor.email or "").strip() if pago.factura.proveedor else "")
    if not destinatario:
        return False, "Proveedor sin email", "sin_email"
    ok, info = enviar_recibo_pago(request, pago)
    return ok, info, "enviado" if ok else "error"


def enviar_correo_lote_si_aplica(request, lote: PagoLote):
    ok, info = enviar_recibo_lote(request, lote)
    return ok, info, "enviado" if ok else "error"


@transaction.atomic
def confirmar_factura(
    factura: Factura,
    *,
    pago=None,
    request=None,
    usuario=None,
    email=None,
    event_type=EventoAuditoria.TIPO_CONFIRMACION_FACTURA_PUBLICA,
    metadata=None,
) -> Factura:
    if not factura.confirmado_pago:
        factura.confirmado_pago = True
        factura.confirmado_fecha = timezone.now()
        factura.confirmado_por_email = email if email is not None else factura.proveedor.email
        factura.save(update_fields=["confirmado_pago", "confirmado_fecha", "confirmado_por_email"])
        registrar_evento(
            event_type,
            factura=factura,
            pago=pago,
            usuario=usuario,
            request=request,
            metadata={"confirmado_por_email": factura.confirmado_por_email, **(metadata or {})},
        )
    return factura


@transaction.atomic
def confirmar_lote(
    lote: PagoLote,
    *,
    request=None,
    usuario=None,
    proveedor=None,
    event_type=EventoAuditoria.TIPO_CONFIRMACION_LOTE_PUBLICA,
    metadata=None,
):
    if proveedor is not None and lote.proveedor_id != proveedor.pk:
        raise PermissionDenied("No tienes permiso para confirmar este lote.")

    pagos = list(lote.pagos.select_related("factura"))
    if any(pago.factura.proveedor_id != lote.proveedor_id for pago in pagos):
        raise ValidationError(PAGO_LOTE_MONOPROVEEDOR_ERROR)

    ahora = timezone.now()
    facturas_confirmadas = []
    for pago in pagos:
        factura = pago.factura
        if not factura.confirmado_pago:
            factura.confirmado_pago = True
            factura.confirmado_fecha = ahora
            factura.confirmado_por_email = lote.proveedor.email
            factura.save(update_fields=["confirmado_pago", "confirmado_fecha", "confirmado_por_email"])
            facturas_confirmadas.append(factura.pk)

    if facturas_confirmadas:
        registrar_evento(
            event_type,
            lote=lote,
            usuario=usuario,
            request=request,
            metadata={
                "facturas_confirmadas": facturas_confirmadas,
                "facturas": [p.factura_id for p in pagos],
                "pagos": [p.pk for p in pagos],
                "proveedor_id": lote.proveedor_id,
                **(metadata or {}),
            },
        )
    return ahora, pagos
