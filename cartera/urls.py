from django.urls import include, path
from rest_framework.routers import DefaultRouter

from . import provider_views, views

router = DefaultRouter()
router.register(r"api/proveedores", views.ProveedorViewSet, basename="proveedor")
router.register(r"api/facturas", views.FacturaViewSet, basename="factura")
router.register(r"api/pagos", views.PagoViewSet, basename="pago")

urlpatterns = [
    path("portal-proveedor/", provider_views.PortalProveedorDashboardView.as_view(), name="portal_proveedor_dashboard"),
    path("portal-proveedor/facturas/", provider_views.PortalFacturaListView.as_view(), name="portal_proveedor_facturas"),
    path("portal-proveedor/facturas/<int:pk>/", provider_views.PortalFacturaDetailView.as_view(), name="portal_proveedor_factura_detail"),
    path("portal-proveedor/pagos/", provider_views.PortalPagoListView.as_view(), name="portal_proveedor_pagos"),
    path("portal-proveedor/pagos/<int:pk>/confirmar/", provider_views.PortalPagoConfirmView.as_view(), name="portal_proveedor_pago_confirmar"),
    path("portal-proveedor/lotes/<int:pk>/", provider_views.PortalLoteDetailView.as_view(), name="portal_proveedor_lote_detail"),
    path("portal-proveedor/lotes/<int:pk>/confirmar/", provider_views.PortalLoteConfirmView.as_view(), name="portal_proveedor_lote_confirmar"),
    path("portal-proveedor/novedades/", provider_views.PortalNovedadListView.as_view(), name="portal_proveedor_novedades"),
    path("portal-proveedor/pagos/<int:pk>/novedad/", provider_views.PortalPagoNovedadView.as_view(), name="portal_proveedor_pago_novedad"),
    path("portal-proveedor/lotes/<int:pk>/novedad/", provider_views.PortalLoteNovedadView.as_view(), name="portal_proveedor_lote_novedad"),
    path("portal-proveedor/notificaciones/", provider_views.PortalNotificacionListView.as_view(), name="portal_proveedor_notificaciones"),
    path("portal-proveedor/notificaciones/<int:pk>/leer/", provider_views.PortalNotificacionLeerView.as_view(), name="portal_proveedor_notificacion_leer"),
    path("portal-proveedor/comprobantes/<int:pago_id>/", provider_views.PortalComprobanteView.as_view(), name="portal_proveedor_comprobante"),
    path("", views.DashboardView.as_view(), name="dashboard"),
    path("facturas/nueva/", views.FacturaCreateView.as_view(), name="factura_create"),
    path("facturas/pendientes/", views.facturas_pendientes_view, name="facturas_pendientes"),
    path("facturas/pagadas/", views.pagos_list_view, name="pagos_list"),
    path("facturas/todas/", views.facturas_todas_view, name="facturas_todas"),
    path("facturas/<int:pk>/", views.FacturaDetalleView.as_view(), name="factura_detalle"),
    path("facturas/<int:pk>/editar/", views.FacturaUpdateView.as_view(), name="factura_update"),
    path("facturas/<int:pk>/pagar/", views.PagoCreateView.as_view(), name="pago_create"),
    path("pagos/<int:pk>/adjuntar/", views.PagoAdjuntarComprobanteView.as_view(), name="pago_adjuntar"),
    path("pagos/<int:pk>/enviar-email/", views.PagoEnviarEmailView.as_view(), name="pago_enviar_email"),
    path("pagos/confirmar/<str:token>/", views.ConfirmarPagoView.as_view(), name="pago_confirmar"),
    path("pagos/lote/nuevo/", views.PagoLoteCreateView.as_view(), name="pago_lote_create"),
    path("pagos/confirmar-lote/<str:token>/", views.ConfirmarPagoLoteView.as_view(), name="pago_lote_confirmar"),
    path("analitica/", views.analytics_dashboard, name="analytics_dashboard"),
    path("", include(router.urls)),
]
