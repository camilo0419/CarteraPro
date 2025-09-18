from django.contrib import admin
from .models import PuntoVenta, Proveedor, Factura, Pago, PuntoVentaUsuario

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
    # ✅ Evita N+1 y asegura que se resuelva el PDV en la lista
    list_select_related = ("proveedor", "punto_venta")
    ordering = ("-fecha_factura", "-id")


@admin.register(Pago)
class PagoAdmin(admin.ModelAdmin):
    # Columna PDV por relación a factura
    list_display = ("id", "factura", "get_pdv", "fecha_pago", "valor_pagado", "pagado_por", "creado_en")
    list_filter = ("factura__punto_venta", "fecha_pago")
    search_fields = ("factura__numero_factura", "pagado_por", "factura__proveedor__nombre")
    # ✅ Carga el PDV y proveedor junto con la factura para que la columna se vea sin errores/latencia
    list_select_related = ("factura", "factura__punto_venta", "factura__proveedor")
    ordering = ("-fecha_pago", "-id")

    @admin.display(description="Punto de Venta")
    def get_pdv(self, obj):
        return obj.factura.punto_venta.nombre if obj.factura and obj.factura.punto_venta else "-"


@admin.register(PuntoVentaUsuario)
class PuntoVentaUsuarioAdmin(admin.ModelAdmin):
    list_display = ("user", "punto_venta")
    search_fields = ("user__username", "punto_venta__nombre")
    list_select_related = ("user", "punto_venta")
