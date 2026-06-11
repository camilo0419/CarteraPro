from decimal import Decimal

from django.db import transaction
from django.utils import timezone

from cartera.models import EventoAuditoria, Factura

from .audit import registrar_evento
from .payments import crear_pago


@transaction.atomic
def guardar_factura_desde_form(
    factura: Factura,
    *,
    created=False,
    usuario=None,
    request=None,
    auto_payment_note="Pago auto-generado al marcar la factura como PAGADA.",
) -> Factura:
    if created and usuario and getattr(usuario, "is_authenticated", False) and not factura.creado_por_id:
        factura.creado_por = usuario

    if (factura.estado or "").lower() == "pagada":
        factura.total_pagado = factura.valor_factura or Decimal("0")
    else:
        factura.estado = "pendiente"
        factura.total_pagado = Decimal("0")

    factura.save()
    registrar_evento(
        EventoAuditoria.TIPO_FACTURA_CREADA if created else EventoAuditoria.TIPO_FACTURA_EDITADA,
        factura=factura,
        usuario=usuario,
        request=request,
        metadata={
            "numero_factura": factura.numero_factura,
            "estado": factura.estado,
            "valor_factura": factura.valor_factura,
            "punto_venta_id": factura.punto_venta_id,
            "proveedor_id": factura.proveedor_id,
        },
    )

    if factura.estado == "pagada" and not factura.pagos.exists():
        pagado_por = f"PDV - {factura.punto_venta.nombre}" if factura.punto_venta else "OFICINA"
        crear_pago(
            factura=factura,
            fecha_pago=timezone.localdate(),
            valor_pagado=factura.valor_factura or Decimal("0"),
            pagado_por=pagado_por,
            notas=auto_payment_note,
            usuario=usuario,
            request=request,
        )
    return factura
