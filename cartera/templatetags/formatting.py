from django import template

from cartera.scoping import get_user_pdv

register = template.Library()

NOVEDAD_MOTIVO_LABELS = {
    "comprobante_no_abre": "Comprobante no abre",
    "valor_no_coincide": "Valor no coincide",
    "no_identifico_pago": "No identifico el pago",
    "factura_no_corresponde": "Factura no corresponde",
    "pago_parcial": "Pago recibido parcialmente",
    "otro": "Otro",
}

@register.filter
def miles(value):
    """
    Formatea con punto de miles y sin decimales.
    Acepta Decimal, int o str convertible a número.
    """
    try:
        n = float(value)
    except (TypeError, ValueError):
        return value
    # 1) convierte 12345.67 -> "12,346"
    s = f"{n:,.0f}"
    # 2) cambia coma por punto: "12.346"
    return s.replace(",", ".")


@register.filter
def motivo_novedad(value):
    raw = (value or "").strip()
    if not raw:
        return "Novedad"
    return NOVEDAD_MOTIVO_LABELS.get(raw, raw.replace("_", " ").capitalize())


@register.simple_tag(takes_context=True)
def user_pv(context):
    """
    Devuelve el Punto de Venta del usuario autenticado o None.
    No rompe si no existe el mapeo.
    """
    request = context.get("request")
    user = getattr(request, "user", None)
    return get_user_pdv(user)
