from django.db import models
from django.contrib.auth import get_user_model

User = get_user_model()


class PuntoVenta(models.Model):
    nombre = models.CharField(max_length=100)
    ciudad = models.CharField(max_length=100, blank=True)
    usuario = models.OneToOneField(
        User,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="punto_venta",
    )

    def __str__(self):
        return self.nombre


class Proveedor(models.Model):
    nombre = models.CharField(max_length=150)
    nit = models.CharField(max_length=50, blank=True)
    email = models.EmailField(blank=True)
    telefono = models.CharField(max_length=50, blank=True)
    creado_en = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return self.nombre


class Factura(models.Model):
    ESTADOS = [
        ("pendiente", "Pendiente"),
        ("pagada", "Pagada"),
    ]

    proveedor = models.ForeignKey(Proveedor, on_delete=models.PROTECT)
    punto_venta = models.ForeignKey(PuntoVenta, on_delete=models.PROTECT)
    numero_factura = models.CharField(max_length=50)
    fecha_factura = models.DateField()
    valor_factura = models.DecimalField(max_digits=14, decimal_places=2)
    total_pagado = models.DecimalField(max_digits=14, decimal_places=2, default=0)
    estado = models.CharField(max_length=10, choices=ESTADOS, default="pendiente")
    creado_en = models.DateTimeField(auto_now_add=True)
    actualizado_en = models.DateTimeField(auto_now=True)
    creado_por = models.ForeignKey(User, null=True, blank=True, on_delete=models.SET_NULL)

    confirmado_pago = models.BooleanField(default=False)
    confirmado_fecha = models.DateTimeField(null=True, blank=True)
    confirmado_por_email = models.EmailField(null=True, blank=True)

    class Meta:
        ordering = ["-fecha_factura", "-id"]

    def __str__(self):
        return f"{self.numero_factura} - {self.proveedor}"

    @property
    def saldo(self):
        return (self.valor_factura or 0) - (self.total_pagado or 0)


class PagoLote(models.Model):
    proveedor = models.ForeignKey(Proveedor, on_delete=models.PROTECT, related_name="lotes")
    fecha_pago = models.DateField()
    pagado_por = models.CharField(max_length=150)
    comprobante = models.FileField(upload_to="comprobantes/")
    notas = models.TextField(blank=True)
    creado_en = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-fecha_pago", "-id"]

    def __str__(self):
        return f"Lote #{self.pk} — {self.proveedor.nombre} — {self.fecha_pago}"


class Pago(models.Model):
    factura = models.ForeignKey(Factura, on_delete=models.CASCADE, related_name="pagos")
    fecha_pago = models.DateField()
    valor_pagado = models.DecimalField(max_digits=14, decimal_places=2)
    pagado_por = models.CharField(max_length=150, blank=True)
    comprobante = models.FileField(upload_to="comprobantes/", blank=True, null=True)
    notas = models.TextField(blank=True)
    creado_en = models.DateTimeField(auto_now_add=True)
    lote = models.ForeignKey("PagoLote", null=True, blank=True, on_delete=models.SET_NULL, related_name="pagos")

    class Meta:
        ordering = ["-fecha_pago", "-id"]

    def __str__(self):
        return f"Pago {self.valor_pagado} - {self.factura}"


class PuntoVentaUsuario(models.Model):
    user = models.OneToOneField(User, on_delete=models.CASCADE, related_name="pv_map")
    punto_venta = models.ForeignKey(PuntoVenta, on_delete=models.CASCADE, related_name="usuarios")

    class Meta:
        verbose_name = "Asignación de usuario a Punto de Venta"
        verbose_name_plural = "Asignaciones usuario–PDV"

    def __str__(self):
        return f"{self.user.username} → {self.punto_venta.nombre}"


class CorreoEnvioLog(models.Model):
    TIPO_CHOICES = [
        ("individual", "Individual"),
        ("lote", "Lote"),
    ]

    tipo = models.CharField(max_length=20, choices=TIPO_CHOICES)
    factura = models.ForeignKey(Factura, null=True, blank=True, on_delete=models.CASCADE, related_name="logs_correo")
    pago = models.ForeignKey(Pago, null=True, blank=True, on_delete=models.SET_NULL, related_name="logs_correo")
    lote = models.ForeignKey(PagoLote, null=True, blank=True, on_delete=models.SET_NULL, related_name="logs_correo")
    enviado_a = models.EmailField(blank=True)
    asunto = models.CharField(max_length=255, blank=True)
    exito = models.BooleanField(default=False)
    detalle = models.TextField(blank=True)
    creado_en = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-creado_en", "-id"]
        verbose_name = "Log de envío de correo"
        verbose_name_plural = "Logs de envío de correo"
        indexes = [
            models.Index(fields=["tipo", "creado_en"]),
            models.Index(fields=["factura", "creado_en"]),
            models.Index(fields=["lote", "creado_en"]),
        ]

    def __str__(self):
        base = self.factura.numero_factura if self.factura_id else f"Lote #{self.lote_id}" if self.lote_id else "Correo"
        return f"{self.get_tipo_display()} - {base} - {'OK' if self.exito else 'ERROR'}"
