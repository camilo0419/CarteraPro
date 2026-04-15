from django.contrib import admin
from .models import PuntoVenta, Proveedor, Factura, Pago, PuntoVentaUsuario, PagoLote, CorreoEnvioLog


@admin.register(PuntoVenta)
class PuntoVentaAdmin(admin.ModelAdmin):
    list_display = ("nombre", "ciudad", "usuario")
    search_fields = ("nombre", "ciudad", "usuario__username")


@admin.register(Proveedor)
class ProveedorAdmin(admin.ModelAdmin):
    list_display = ("nombre", "nit", "email", "telefono", "creado_en")
    search_fields = ("nombre", "nit", "email")


@admin.register(Factura)
class FacturaAdmin(admin.ModelAdmin):
    list_display = (
        "numero_factura", "proveedor", "punto_venta", "fecha_factura",
        "valor_factura", "total_pagado", "estado", "confirmado_pago"
    )
    list_filter = ("estado", "punto_venta", "proveedor", "confirmado_pago")
    search_fields = ("numero_factura", "proveedor__nombre", "punto_venta__nombre")
    list_select_related = ("proveedor", "punto_venta")
    ordering = ("-fecha_factura", "-id")


@admin.register(Pago)
class PagoAdmin(admin.ModelAdmin):
    list_display = ("id", "factura", "get_pdv", "fecha_pago", "valor_pagado", "pagado_por", "creado_en")
    list_filter = ("factura__punto_venta", "fecha_pago")
    search_fields = ("factura__numero_factura", "pagado_por", "factura__proveedor__nombre")
    list_select_related = ("factura", "factura__punto_venta", "factura__proveedor")
    ordering = ("-fecha_pago", "-id")

    @admin.display(description="Punto de Venta")
    def get_pdv(self, obj):
        return obj.factura.punto_venta.nombre if obj.factura and obj.factura.punto_venta else "-"


@admin.register(PagoLote)
class PagoLoteAdmin(admin.ModelAdmin):
    list_display = ("id", "proveedor", "fecha_pago", "pagado_por", "creado_en")
    search_fields = ("proveedor__nombre", "pagado_por")
    list_select_related = ("proveedor",)
    ordering = ("-fecha_pago", "-id")


@admin.register(PuntoVentaUsuario)
class PuntoVentaUsuarioAdmin(admin.ModelAdmin):
    list_display = ("user", "punto_venta")
    search_fields = ("user__username", "punto_venta__nombre")
    list_select_related = ("user", "punto_venta")


@admin.register(CorreoEnvioLog)
class CorreoEnvioLogAdmin(admin.ModelAdmin):
    list_display = ("id", "tipo", "factura", "lote", "enviado_a", "exito", "creado_en")
    list_filter = ("tipo", "exito")
    search_fields = ("factura__numero_factura", "enviado_a", "asunto", "detalle", "lote__id")
    list_select_related = ("factura", "lote")
    ordering = ("-creado_en", "-id")
