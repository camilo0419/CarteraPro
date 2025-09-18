from django.urls import path, include
from rest_framework.routers import DefaultRouter
from . import views

router = DefaultRouter()
router.register(r'api/proveedores', views.ProveedorViewSet, basename='proveedor')
router.register(r'api/facturas', views.FacturaViewSet, basename='factura')
router.register(r'api/pagos', views.PagoViewSet, basename='pago')

urlpatterns = [
    path('', views.DashboardView.as_view(), name='dashboard'),

    # Facturas
    path('facturas/nueva/', views.FacturaCreateView.as_view(), name='factura_create'),
    path('facturas/pendientes/', views.FacturaPendientesView.as_view(), name='facturas_pendientes'),
    path('facturas/<int:pk>/', views.FacturaDetalleView.as_view(), name='factura_detalle'),
    path('facturas/<int:pk>/editar/', views.FacturaUpdateView.as_view(), name='factura_update'),

    # Pagos
    path('pagos/', views.PagosListView.as_view(), name='pagos_list'),
    path('facturas/<int:pk>/pagar/', views.PagoCreateView.as_view(), name='pago_create'),

    # API
    path('', include(router.urls)),

    # Correo (manual) + Confirmación por clic
    path('pagos/<int:pk>/enviar-email/', views.PagoEnviarEmailView.as_view(), name='pago_enviar_email'),
    path('pagos/confirmar/<str:token>/', views.ConfirmarPagoView.as_view(), name='pago_confirmar'),

    # Pago por lote
    path('pagos/lote/nuevo/', views.PagoLoteCreateView.as_view(), name='pago_lote_create'),
    path('pagos/confirmar-lote/<str:token>/', views.ConfirmarPagoLoteView.as_view(), name='pago_lote_confirmar'),


    # urls.py (agrega esta línea en urlpatterns, sección Pagos)
    path("pagos/<int:pk>/adjuntar/", views.PagoAdjuntarComprobanteView.as_view(), name="pago_adjuntar"),

]
