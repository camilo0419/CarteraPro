from decimal import Decimal

from django.utils import timezone
from rest_framework import serializers

from .models import Factura, Pago, Proveedor, PuntoVenta
from .scoping import get_user_pdv, is_global_user, resolve_allowed_pdv
from .services.invoices import guardar_factura_desde_form
from .services.payments import crear_pago
from .validators import validate_comprobante_file


MAX_FACTURA_API = Decimal("10000000")


class PuntoVentaSerializer(serializers.ModelSerializer):
    class Meta:
        model = PuntoVenta
        fields = ["id", "nombre", "ciudad", "usuario"]
        read_only_fields = ["id"]


class ProveedorSerializer(serializers.ModelSerializer):
    class Meta:
        model = Proveedor
        fields = ["id", "nombre", "nit", "email", "telefono", "creado_en"]
        read_only_fields = ["id", "creado_en"]


class FacturaSerializer(serializers.ModelSerializer):
    proveedor_nombre = serializers.CharField(source="proveedor.nombre", read_only=True)
    punto_venta_nombre = serializers.CharField(source="punto_venta.nombre", read_only=True)
    saldo = serializers.SerializerMethodField()
    fecha_pago = serializers.SerializerMethodField()
    punto_venta = serializers.PrimaryKeyRelatedField(queryset=PuntoVenta.objects.all(), required=False)

    class Meta:
        model = Factura
        fields = [
            "id",
            "proveedor",
            "proveedor_nombre",
            "punto_venta",
            "punto_venta_nombre",
            "numero_factura",
            "fecha_factura",
            "valor_factura",
            "total_pagado",
            "saldo",
            "estado",
            "fecha_pago",
            "creado_en",
            "actualizado_en",
            "creado_por",
            "confirmado_pago",
            "confirmado_fecha",
            "confirmado_por_email",
        ]
        read_only_fields = [
            "id",
            "proveedor_nombre",
            "punto_venta_nombre",
            "total_pagado",
            "saldo",
            "fecha_pago",
            "creado_en",
            "actualizado_en",
            "creado_por",
            "confirmado_pago",
            "confirmado_fecha",
            "confirmado_por_email",
        ]

    def get_saldo(self, obj):
        return obj.saldo

    def get_fecha_pago(self, obj):
        pago = obj.pagos.order_by("-fecha_pago", "-id").first()
        return pago.fecha_pago.isoformat() if pago and pago.fecha_pago else None

    def _user(self):
        request = self.context.get("request")
        return getattr(request, "user", None)

    def validate_numero_factura(self, value):
        numero = (value or "").strip().upper()
        if not numero:
            raise serializers.ValidationError("Debes ingresar el número de factura.")
        return numero

    def validate_valor_factura(self, value):
        if value is None:
            raise serializers.ValidationError("Debes ingresar el valor de la factura.")
        if value <= 0:
            raise serializers.ValidationError("El valor de la factura debe ser mayor que cero.")
        if value > MAX_FACTURA_API:
            raise serializers.ValidationError("El valor máximo permitido es 10.000.000.")
        return value.quantize(Decimal("0.01"))

    def validate(self, attrs):
        user = self._user()
        requested_pdv = attrs.get("punto_venta", getattr(self.instance, "punto_venta", None))
        try:
            allowed_pdv = resolve_allowed_pdv(user, requested_pdv)
        except Exception as exc:
            raise serializers.ValidationError({"punto_venta": str(exc)})

        if not is_global_user(user):
            attrs["punto_venta"] = allowed_pdv
        elif not requested_pdv and not self.instance:
            raise serializers.ValidationError({"punto_venta": "Debes seleccionar un Punto de Venta."})

        proveedor = attrs.get("proveedor", getattr(self.instance, "proveedor", None))
        numero = attrs.get("numero_factura", getattr(self.instance, "numero_factura", None))
        if proveedor and numero:
            qs = Factura.objects.filter(proveedor=proveedor, numero_factura__iexact=numero)
            if self.instance:
                qs = qs.exclude(pk=self.instance.pk)
            if qs.exists():
                raise serializers.ValidationError({"numero_factura": "Ya existe una factura con ese proveedor y ese número."})

        if self.instance and (self.instance.pagos.exists() or self.instance.confirmado_pago):
            protected = {"proveedor", "punto_venta", "numero_factura", "fecha_factura", "valor_factura", "estado"}
            if protected.intersection(attrs):
                raise serializers.ValidationError("Esta factura ya tiene pago o confirmación y no se puede editar por API.")

        return attrs

    def create(self, validated_data):
        request = self.context.get("request")
        user = getattr(request, "user", None)
        factura = Factura(**validated_data)
        return guardar_factura_desde_form(
            factura,
            created=True,
            usuario=user,
            request=request,
            auto_payment_note="Pago auto-generado al crear la factura como PAGADA via API.",
        )

    def update(self, instance, validated_data):
        request = self.context.get("request")
        user = getattr(request, "user", None)
        for field, value in validated_data.items():
            setattr(instance, field, value)
        return guardar_factura_desde_form(
            instance,
            created=False,
            usuario=user,
            request=request,
            auto_payment_note="Pago auto-generado al marcar la factura como PAGADA via API.",
        )


