from django.core.exceptions import PermissionDenied

from .models import Factura, Pago, PuntoVentaUsuario


def is_global_user(user):
    return bool(user and user.is_authenticated and (user.is_staff or user.is_superuser))


def get_user_pdv(user):
    if not user or not user.is_authenticated or is_global_user(user):
        return None
    try:
        return user.pv_map.punto_venta
    except PuntoVentaUsuario.DoesNotExist:
        return None


def ensure_user_scope(user):
    if is_global_user(user):
        return None
    pv = get_user_pdv(user)
    if not pv:
        raise PermissionDenied("El usuario no tiene un Punto de Venta asignado.")
    return pv


def scoped_facturas(user):
    qs = Factura.objects.select_related("proveedor", "punto_venta")
    pv = ensure_user_scope(user)
    if pv:
        qs = qs.filter(punto_venta=pv)
    return qs


def scoped_pagos(user):
    qs = Pago.objects.select_related("factura", "factura__proveedor", "factura__punto_venta", "lote")
    pv = ensure_user_scope(user)
    if pv:
        qs = qs.filter(factura__punto_venta=pv)
    return qs


def resolve_allowed_pdv(user, requested_pdv=None):
    if is_global_user(user):
        return requested_pdv
    pv = ensure_user_scope(user)
    if requested_pdv and requested_pdv.pk != pv.pk:
        raise PermissionDenied("No tiene permiso para usar otro Punto de Venta.")
    return pv
