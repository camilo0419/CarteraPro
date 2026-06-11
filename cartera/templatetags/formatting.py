from django import template

from cartera.scoping import get_user_pdv

register = template.Library()

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


@register.simple_tag(takes_context=True)
def user_pv(context):
    """
    Devuelve el Punto de Venta del usuario autenticado o None.
    No rompe si no existe el mapeo.
    """
    request = context.get("request")
    user = getattr(request, "user", None)
    return get_user_pdv(user)