class PagoSerializer(serializers.ModelSerializer):
    proveedor_email = serializers.SerializerMethodField()
    proveedor_nombre = serializers.CharField(source="factura.proveedor.nombre", read_only=True)
    factura_numero = serializers.CharField(source="factura.numero_factura", read_only=True)
    punto_venta = serializers.IntegerField(source="factura.punto_venta_id", read_only=True)
    punto_venta_nombre = serializers.CharField(source="factura.punto_venta.nombre", read_only=True)

    class Meta:
        model = Pago
        fields = [
            "id",
            "factura",
            "factura_numero",
            "proveedor_nombre",
            "proveedor_email",
            "punto_venta",
            "punto_venta_nombre",
            "fecha_pago",
            "valor_pagado",
            "pagado_por",
            "comprobante",
            "notas",
            "lote",
            "creado_en",
        ]
        read_only_fields = [
            "id",
            "factura_numero",
            "proveedor_nombre",
            "proveedor_email",
            "punto_venta",
            "punto_venta_nombre",
            "valor_pagado",
            "lote",
            "creado_en",
        ]

    def get_proveedor_email(self, obj):
        prov = obj.factura.proveedor
        return getattr(prov, "email", None)

    def _user(self):
        request = self.context.get("request")
        return getattr(request, "user", None)

    def validate_comprobante(self, value):
        validate_comprobante_file(value)
        return value

    def validate_factura(self, factura):
        user = self._user()
        pv = get_user_pdv(user)
        if pv and factura.punto_venta_id != pv.id:
            raise serializers.ValidationError("No tiene permiso para registrar pagos en otro Punto de Venta.")
        if not is_global_user(user) and not pv:
            raise serializers.ValidationError("El usuario no tiene un Punto de Venta asignado.")
        return factura

    def validate_pagado_por(self, value):
        seleccionado = value or ""
        user = self._user()
        if is_global_user(user):
            if seleccionado == "OFICINA":
                return seleccionado
            if seleccionado.startswith("PDV - "):
                nombre = seleccionado.split("PDV - ", 1)[-1]
                if PuntoVenta.objects.filter(nombre__iexact=nombre).exists():
                    return seleccionado
            raise serializers.ValidationError("Selección inválida de 'Pagado por'.")

        pv = get_user_pdv(user)
        etiqueta_pdv = f"PDV - {pv.nombre}" if pv else None
        if seleccionado == "OFICINA" or (etiqueta_pdv and seleccionado == etiqueta_pdv):
            return seleccionado
        raise serializers.ValidationError("No tiene permiso para registrar pagos a nombre de otro punto.")

    def validate(self, attrs):
        factura = attrs.get("factura", getattr(self.instance, "factura", None))
        if self.instance and "factura" in attrs and attrs["factura"] != self.instance.factura:
            raise serializers.ValidationError("No se puede cambiar la factura asociada a un pago existente.")
        if not self.instance and factura and factura.pagos.exists():
            raise serializers.ValidationError("Esta factura ya tiene un pago registrado.")
        if factura and factura.confirmado_pago:
            raise serializers.ValidationError("Esta factura ya fue confirmada y no admite cambios de pago por API.")
        return attrs

    def create(self, validated_data):
        request = self.context.get("request")
        user = getattr(request, "user", None)
        factura = validated_data["factura"]
        return crear_pago(
            factura=factura,
            fecha_pago=validated_data.get("fecha_pago") or timezone.localdate(),
            valor_pagado=factura.valor_factura,
            pagado_por=validated_data.get("pagado_por", ""),
            comprobante=validated_data.get("comprobante"),
            notas=validated_data.get("notas", ""),
            usuario=user,
            request=request,
        )
