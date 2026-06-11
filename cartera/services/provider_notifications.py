from django.urls import reverse

from cartera.models import EventoAuditoria, NotificacionProveedor, ProveedorUsuario

from .audit import registrar_evento


def _usuarios_destino(proveedor):
    return ProveedorUsuario.objects.select_related("user").filter(
        proveedor=proveedor,
        activo=True,
        recibe_notificaciones=True,
    )


def crear_notificacion(
    *,
    usuario,
    proveedor,
    tipo,
    titulo,
    mensaje="",
    factura=None,
    pago=None,
    lote=None,
    url_destino="",
    request=None,
    metadata=None,
):
    notif = NotificacionProveedor.objects.create(
        usuario=usuario,
        proveedor=proveedor,
        tipo=tipo,
        titulo=titulo,
        mensaje=mensaje or "",
        factura=factura,
        pago=pago,
        lote=lote,
        url_destino=url_destino or "",
    )
    registrar_evento(
        EventoAuditoria.TIPO_NOTIFICACION_GENERADA,
        factura=factura,
        pago=pago,
        lote=lote,
        usuario=usuario,
        request=request,
        metadata={
            "origen": "portal_proveedor",
            "proveedor_id": proveedor.pk,
            "notificacion_id": notif.pk,
            "tipo": tipo,
            **(metadata or {}),
        },
    )
    return notif


def notificar_pago_registrado(pago, *, request=None):
    proveedor = pago.factura.proveedor
    if pago.lote_id:
        url = reverse("portal_proveedor_lote_detail", args=[pago.lote_id])
        tipo = NotificacionProveedor.TIPO_LOTE_REGISTRADO
        titulo = f"Lote #{pago.lote_id} registrado"
        mensaje = "Se registró un pago por lote asociado a tus facturas."
        created = []
        for link in _usuarios_destino(proveedor):
            notif, was_created = NotificacionProveedor.objects.get_or_create(
                usuario=link.user,
                proveedor=proveedor,
                tipo=tipo,
                lote=pago.lote,
                defaults={
                    "titulo": titulo,
                    "mensaje": mensaje,
                    "url_destino": url,
                    "factura": pago.factura,
                    "pago": pago,
                },
            )
            if was_created:
                registrar_evento(
                    EventoAuditoria.TIPO_NOTIFICACION_GENERADA,
                    factura=pago.factura,
                    pago=pago,
                    lote=pago.lote,
                    usuario=link.user,
                    request=request,
                    metadata={
                        "origen": "portal_proveedor",
                        "proveedor_id": proveedor.pk,
                        "notificacion_id": notif.pk,
                        "tipo": tipo,
                    },
                )
                created.append(notif)
        return created

    url = reverse("portal_proveedor_factura_detail", args=[pago.factura_id])
    return [
        crear_notificacion(
            usuario=link.user,
            proveedor=proveedor,
            tipo=NotificacionProveedor.TIPO_PAGO_REGISTRADO,
            titulo=f"Pago registrado para factura {pago.factura.numero_factura}",
            mensaje="Hay un nuevo pago disponible para revisar y confirmar.",
            factura=pago.factura,
            pago=pago,
            url_destino=url,
            request=request,
        )
        for link in _usuarios_destino(proveedor)
    ]


def notificar_correo_enviado(*, factura=None, pago=None, lote=None, request=None, exito=False):
    proveedor = factura.proveedor if factura else lote.proveedor if lote else None
    if not proveedor:
        return []
    url = reverse("portal_proveedor_lote_detail", args=[lote.pk]) if lote else reverse("portal_proveedor_factura_detail", args=[factura.pk])
    return [
        crear_notificacion(
            usuario=link.user,
            proveedor=proveedor,
            tipo=NotificacionProveedor.TIPO_CORREO_ENVIADO,
            titulo="Correo de confirmación enviado" if exito else "Correo de confirmación no enviado",
            mensaje="Se registró el resultado del envío de correo de confirmación.",
            factura=factura,
            pago=pago,
            lote=lote,
            url_destino=url,
            request=request,
            metadata={"exito": exito},
        )
        for link in _usuarios_destino(proveedor)
    ]


def notificar_confirmacion(*, proveedor, usuario_actor, factura=None, pago=None, lote=None, request=None):
    tipo = NotificacionProveedor.TIPO_CONFIRMACION_LOTE if lote else NotificacionProveedor.TIPO_CONFIRMACION_PAGO
    titulo = "Lote confirmado" if lote else "Pago confirmado"
    url = reverse("portal_proveedor_lote_detail", args=[lote.pk]) if lote else reverse("portal_proveedor_factura_detail", args=[factura.pk])
    return [
        crear_notificacion(
            usuario=link.user,
            proveedor=proveedor,
            tipo=tipo,
            titulo=titulo,
            mensaje="La recepción fue confirmada desde el portal de proveedores.",
            factura=factura,
            pago=pago,
            lote=lote,
            url_destino=url,
            request=request,
            metadata={"usuario_actor_id": getattr(usuario_actor, "pk", None)},
        )
        for link in _usuarios_destino(proveedor)
    ]


def notificar_novedad(*, proveedor, usuario_actor, factura=None, pago=None, lote=None, motivo="", request=None):
    url = reverse("portal_proveedor_lote_detail", args=[lote.pk]) if lote else reverse("portal_proveedor_factura_detail", args=[factura.pk])
    return [
        crear_notificacion(
            usuario=link.user,
            proveedor=proveedor,
            tipo=NotificacionProveedor.TIPO_NOVEDAD,
            titulo="Novedad reportada",
            mensaje="Se registró una novedad desde el portal de proveedores.",
            factura=factura,
            pago=pago,
            lote=lote,
            url_destino=url,
            request=request,
            metadata={"usuario_actor_id": getattr(usuario_actor, "pk", None), "motivo": motivo},
        )
        for link in _usuarios_destino(proveedor)
    ]


def marcar_notificacion_leida(notificacion, *, request=None):
    if not notificacion.leida:
        notificacion.leida = True
        notificacion.save(update_fields=["leida"])
        registrar_evento(
            EventoAuditoria.TIPO_NOTIFICACION_LEIDA,
            factura=notificacion.factura,
            pago=notificacion.pago,
            lote=notificacion.lote,
            usuario=notificacion.usuario,
            request=request,
            metadata={
                "origen": "portal_proveedor",
                "proveedor_id": notificacion.proveedor_id,
                "notificacion_id": notificacion.pk,
            },
        )
    return notificacion
