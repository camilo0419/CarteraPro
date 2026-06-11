from django.conf import settings
from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.db import models

from .validators import validate_comprobante_file

User = get_user_model()

PAGO_LOTE_MONOPROVEEDOR_ERROR = "Un lote solo puede contener pagos del mismo proveedor."


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


class ProveedorUsuario(models.Model):
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="proveedores_portal")
    proveedor = models.ForeignKey(Proveedor, on_delete=models.CASCADE, related_name="usuarios_portal")
    activo = models.BooleanField(default=True)
    puede_confirmar_pagos = models.BooleanField(default=True)
    recibe_notificaciones = models.BooleanField(default=True)
    creado_en = models.DateTimeField(auto_now_add=True)
    actualizado_en = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["proveedor__nombre", "user__username"]
        verbose_name = "Usuario de proveedor"
        verbose_name_plural = "Usuarios de proveedores"
        constraints = [
            models.UniqueConstraint(fields=["user", "proveedor"], name="unique_usuario_proveedor_portal"),
        ]
        indexes = [
            models.Index(fields=["user", "activo"]),
            models.Index(fields=["proveedor", "activo"]),
        ]

    def __str__(self):
        return f"{self.user} - {self.proveedor}"


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
    """PagoLote es siempre monoproveedor; proveedor es la fuente de verdad."""

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

    def clean(self):
        super().clean()
        validate_comprobante_file(self.comprobante)
        if self.pk and self.proveedor_id:
            if self.pagos.exclude(factura__proveedor_id=self.proveedor_id).exists():
                raise ValidationError(PAGO_LOTE_MONOPROVEEDOR_ERROR)


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

    def clean(self):
        super().clean()
        validate_comprobante_file(self.comprobante)
        if self.lote_id and self.factura_id and self.lote.proveedor_id != self.factura.proveedor_id:
            raise ValidationError({"lote": PAGO_LOTE_MONOPROVEEDOR_ERROR})


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


class EventoAuditoria(models.Model):
    TIPO_FACTURA_CREADA = "factura_creada"
    TIPO_FACTURA_EDITADA = "factura_editada"
    TIPO_PAGO_CREADO = "pago_creado"
    TIPO_PAGO_ELIMINADO = "pago_eliminado"
    TIPO_CORREO_ENVIADO = "correo_enviado"
    TIPO_CONFIRMACION_FACTURA_PUBLICA = "confirmacion_factura_publica"
    TIPO_CONFIRMACION_LOTE_PUBLICA = "confirmacion_lote_publica"
    TIPO_CONFIRMACION_PAGO_PORTAL = "confirmacion_pago_portal"
    TIPO_CONFIRMACION_LOTE_PORTAL = "confirmacion_lote_portal"
    TIPO_COMPROBANTE_VISUALIZADO = "comprobante_visualizado"
    TIPO_NOVEDAD_PROVEEDOR = "novedad_proveedor"
    TIPO_NOTIFICACION_GENERADA = "notificacion_generada"
    TIPO_NOTIFICACION_LEIDA = "notificacion_leida"

    TIPO_CHOICES = [
        (TIPO_FACTURA_CREADA, "Creacion de factura"),
        (TIPO_FACTURA_EDITADA, "Edicion de factura"),
        (TIPO_PAGO_CREADO, "Creacion de pago"),
        (TIPO_PAGO_ELIMINADO, "Eliminacion de pago"),
        (TIPO_CORREO_ENVIADO, "Envio de correo"),
        (TIPO_CONFIRMACION_FACTURA_PUBLICA, "Confirmacion publica de factura"),
        (TIPO_CONFIRMACION_LOTE_PUBLICA, "Confirmacion publica de lote"),
        (TIPO_CONFIRMACION_PAGO_PORTAL, "Confirmacion de pago desde portal proveedor"),
        (TIPO_CONFIRMACION_LOTE_PORTAL, "Confirmacion de lote desde portal proveedor"),
        (TIPO_COMPROBANTE_VISUALIZADO, "Visualizacion de comprobante"),
        (TIPO_NOVEDAD_PROVEEDOR, "Novedad reportada por proveedor"),
        (TIPO_NOTIFICACION_GENERADA, "Notificacion generada"),
        (TIPO_NOTIFICACION_LEIDA, "Notificacion marcada como leida"),
    ]

    tipo = models.CharField(max_length=60, choices=TIPO_CHOICES, db_index=True)
    factura = models.ForeignKey(Factura, null=True, blank=True, on_delete=models.SET_NULL, related_name="eventos_auditoria")
    pago = models.ForeignKey(Pago, null=True, blank=True, on_delete=models.SET_NULL, related_name="eventos_auditoria")
    lote = models.ForeignKey(PagoLote, null=True, blank=True, on_delete=models.SET_NULL, related_name="eventos_auditoria")
    usuario = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="eventos_auditoria_cartera",
    )
    creado_en = models.DateTimeField(auto_now_add=True)
    metadata = models.JSONField(default=dict, blank=True)
    ip_address = models.GenericIPAddressField(null=True, blank=True)
    user_agent = models.TextField(blank=True)

    class Meta:
        ordering = ["-creado_en", "-id"]
        verbose_name = "Evento de auditoria"
        verbose_name_plural = "Eventos de auditoria"
        indexes = [
            models.Index(fields=["tipo", "creado_en"]),
            models.Index(fields=["factura", "creado_en"]),
            models.Index(fields=["pago", "creado_en"]),
            models.Index(fields=["lote", "creado_en"]),
        ]

    def __str__(self):
        return f"{self.tipo} #{self.pk}"


