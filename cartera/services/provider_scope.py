from django.core.exceptions import PermissionDenied
from django.shortcuts import get_object_or_404

from cartera.models import Factura, NotificacionProveedor, Pago, PagoLote, Proveedor, ProveedorUsuario


def proveedor_links(user, *, require_active=True):
    qs = ProveedorUsuario.objects.select_related("proveedor", "user").filter(user=user)
    if require_active:
        qs = qs.filter(activo=True)
    return qs.order_by("proveedor__nombre", "id")


def proveedores_activos(user):
    return Proveedor.objects.filter(usuarios_portal__in=proveedor_links(user)).distinct().order_by("nombre")


def proveedor_ids(user):
    return list(proveedores_activos(user).values_list("id", flat=True))


def require_portal_access(user):
    ids = proveedor_ids(user)
    if not ids:
        raise PermissionDenied("No tienes un proveedor activo asociado al portal.")
    return ids


def user_can_confirm(user, proveedor):
    return proveedor_links(user).filter(proveedor=proveedor, puede_confirmar_pagos=True).exists()


def require_can_confirm(user, proveedor):
    if not user_can_confirm(user, proveedor):
        raise PermissionDenied("Tu usuario no tiene permiso para confirmar pagos de este proveedor.")


def facturas_visibles(user):
    ids = require_portal_access(user)
    return Factura.objects.select_related("proveedor", "punto_venta").filter(proveedor_id__in=ids)


def pagos_visibles(user):
    ids = require_portal_access(user)
    return Pago.objects.select_related("factura", "factura__proveedor", "factura__punto_venta", "lote").filter(
        factura__proveedor_id__in=ids
    )


def lotes_visibles(user):
    ids = require_portal_access(user)
    return PagoLote.objects.select_related("proveedor").prefetch_related("pagos__factura__punto_venta").filter(
        proveedor_id__in=ids
    )


def notificaciones_visibles(user):
    ids = require_portal_access(user)
    return NotificacionProveedor.objects.select_related("proveedor", "factura", "pago", "lote").filter(
        usuario=user,
        proveedor_id__in=ids,
    )


def get_factura_for_user(user, pk):
    return get_object_or_404(facturas_visibles(user).prefetch_related("pagos__lote", "eventos_auditoria"), pk=pk)


def get_pago_for_user(user, pk):
    return get_object_or_404(pagos_visibles(user), pk=pk)


def get_lote_for_user(user, pk):
    return get_object_or_404(lotes_visibles(user), pk=pk)


def get_notificacion_for_user(user, pk):
    return get_object_or_404(notificaciones_visibles(user), pk=pk)


def validate_comprobante_access(user, pago):
    if not pagos_visibles(user).filter(pk=pago.pk).exists():
        raise PermissionDenied("No tienes permiso para ver este comprobante.")
    if not (pago.comprobante and pago.comprobante.name):
        raise PermissionDenied("Este pago no tiene comprobante disponible.")
    return pago
