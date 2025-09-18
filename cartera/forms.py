from django import forms
from django.utils import timezone

from .models import Factura, Pago, PuntoVenta, PuntoVentaUsuario, PagoLote


def get_user_pdv(user):
    if not user or not user.is_authenticated or user.is_staff or user.is_superuser:
        return None
    try:
        return user.pv_map.punto_venta
    except PuntoVentaUsuario.DoesNotExist:
        return None


# === Widget helper: siempre en ISO para <input type="date"> ===
class ISODateInput(forms.DateInput):
    input_type = "date"

    def __init__(self, attrs=None):
        # Formato ISO para que el navegador lo muestre como default
        super().__init__(attrs=attrs or {}, format="%Y-%m-%d")


# ----------------------------
# Facturas
# ----------------------------
class FacturaForm(forms.ModelForm):
    class Meta:
        model = Factura
        fields = ["proveedor", "punto_venta", "numero_factura", "fecha_factura", "valor_factura", "estado"]
        widgets = {
            "fecha_factura": ISODateInput(),
        }

    def __init__(self, *args, user=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.user = user

        self.fields["estado"].choices = [("pendiente", "pendiente"), ("pagada", "pagada")]

        # Solo setear initial en GET (cuando no viene self.data)
        if not self.data and not self.initial.get("fecha_factura"):
            self.fields["fecha_factura"].initial = timezone.localdate()

        if user and not user.is_staff:
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
        if self.user and not self.user.is_staff:
            pv = get_user_pdv(self.user)
            if not pv:
                raise forms.ValidationError("No se pudo identificar el Punto de Venta del usuario.")
            return pv
        pv = self.cleaned_data.get("punto_venta")
        if not pv:
            raise forms.ValidationError("Debes seleccionar un Punto de Venta.")
        return pv

    def clean_numero_factura(self):
        numero = self.cleaned_data.get("numero_factura")
        return numero.upper() if numero else numero


# ----------------------------
# Pagos (individual)
# ----------------------------
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

        # valor_pagado siempre bloqueado e igual al valor de la factura
        self.fields["valor_pagado"].disabled = True
        if factura:
            self.fields["valor_pagado"].initial = factura.valor_factura

        if not self.data:
            self.fields["fecha_pago"].initial = timezone.localdate()

        # STAFF: puede elegir Oficina o cualquier PDV
        if user and user.is_staff:
            choices = [("OFICINA", "OFICINA")]
            for pv in PuntoVenta.objects.order_by("nombre"):
                etiqueta = f"PDV - {pv.nombre}"
                choices.append((etiqueta, etiqueta))
            self.fields["pagado_por"].choices = choices
            if factura and factura.punto_venta:
                self.fields["pagado_por"].initial = f"PDV - {factura.punto_venta.nombre}"
        else:
            # NO STAFF: su PDV + OFICINA
            pv = get_user_pdv(user)
            if pv:
                etiqueta_pdv = f"PDV - {pv.nombre}"
                self.fields["pagado_por"].choices = [
                    ("OFICINA", "OFICINA"),
                    (etiqueta_pdv, etiqueta_pdv),
                ]
                self.fields["pagado_por"].initial = etiqueta_pdv
            else:
                self.fields["pagado_por"].choices = []
                self.fields["pagado_por"].initial = None

    def clean_valor_pagado(self):
        return self.factura.valor_factura if self.factura else self.cleaned_data["valor_pagado"]

    def clean_pagado_por(self):
        seleccionado = self.cleaned_data.get("pagado_por")
        if self.user and self.user.is_staff:
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


class PagoComprobanteForm(forms.ModelForm):
    class Meta:
        model = Pago
        fields = ["comprobante"]
        widgets = {
            "comprobante": forms.ClearableFileInput(
                attrs={"accept": "image/*,application/pdf", "capture": "environment"}
            )
        }


# ----------------------------
# Pagos (lote)
# ----------------------------
class PagoLoteForm(forms.ModelForm):
    pagado_por = forms.ChoiceField(required=True)

    class Meta:
        model = PagoLote
        fields = ["fecha_pago", "pagado_por", "comprobante", "notas"]
        widgets = {
            "fecha_pago": forms.DateInput(attrs={"type": "date"}),
            "notas": forms.Textarea(attrs={"rows": 2}),
            "comprobante": forms.ClearableFileInput(
                attrs={"accept": "image/*,application/pdf", "capture": "environment"}
            ),
        }

    def __init__(self, *args, user=None, pdv_default=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.user = user

        # 🔹 Preestablecer siempre hoy si no hay valor
        if not self.data.get("fecha_pago") and not self.initial.get("fecha_pago"):
            self.fields["fecha_pago"].initial = timezone.localdate()

        # STAFF: Oficina + todos los PDV
        if user and user.is_staff:
            choices = [("OFICINA", "OFICINA")]
            for pv in PuntoVenta.objects.order_by("nombre"):
                etiqueta = f"PDV - {pv.nombre}"
                choices.append((etiqueta, etiqueta))
            self.fields["pagado_por"].choices = choices
            if pdv_default:
                self.fields["pagado_por"].initial = f"PDV - {pdv_default.nombre}"
        else:
            pv = get_user_pdv(user)
            if pv:
                etiqueta = f"PDV - {pv.nombre}"
                self.fields["pagado_por"].choices = [("OFICINA", "OFICINA"), (etiqueta, etiqueta)]
                self.fields["pagado_por"].initial = etiqueta
            else:
                self.fields["pagado_por"].choices = []
                self.fields["pagado_por"].initial = None

    def clean_fecha_pago(self):
        """
        Garantiza que siempre se guarde la fecha de hoy si el usuario no selecciona nada.
        """
        fecha = self.cleaned_data.get("fecha_pago")
        if not fecha:
            return timezone.localdate()
        return fecha

    def clean_pagado_por(self):
        seleccionado = self.cleaned_data.get("pagado_por")
        if self.user and self.user.is_staff:
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