class NotificacionProveedor(models.Model):
    TIPO_PAGO_REGISTRADO = "pago_registrado"
    TIPO_LOTE_REGISTRADO = "lote_registrado"
    TIPO_CORREO_ENVIADO = "correo_enviado"
    TIPO_CONFIRMACION_PAGO = "confirmacion_pago"
    TIPO_CONFIRMACION_LOTE = "confirmacion_lote"
    TIPO_NOVEDAD = "novedad"
    TIPO_SISTEMA = "sistema"

    TIPO_CHOICES = [
        (TIPO_PAGO_REGISTRADO, "Pago registrado"),
        (TIPO_LOTE_REGISTRADO, "Lote registrado"),
        (TIPO_CORREO_ENVIADO, "Correo enviado"),
        (TIPO_CONFIRMACION_PAGO, "Confirmacion de pago"),
        (TIPO_CONFIRMACION_LOTE, "Confirmacion de lote"),
        (TIPO_NOVEDAD, "Novedad"),
        (TIPO_SISTEMA, "Sistema"),
    ]

    usuario = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="notificaciones_proveedor")
    proveedor = models.ForeignKey(Proveedor, on_delete=models.CASCADE, related_name="notificaciones")
    factura = models.ForeignKey(Factura, null=True, blank=True, on_delete=models.SET_NULL, related_name="notificaciones_proveedor")
    pago = models.ForeignKey(Pago, null=True, blank=True, on_delete=models.SET_NULL, related_name="notificaciones_proveedor")
    lote = models.ForeignKey(PagoLote, null=True, blank=True, on_delete=models.SET_NULL, related_name="notificaciones_proveedor")
    tipo = models.CharField(max_length=40, choices=TIPO_CHOICES, db_index=True)
    titulo = models.CharField(max_length=160)
    mensaje = models.TextField(blank=True)
    leida = models.BooleanField(default=False)
    url_destino = models.CharField(max_length=255, blank=True)
    creada_en = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-creada_en", "-id"]
        verbose_name = "Notificacion de proveedor"
        verbose_name_plural = "Notificaciones de proveedores"
        indexes = [
            models.Index(fields=["usuario", "leida", "creada_en"]),
            models.Index(fields=["proveedor", "tipo", "creada_en"]),
        ]

    def __str__(self):
        return f"{self.titulo} - {self.usuario}"
