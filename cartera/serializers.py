from rest_framework import serializers
from .models import Proveedor, Factura, Pago, PuntoVenta

class PuntoVentaSerializer(serializers.ModelSerializer):
    class Meta:
        model = PuntoVenta
        fields = "__all__"

class ProveedorSerializer(serializers.ModelSerializer):
    class Meta:
        model = Proveedor
        fields = "__all__"

class FacturaSerializer(serializers.ModelSerializer):
    proveedor_nombre = serializers.CharField(source='proveedor.nombre', read_only=True)
    punto_venta_nombre = serializers.CharField(source='punto_venta.nombre', read_only=True)

    class Meta:
        model = Factura
        fields = "__all__"

class PagoSerializer(serializers.ModelSerializer):
    proveedor_email = serializers.SerializerMethodField()

    def get_proveedor_email(self, obj):
        prov = obj.factura.proveedor
        return getattr(prov, 'email', None) or getattr(prov, 'correo', None)

    class Meta:
        model = Pago
        fields = "__all__"
