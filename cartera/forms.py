from decimal import Decimal, InvalidOperation

from django import forms
from django.utils import timezone

from .models import Factura, Pago, PuntoVenta, PagoLote, Proveedor
from .scoping import get_user_pdv, is_global_user
from .validators import validate_comprobante_file


MAX_FACTURA = Decimal("10000000")
ALERTA_FACTURA = Decimal("1000000")


class ISODateInput(forms.DateInput):
    input_type = "date"

    def __init__(self, attrs=None):
        super().__init__(attrs=attrs or {}, format="%Y-%m-%d")


class ProveedorChoiceField(forms.ModelChoiceField):
    def label_from_instance(self, obj):
        correo = (obj.email or "sin correo asignado").strip()
        return f"{obj.nombre} - {correo}"


class FacturaForm(forms.ModelForm):
    proveedor = ProveedorChoiceField(queryset=Proveedor.objects.all().order_by("nombre"), required=True)
    valor_factura = forms.CharField(
        required=True,
        widget=forms.TextInput(attrs={"inputmode": "decimal", "autocomplete": "off"}),
    )
    confirmar_valor_alto = forms.BooleanField(required=False, widget=forms.HiddenInput())

    class Meta:
        model = Factura
        fields = ["proveedor", "punto_venta", "numero_factura", "fecha_factura", "valor_factura", "estado"]
        widgets = {
            "fecha_factura": ISODateInput(),
            "numero_factura": forms.TextInput(attrs={"maxlength": 50}),
        }

    def __init__(self, *args, user=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.user = user
        self.fields["estado"].choices = [("pendiente", "pendiente"), ("pagada", "pagada")]

        if not self.data and not self.initial.get("fecha_factura"):
            self.fields["fecha_factura"].initial = timezone.localdate()

        # FIX: cuando se edita una factura existente, evitar que el valor inicial
        # llegue como "300000.00" al input de texto, porque el JS lo infla.
        if not self.is_bound and getattr(self.instance, "pk", None) and self.instance.valor_factura is not None:
            try:
                entero = int(Decimal(self.instance.valor_factura))
                self.initial["valor_factura"] = str(entero)
                self.fields["valor_factura"].initial = str(entero)
            except Exception:
                pass

        if user and not is_global_user(user):
            pv = get_user_pdv(user)
            if pv:
                self.fields["punto_venta"].queryset = PuntoVenta.objects.filter(pk=pv.pk)
                self.fields["punto_venta"].initial = pv
                self.fields["punto_venta"].disabled = True
            else:
                self.fields["punto_venta"].queryset = PuntoVenta.objects.none()
                self.fields["punto_venta"].disabled = True
        else:
            self.fields["punto_venta"].queryset = PuntoVenta.objects.all().order_by("nombre")

    def clean_punto_venta(self):
        if self.user and not is_global_user(self.user):
            pv = get_user_pdv(self.user)
            if not pv:
                raise forms.ValidationError("No se pudo identificar el Punto de Venta del usuario.")
            return pv
        pv = self.cleaned_data.get("punto_venta")
        if not pv:
            raise forms.ValidationError("Debes seleccionar un Punto de Venta.")
        return pv

    def clean_numero_factura(self):
        numero = (self.cleaned_data.get("numero_factura") or "").strip().upper()
        if not numero:
            raise forms.ValidationError("Debes ingresar el número de factura.")
        return numero

    def clean_valor_factura(self):
        valor = self.cleaned_data.get("valor_factura")

        if isinstance(valor, Decimal):
            valor_decimal = valor
        elif isinstance(valor, str):
            raw = valor.strip()

            raw = raw.replace(" ", "")

            if "," in raw:
                raw = raw.replace(".", "").replace(",", ".")
            elif "." in raw:
                parts = raw.split(".")
                if len(parts) > 2:
                    raw = "".join(parts)
                elif len(parts) == 2 and len(parts[1]) == 2 and len(parts[0]) > 3:
                    raw = ".".join(parts)
                else:
                    raw = "".join(parts)

            try:
                valor_decimal = Decimal(raw or "0")
            except InvalidOperation:
                raise forms.ValidationError("Valor de factura inválido.")
        else:
            valor_decimal = valor

        if valor_decimal is None:
            raise forms.ValidationError("Debes ingresar el valor de la factura.")
        if valor_decimal > MAX_FACTURA:
            raise forms.ValidationError("El valor máximo permitido es 10.000.000.")
        if valor_decimal <= 0:
            raise forms.ValidationError("El valor de la factura debe ser mayor que cero.")

        return valor_decimal.quantize(Decimal("0.01"))

    def clean(self):
        cleaned = super().clean()
        proveedor = cleaned.get("proveedor")
        numero = cleaned.get("numero_factura")
        valor = cleaned.get("valor_factura")
        confirmar_valor_alto = self.data.get("confirmar_valor_alto") in {"1", "true", "True", True}

        if proveedor and numero:
            qs = Factura.objects.filter(proveedor=proveedor, numero_factura__iexact=numero)
            if self.instance.pk:
                qs = qs.exclude(pk=self.instance.pk)
            if qs.exists():
                self.add_error("numero_factura", "Ya existe una factura con ese proveedor y ese número.")

        if valor and valor > ALERTA_FACTURA and not confirmar_valor_alto:
            self.add_error("valor_factura", f"Confirma el valor alto de la factura: ${int(valor):,}".replace(",", "."))

        return cleaned


class PagoForm(forms.ModelForm):
    pagado_por = forms.ChoiceField(required=True)

    class Meta:
        model = Pago
        fields = ["valor_pagado", "fecha_pago", "pagado_por", "comprobante", "notas"]
        widgets = {
            "fecha_pago": ISODateInput(),
            "notas": forms.Textarea(attrs={"rows": 2}),
        }

    def __init__(self, *args, user=None, factura=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.user = user
        self.factura = factura
        self.fields["valor_pagado"].disabled = True
        if factura:
            self.fields["valor_pagado"].initial = factura.valor_factura
        if not self.data:
            self.fields["fecha_pago"].initial = timezone.localdate()
        if is_global_user(user):
            choices = [("OFICINA", "OFICINA")]
            for pv in PuntoVenta.objects.order_by("nombre"):
                etiqueta = f"PDV - {pv.nombre}"
                choices.append((etiqueta, etiqueta))
            self.fields["pagado_por"].choices = choices
            if factura and factura.punto_venta:
                self.fields["pagado_por"].initial = f"PDV - {factura.punto_venta.nombre}"
        else:
            pv = get_user_pdv(user)
            if pv:
                etiqueta_pdv = f"PDV - {pv.nombre}"
                self.fields["pagado_por"].choices = [("OFICINA", "OFICINA"), (etiqueta_pdv, etiqueta_pdv)]
                self.fields["pagado_por"].initial = etiqueta_pdv
            else:
                self.fields["pagado_por"].choices = []

    def clean_valor_pagado(self):
        return self.factura.valor_factura if self.factura else self.cleaned_data["valor_pagado"]

    def clean_pagado_por(self):
        seleccionado = self.cleaned_data.get("pagado_por")
        if is_global_user(self.user):
            if seleccionado == "OFICINA":
                return seleccionado
            if seleccionado and seleccionado.startswith("PDV - "):
                nombre = seleccionado.split("PDV - ", 1)[-1]
                if PuntoVenta.objects.filter(nombre__iexact=nombre).exists():
                    return seleccionado
            raise forms.ValidationError("Selección inválida de 'Pagado por'.")
        pv = get_user_pdv(self.user)
        etiqueta_pdv = f"PDV - {pv.nombre}" if pv else None
        if seleccionado == "OFICINA":
            return seleccionado
        if etiqueta_pdv and seleccionado == etiqueta_pdv:
            return seleccionado
        raise forms.ValidationError("No tiene permiso para registrar pagos a nombre de otro punto.")

    def clean_comprobante(self):
        comprobante = self.cleaned_data.get("comprobante")
        validate_comprobante_file(comprobante)
        return comprobante


class PagoComprobanteForm(forms.ModelForm):
    class Meta:
        model = Pago
        fields = ["comprobante"]
        widgets = {
            "comprobante": forms.ClearableFileInput(attrs={"accept": "image/*,application/pdf", "capture": "environment"})
        }

    def clean_comprobante(self):
        comprobante = self.cleaned_data.get("comprobante")
        validate_comprobante_file(comprobante)
        return comprobante


class PagoLoteForm(forms.ModelForm):
    pagado_por = forms.ChoiceField(required=True)

    class Meta:
        model = PagoLote
        fields = ["fecha_pago", "pagado_por", "comprobante", "notas"]
        widgets = {
            "fecha_pago": ISODateInput(),
            "notas": forms.Textarea(attrs={"rows": 2}),
            "comprobante": forms.ClearableFileInput(attrs={"accept": "image/*,application/pdf", "capture": "environment"}),
        }

    def __init__(self, *args, user=None, pdv_default=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.user = user
        if not self.data.get("fecha_pago") and not self.initial.get("fecha_pago"):
            self.initial["fecha_pago"] = timezone.localdate()

        if is_global_user(user):
            all_pv = [(f"PDV - {pv.nombre}", f"PDV - {pv.nombre}") for pv in PuntoVenta.objects.order_by("nombre")]
            base = [("OFICINA", "OFICINA")]
            valor_pdv = None
            if pdv_default and getattr(pdv_default, "nombre", None):
                valor_pdv = f"PDV - {pdv_default.nombre}"
                all_pv = [opt for opt in all_pv if opt[0] != valor_pdv]
                choices = [(valor_pdv, valor_pdv)] + base + all_pv
            else:
                choices = base + all_pv
            self.fields["pagado_por"].choices = choices
            if not self.is_bound and valor_pdv:
                self.initial["pagado_por"] = valor_pdv
                self.fields["pagado_por"].initial = valor_pdv
        else:
            pv = get_user_pdv(user)
            if pv:
                etiqueta = f"PDV - {pv.nombre}"
                self.fields["pagado_por"].choices = [(etiqueta, etiqueta), ("OFICINA", "OFICINA")]
                if not self.is_bound:
                    self.initial["pagado_por"] = etiqueta
                    self.fields["pagado_por"].initial = etiqueta
            else:
                self.fields["pagado_por"].choices = [("OFICINA", "OFICINA")]
                if not self.is_bound:
                    self.initial["pagado_por"] = "OFICINA"
                    self.fields["pagado_por"].initial = "OFICINA"

    def clean_fecha_pago(self):
        fecha = self.cleaned_data.get("fecha_pago")
        return fecha or timezone.localdate()

    def clean_pagado_por(self):
        seleccionado = self.cleaned_data.get("pagado_por")
        if is_global_user(self.user):
            if seleccionado == "OFICINA":
                return seleccionado
            if seleccionado and seleccionado.startswith("PDV - "):
                nombre = seleccionado.split("PDV - ", 1)[-1]
                if PuntoVenta.objects.filter(nombre__iexact=nombre).exists():
                    return seleccionado
            raise forms.ValidationError("Selección inválida de 'Pagado por'.")
        pv = get_user_pdv(self.user)
        etiqueta_pdv = f"PDV - {pv.nombre}" if pv else None
        if seleccionado == "OFICINA":
            return seleccionado
        if etiqueta_pdv and seleccionado == etiqueta_pdv:
            return seleccionado
        raise forms.ValidationError("No tiene permiso para registrar pagos a nombre de otro punto.")

    def clean_comprobante(self):
        comprobante = self.cleaned_data.get("comprobante")
        validate_comprobante_file(comprobante)
        return comprobante


class NovedadProveedorForm(forms.Form):
    MOTIVOS = [
        ("valor_no_coincide", "Valor no coincide"),
        ("comprobante_no_abre", "Comprobante no abre"),
        ("no_identifico_pago", "No identifico el pago"),
        ("factura_no_corresponde", "Factura no corresponde"),
        ("pago_parcial", "Pago recibido parcialmente"),
        ("otro", "Otro"),
    ]

    motivo = forms.ChoiceField(choices=MOTIVOS)
    detalle = forms.CharField(
        required=True,
        widget=forms.Textarea(attrs={"rows": 4, "maxlength": 1000}),
        max_length=1000,
    )
