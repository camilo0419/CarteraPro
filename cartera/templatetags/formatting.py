from django import template

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
