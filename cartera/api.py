from rest_framework import viewsets, permissions
from rest_framework.decorators import action
from rest_framework.response import Response
from django.db.models import Sum
from .models import Proveedor, PuntoVenta, Factura, Pago
from .serializers import ProveedorSerializer, PuntoVentaSerializer, FacturaSerializer, PagoSerializer

class ProveedorViewSet(viewsets.ReadOnlyModelViewSet):
    queryset = Proveedor.objects.all()
    serializer_class = ProveedorSerializer
    permission_classes = [permissions.IsAuthenticated]
    filterset_fields = ['activo']
    search_fields  = ['nombre','nit','correo']
    ordering_fields = ['nombre']

class PuntoVentaViewSet(viewsets.ReadOnlyModelViewSet):
    queryset = PuntoVenta.objects.all()
    serializer_class = PuntoVentaSerializer
    permission_classes = [permissions.IsAuthenticated]
    filterset_fields = ['activo']
    search_fields  = ['nombre']
    ordering_fields = ['nombre']

class FacturaViewSet(viewsets.ReadOnlyModelViewSet):
    queryset = Factura.objects.select_related('proveedor','punto_venta').all()
    serializer_class = FacturaSerializer
    permission_classes = [permissions.IsAuthenticated]
    filterset_fields = ['estado','punto_venta','proveedor','fecha_factura']
    search_fields  = ['numero_factura','proveedor__nombre','proveedor__nit']
    ordering_fields = ['fecha_factura','valor_factura','total_pagado','actualizado_en']

    @action(detail=False, methods=['get'])
    def resumen(self, request):
        fact = self.get_queryset().exclude(estado='PAGADA')
        total = fact.aggregate(pend=Sum('valor_factura') - Sum('total_pagado'))['pend'] or 0
        por_proveedor = (fact.values('proveedor__nombre')
                             .annotate(pend=Sum('valor_factura') - Sum('total_pagado'))
                             .order_by('-pend'))
        return Response({'total_pendiente': total, 'por_proveedor': list(por_proveedor)})

class PagoViewSet(viewsets.ReadOnlyModelViewSet):
    queryset = Pago.objects.select_related('factura','pagado_por').all()
    serializer_class = PagoSerializer
    permission_classes = [permissions.IsAuthenticated]
    filterset_fields = ['fecha_pago','factura__punto_venta','factura__proveedor']
    ordering_fields = ['fecha_pago','valor_pagado','creado_en']
