import json
from calendar import monthrange
from datetime import date, datetime, timedelta
from decimal import Decimal, InvalidOperation

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.contrib.auth.mixins import LoginRequiredMixin
from django.core.exceptions import ValidationError
from django.core.paginator import Paginator, EmptyPage, PageNotAnInteger
from django.db import transaction
from django.db.models import Count, F, Prefetch, Q, Sum
from django.db.models.functions import TruncMonth
from django.http import HttpResponseRedirect
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse, reverse_lazy
from django.utils import timezone
from django.utils.dateparse import parse_date
from django.views.generic import CreateView, DetailView, TemplateView, UpdateView, View
from rest_framework import filters, permissions, viewsets
from rest_framework.exceptions import PermissionDenied as DRFPermissionDenied
from rest_framework.exceptions import ValidationError as DRFValidationError
from django_filters.rest_framework import DjangoFilterBackend

from .forms import FacturaForm, PagoComprobanteForm, PagoForm, PagoLoteForm
from .models import CorreoEnvioLog, EventoAuditoria, Factura, PAGO_LOTE_MONOPROVEEDOR_ERROR, Pago, PagoLote, Proveedor, PuntoVenta
from .scoping import ensure_user_scope, get_user_pdv, is_global_user, scoped_facturas, scoped_pagos
from .serializers import FacturaSerializer, PagoSerializer, ProveedorSerializer
from .services.invoices import guardar_factura_desde_form
from .services.payments import (
    confirmar_factura,
    confirmar_lote,
    crear_pago,
    eliminar_pago_seguro,
    enviar_correo_lote_si_aplica,
    enviar_correo_pago_si_aplica,
)
from .utils import validar_token, validar_token_lote
from .templatetags.formatting import motivo_novedad

# fix accidental import name typo if referenced elsewhere
ALERTA_FACTURA = Decimal("1000000")


def _d(value, fallback):
    try:
        parsed = parse_date(value) if isinstance(value, str) else None
        return parsed or fallback
    except Exception:
        return fallback


def _month_bounds(today: date):
    first = date(today.year, today.month, 1)
    last = date(today.year, today.month, monthrange(today.year, today.month)[1])
    return first, last


def _safe_div(num, den):
    try:
        if not den or den == 0:
            return Decimal("0")
        return (Decimal(num) / Decimal(den)) * Decimal("100")
    except (InvalidOperation, ZeroDivisionError, TypeError):
        return Decimal("0")


def _es_contado_por_notas(pago):
    n = (pago.notas or "").lower()
    return "auto-generado" in n


def _parse_decimal_search(raw):
    qnum = (raw or "").replace(".", "").replace(",", "").strip()
    if not qnum.isdigit():
        return None
    try:
        return Decimal(qnum)
    except Exception:
        return None


def _format_origen_novedad(value):
    raw = (value or "").strip()
    if raw == "portal_proveedor":
        return "Portal proveedor"
    return raw.replace("_", " ").capitalize() if raw else "Portal proveedor"


def _build_novedades_factura(factura):
    lote_ids = factura.pagos.exclude(lote__isnull=True).values_list("lote_id", flat=True)
    eventos = (
        EventoAuditoria.objects.filter(tipo=EventoAuditoria.TIPO_NOVEDAD_PROVEEDOR)
        .filter(Q(factura=factura) | Q(pago__factura=factura) | Q(lote_id__in=lote_ids))
        .select_related("factura__proveedor", "pago__factura__proveedor", "lote__proveedor", "usuario")
        .distinct()
        .order_by("-creado_en", "-id")
    )
    novedades = []
    for evento in eventos:
        metadata = evento.metadata or {}
        if evento.pago_id:
            proveedor = evento.pago.factura.proveedor
            relacion = f"Pago #{evento.pago_id}"
        elif evento.lote_id:
            proveedor = evento.lote.proveedor
            relacion = f"Lote #{evento.lote_id}"
        elif evento.factura_id:
            proveedor = evento.factura.proveedor
            relacion = f"Factura {evento.factura.numero_factura}"
        else:
            proveedor = factura.proveedor
            relacion = f"Factura {factura.numero_factura}"
        novedades.append({
            "fecha": evento.creado_en,
            "proveedor": proveedor.nombre if proveedor else "",
            "usuario": evento.usuario.get_username() if evento.usuario_id else "",
            "motivo": motivo_novedad(metadata.get("motivo")),
            "detalle": metadata.get("detalle") or "",
            "origen": _format_origen_novedad(metadata.get("origen")),
            "relacion": relacion,
        })
    return novedades


def _base_factura_filters(request, qs, include_estado=None):
    q = (request.GET.get("q") or "").strip()
    prov = (request.GET.get("prov") or "").strip()
    pdv = (request.GET.get("pdv") or "").strip()
    estado = (request.GET.get("estado") or "").strip()
    confirmacion = (request.GET.get("confirmacion") or "").strip()
    mes = (request.GET.get("mes") or "").strip()
    anio = (request.GET.get("anio") or "").strip()
    desde = (request.GET.get("desde") or "").strip()
    hasta = (request.GET.get("hasta") or "").strip()

    if include_estado is not None:
        qs = qs.filter(estado=include_estado)
    elif estado in {"pendiente", "pagada"}:
        qs = qs.filter(estado=estado)

    if prov.isdigit():
        qs = qs.filter(proveedor_id=int(prov))
    if pdv.isdigit() and is_global_user(request.user):
        qs = qs.filter(punto_venta_id=int(pdv))

    if confirmacion == "si":
        qs = qs.filter(confirmado_pago=True)
    elif confirmacion == "no":
        qs = qs.filter(confirmado_pago=False)

    if anio.isdigit():
        qs = qs.filter(fecha_factura__year=int(anio))
    if mes.isdigit() and 1 <= int(mes) <= 12:
        qs = qs.filter(fecha_factura__month=int(mes))

    if desde:
        qs = qs.filter(fecha_factura__gte=_d(desde, date(1900, 1, 1)))
    if hasta:
        qs = qs.filter(fecha_factura__lte=_d(hasta, timezone.localdate()))

    if q:
        decimal_q = _parse_decimal_search(q)
        q_filter = (
            Q(numero_factura__icontains=q)
            | Q(proveedor__nombre__icontains=q)
            | Q(proveedor__nit__icontains=q)
            | Q(punto_venta__nombre__icontains=q)
        )
        if decimal_q is not None:
            q_filter |= Q(valor_factura=decimal_q) | Q(total_pagado=decimal_q)
        qs = qs.filter(q_filter)

    return qs.order_by("-fecha_factura", "-id")


def _paginate(request, qs, per_page=50):
    paginator = Paginator(qs, per_page)
    page = request.GET.get("page")
    try:
        items = paginator.page(page)
    except PageNotAnInteger:
        items = paginator.page(1)
    except EmptyPage:
        items = paginator.page(paginator.num_pages)
    return items


def _attach_payment_dates(facturas):
    for factura in facturas:
        pagos = getattr(factura, "pagos_ordenados", None)
        pago = pagos[0] if pagos else factura.pagos.order_by("-fecha_pago", "-id").first()
        factura.fecha_pago_mostrada = pago.fecha_pago if pago else None
    return facturas


def _factura_listing_context(request, qs, title, include_estado=None, show_estado_filter=True, show_confirm_filter=False, template_tab=""):
    qs = _base_factura_filters(request, qs, include_estado=include_estado)
    page_obj = _paginate(request, qs, per_page=50)
    resumen_por_proveedor = (
        qs.values("proveedor__id", "proveedor__nombre")
        .annotate(facturas=Count("id"), total=Sum(F("valor_factura") - F("total_pagado")))
        .order_by("proveedor__nombre")
    )
    total_general = qs.aggregate(t=Sum(F("valor_factura") - F("total_pagado")))["t"] or 0
    proveedores = Proveedor.objects.order_by("nombre")
    pdvs = PuntoVenta.objects.order_by("nombre") if is_global_user(request.user) else []
    anios = list(scoped_facturas(request.user).dates("fecha_factura", "year", order="DESC"))
    return {
        "title": title,
        "page_obj": page_obj,
        "facturas": page_obj.object_list,
        "resumen_por_proveedor": resumen_por_proveedor,
        "total_general_pendiente": total_general,
        "proveedores": proveedores,
        "pdvs": pdvs,
        "anios": [d.year for d in anios],
        "show_estado_filter": show_estado_filter,
        "show_confirm_filter": show_confirm_filter,
        "show_payment_date": False,
        "date_column_label": "Fecha",
        "tab": template_tab,
        "filters": {
            "q": request.GET.get("q", ""),
            "prov": request.GET.get("prov", ""),
            "pdv": request.GET.get("pdv", ""),
            "estado": request.GET.get("estado", ""),
            "confirmacion": request.GET.get("confirmacion", ""),
            "mes": request.GET.get("mes", ""),
            "anio": request.GET.get("anio", ""),
            "desde": request.GET.get("desde", ""),
            "hasta": request.GET.get("hasta", ""),
        },
    }


class DashboardView(LoginRequiredMixin, TemplateView):
    template_name = "cartera/dashboard.html"

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        qs = scoped_facturas(self.request.user).filter(estado="pendiente")
        ctx["total_pendiente"] = qs.aggregate(total=Sum(F("valor_factura") - F("total_pagado")))["total"] or 0
        ctx["pendientes_count"] = qs.count()
        resumen_qs = (
            qs.values("proveedor__id", "proveedor__nombre")
            .annotate(facturas=Count("id"), total=Sum(F("valor_factura") - F("total_pagado")))
            .order_by("-total", "proveedor__nombre")
        )
        ctx["resumen_por_proveedor"] = resumen_qs
        ctx["proveedores_con_saldo"] = resumen_qs.count()
        ctx["total_resumen_proveedor"] = sum((r["total"] or 0) for r in resumen_qs)
        return ctx


@login_required
def facturas_pendientes_view(request):
    ctx = _factura_listing_context(
        request,
        scoped_facturas(request.user),
        title="Facturas pendientes",
        include_estado="pendiente",
        show_estado_filter=False,
        show_confirm_filter=False,
        template_tab="pendientes",
    )
    return render(request, "cartera/facturas_list.html", ctx)


@login_required
def pagos_list_view(request):
    qs = _base_factura_filters(
        request,
        scoped_facturas(request.user)
        .filter(estado="pagada")
        .prefetch_related(Prefetch("pagos", queryset=Pago.objects.order_by("-fecha_pago", "-id"), to_attr="pagos_ordenados")),
        include_estado="pagada",
    )
    page_obj = _paginate(request, qs, per_page=50)
    _attach_payment_dates(page_obj.object_list)
    proveedores = Proveedor.objects.order_by("nombre")
    pdvs = PuntoVenta.objects.order_by("nombre") if is_global_user(request.user) else []
    anios = list(scoped_facturas(request.user).dates("fecha_factura", "year", order="DESC"))
    return render(request, "cartera/facturas_list.html", {
        "title": "Facturas pagadas",
        "page_obj": page_obj,
        "facturas": page_obj.object_list,
        "proveedores": proveedores,
        "pdvs": pdvs,
        "anios": [d.year for d in anios],
        "show_estado_filter": False,
        "show_confirm_filter": True,
        "show_payment_date": True,
        "date_column_label": "Fecha de pago",
        "tab": "pagadas",
        "filters": {
            "q": request.GET.get("q", ""),
            "prov": request.GET.get("prov", ""),
            "pdv": request.GET.get("pdv", ""),
            "estado": request.GET.get("estado", ""),
            "confirmacion": request.GET.get("confirmacion", ""),
            "mes": request.GET.get("mes", ""),
            "anio": request.GET.get("anio", ""),
            "desde": request.GET.get("desde", ""),
            "hasta": request.GET.get("hasta", ""),
        },
    })


@login_required
def facturas_todas_view(request):
    ctx = _factura_listing_context(
        request,
        scoped_facturas(request.user),
        title="Todas las facturas",
        include_estado=None,
        show_estado_filter=True,
        show_confirm_filter=True,
        template_tab="todas",
    )
    return render(request, "cartera/facturas_list.html", ctx)


class FacturaDetalleView(LoginRequiredMixin, DetailView):
    model = Factura
    template_name = "cartera/factura_detalle.html"

    def get_queryset(self):
        return scoped_facturas(self.request.user).prefetch_related("pagos__logs_correo", "logs_correo")

    def post(self, request, *args, **kwargs):
        self.object = self.get_object()
        if request.POST.get("action") == "delete":
            if self.object.estado != "pendiente" or self.object.pagos.exists() or self.object.confirmado_pago:
                messages.error(request, "Solo se pueden eliminar facturas pendientes sin pago ni confirmación.")
                return redirect("factura_detalle", pk=self.object.pk)
            numero = self.object.numero_factura
            self.object.delete()
            messages.success(request, f"Factura {numero} eliminada correctamente.")
            return redirect("facturas_pendientes")
        return HttpResponseRedirect(self.request.path)

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        factura = self.object
        pagos = list(factura.pagos.select_related("lote").all())
        es_pago_contado = any(_es_contado_por_notas(p) for p in pagos)
        email_logs = factura.logs_correo.order_by("-creado_en")
        envios_exitosos = email_logs.filter(exito=True).count()
        ultimo_envio = email_logs.filter(exito=True).first()

        ctx.update({
            "pagos": pagos,
            "es_pago_contado": es_pago_contado,
            "envios_exitosos": envios_exitosos,
            "ultimo_envio": ultimo_envio,
            "ultimo_enviado_a": getattr(ultimo_envio, "enviado_a", "") if ultimo_envio else "",
            "novedades_proveedor": _build_novedades_factura(factura),
            "puede_eliminar": factura.estado == "pendiente" and not factura.pagos.exists() and not factura.confirmado_pago,
        })
        return ctx


class FacturaUpdateView(LoginRequiredMixin, UpdateView):
    model = Factura
    form_class = FacturaForm
    template_name = "cartera/factura_form.html"

    def get_queryset(self):
        return scoped_facturas(self.request.user)

    def dispatch(self, request, *args, **kwargs):
        factura = self.get_object()
        if factura.pagos.exists() or factura.confirmado_pago:
            messages.info(request, "Esta factura ya tiene pago registrado o fue confirmada. No se puede editar.")
            return redirect("factura_detalle", pk=factura.pk)
        return super().dispatch(request, *args, **kwargs)

    def get_form_kwargs(self):
        kwargs = super().get_form_kwargs()
        kwargs["user"] = self.request.user
        return kwargs

    def form_valid(self, form):
        factura = form.save(commit=False)
        factura = guardar_factura_desde_form(
            factura,
            created=False,
            usuario=self.request.user,
            request=self.request,
            auto_payment_note="Pago auto-generado al marcar la factura como PAGADA en edicion (contado en PDV).",
        )

        messages.success(self.request, "Factura actualizada correctamente.")
        if factura.estado == "pagada":
            return redirect("pagos_list")
        return redirect("facturas_pendientes")

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        ctx["alerta_valor_alto"] = ALERTA_FACTURA
        return ctx


class FacturaCreateView(LoginRequiredMixin, CreateView):
    model = Factura
    form_class = FacturaForm
    template_name = "cartera/factura_form.html"
    success_url = reverse_lazy("facturas_pendientes")

    def get_initial(self):
        initial = super().get_initial()
        initial.setdefault("fecha_factura", timezone.localdate())
        return initial

    def get_form_kwargs(self):
        kwargs = super().get_form_kwargs()
        kwargs["user"] = self.request.user
        return kwargs

    def form_valid(self, form):
        factura = form.save(commit=False)
        factura = guardar_factura_desde_form(
            factura,
            created=True,
            usuario=self.request.user,
            request=self.request,
            auto_payment_note="Pago auto-generado al crear la factura como PAGADA (contado en PDV).",
        )

        messages.success(self.request, "Factura creada correctamente.")
        if factura.estado == "pagada":
            return redirect("pagos_list")
        return redirect("facturas_pendientes")

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        ctx["alerta_valor_alto"] = ALERTA_FACTURA
        return ctx


class PagoCreateView(LoginRequiredMixin, CreateView):
    model = Pago
    form_class = PagoForm
    template_name = "cartera/pago_form.html"

    def dispatch(self, request, *args, **kwargs):
        self.factura = get_object_or_404(scoped_facturas(request.user), pk=kwargs["pk"])
        if self.factura.pagos.exists():
            messages.info(request, "Esta factura ya tiene un pago registrado.")
            return redirect("factura_detalle", pk=self.factura.pk)
        return super().dispatch(request, *args, **kwargs)

    def get_initial(self):
        initial = super().get_initial()
        initial.setdefault("fecha_pago", timezone.localdate())
        if self.factura and self.factura.valor_factura is not None:
            initial.setdefault("valor_pagado", self.factura.valor_factura)
        return initial

    def get_form_kwargs(self):
        kwargs = super().get_form_kwargs()
        kwargs["user"] = self.request.user
        kwargs["factura"] = self.factura
        return kwargs

    def form_valid(self, form):
        pago_form = form.save(commit=False)
        pago = crear_pago(
            factura=self.factura,
            fecha_pago=pago_form.fecha_pago or timezone.localdate(),
            valor_pagado=self.factura.valor_factura,
            pagado_por=pago_form.pagado_por,
            comprobante=pago_form.comprobante,
            notas=pago_form.notas,
            usuario=self.request.user,
            request=self.request,
        )

        ok, info, motivo = enviar_correo_pago_si_aplica(self.request, pago)
        if motivo == "contado":
            messages.success(self.request, "Pago registrado. No se envió correo porque corresponde a pago de contado.")
            return redirect("pagos_list")

        if motivo == "sin_comprobante":
            messages.warning(
                self.request,
                "Pago registrado, pero no se envió correo porque no se adjuntó comprobante."
            )
            return redirect("pagos_list")

        if motivo == "sin_email":
            messages.warning(
                self.request,
                "Pago registrado, pero no se envió correo porque el proveedor no tiene email."
            )
            return redirect("pagos_list")

        if ok:
            messages.success(self.request, "Pago registrado y correo enviado al proveedor.")
        else:
            messages.warning(self.request, f"Pago registrado, pero el correo no se envió: {info}")

        return redirect("pagos_list")

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        ctx["factura"] = self.factura
        return ctx


class PagoAdjuntarComprobanteView(LoginRequiredMixin, UpdateView):
    model = Pago
    form_class = PagoComprobanteForm
    template_name = "cartera/pago_adjuntar.html"

    def get_queryset(self):
        return scoped_pagos(self.request.user)

    def dispatch(self, request, *args, **kwargs):
        self.pago = self.get_object()
        if self.pago.comprobante:
            messages.info(request, "Este pago ya tiene comprobante adjunto.")
            return redirect("factura_detalle", pk=self.pago.factura.pk)
        return super().dispatch(request, *args, **kwargs)

    def form_valid(self, form):
        if not form.cleaned_data.get("comprobante"):
            messages.error(self.request, "Debes seleccionar un archivo.")
            return self.form_invalid(form)
        form.save()
        messages.success(self.request, "Comprobante adjuntado correctamente.")
        return redirect("factura_detalle", pk=self.pago.factura.pk)

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        ctx["pago"] = self.pago
        ctx["factura"] = self.pago.factura
        return ctx


class PagoEnviarEmailView(LoginRequiredMixin, View):
    def post(self, request, pk):
        pago = get_object_or_404(scoped_pagos(request.user), pk=pk)
        if _es_contado_por_notas(pago):
            messages.info(request, "Pago de contado: no se envía correo de confirmación.")
            return redirect("factura_detalle", pk=pago.factura.id)
        if not (pago.comprobante and pago.comprobante.name):
            messages.error(request, "Este pago no tiene comprobante adjunto.")
            return redirect("factura_detalle", pk=pago.factura.id)
        ok, info, _motivo = enviar_correo_pago_si_aplica(request, pago)
        if ok:
            messages.success(request, "Comprobante enviado al proveedor.")
        else:
            messages.error(request, f"No se envió: {info}")
        return redirect("factura_detalle", pk=pago.factura.id)


class ConfirmarPagoView(View):
    template_name = "cartera/confirmacion_publica.html"

    def _get_pago(self, token):
        ok, valor = validar_token(token)
        if not ok:
            return None, "El enlace no es válido o expiró.", 400
        try:
            pago = Pago.objects.select_related("factura__proveedor").get(id=valor)
        except Pago.DoesNotExist:
            return None, "No encontramos el pago asociado a este enlace.", 404
        return pago, "", 200

    def get(self, request, token):
        pago, motivo, status_code = self._get_pago(token)
        if not pago:
            return render(request, "cartera/confirmacion_error.html", {"motivo": motivo}, status=status_code)
        factura = pago.factura
        return render(request, self.template_name, {
            "proveedor": factura.proveedor,
            "numero_factura": factura.numero_factura,
            "valor_factura": factura.valor_factura,
            "fecha_confirmacion": factura.confirmado_fecha,
            "ya_confirmado": factura.confirmado_pago,
            "requiere_confirmacion": not factura.confirmado_pago,
        }, status=200)

    def post(self, request, token):
        pago, motivo, status_code = self._get_pago(token)
        if not pago:
            return render(request, "cartera/confirmacion_error.html", {"motivo": motivo}, status=status_code)
        factura = pago.factura
        factura = confirmar_factura(factura, pago=pago, request=request)
        return render(request, self.template_name, {
            "proveedor": factura.proveedor,
            "numero_factura": factura.numero_factura,
            "valor_factura": factura.valor_factura,
            "fecha_confirmacion": factura.confirmado_fecha,
            "ya_confirmado": True,
            "requiere_confirmacion": False,
        }, status=200)


class PagoLoteCreateView(LoginRequiredMixin, View):
    template_name = "cartera/pago_lote_form.html"

    def _parse_ids(self, request):
        raw = (request.POST.get("ids") or request.GET.get("ids") or "").strip()
        ids = []
        for chunk in raw.replace(",", " ").split():
            if chunk.isdigit():
                ids.append(int(chunk))
        return ids

    def _facturas_validas(self, request, ids):
        qs = scoped_facturas(request.user).filter(pk__in=ids, estado="pendiente").filter(pagos__isnull=True)
        return list(qs)

    def get(self, request):
        ids = self._parse_ids(request)
        if not ids:
            messages.info(request, "Selecciona al menos una factura.")
            return redirect("facturas_pendientes")
        facturas = self._facturas_validas(request, ids)
        if not facturas:
            messages.error(request, "Las facturas seleccionadas no son válidas para pago.")
            return redirect("facturas_pendientes")
        prov = facturas[0].proveedor
        if any(f.proveedor_id != prov.id for f in facturas):
            messages.error(request, PAGO_LOTE_MONOPROVEEDOR_ERROR)
            return redirect("facturas_pendientes")
        total = sum((f.valor_factura or 0) for f in facturas)
        form = PagoLoteForm(user=request.user, pdv_default=facturas[0].punto_venta, initial={"fecha_pago": timezone.localdate()})
        return render(request, self.template_name, {
            "form": form,
            "proveedor": prov,
            "facturas": facturas,
            "total": total,
            "ids": ",".join(str(f.id) for f in facturas),
        })

    def post(self, request):
        ids = self._parse_ids(request)
        facturas = self._facturas_validas(request, ids)
        if not facturas:
            messages.error(request, "Las facturas seleccionadas no son válidas para pago.")
            return redirect("facturas_pendientes")
        prov = facturas[0].proveedor
        if any(f.proveedor_id != prov.id for f in facturas):
            messages.error(request, PAGO_LOTE_MONOPROVEEDOR_ERROR)
            return redirect("facturas_pendientes")
        form = PagoLoteForm(request.POST, request.FILES, user=request.user, pdv_default=facturas[0].punto_venta)
        if not form.is_valid():
            total = sum((f.valor_factura or 0) for f in facturas)
            return render(request, self.template_name, {
                "form": form, "proveedor": prov, "facturas": facturas, "total": total, "ids": ",".join(str(f.id) for f in facturas)
            })
        with transaction.atomic():
            lote = form.save(commit=False)
            lote.proveedor = prov
            lote.save()
            comp_name = lote.comprobante.name if getattr(lote, "comprobante", None) else None
            for f in facturas:
                crear_pago(
                    factura=f,
                    fecha_pago=lote.fecha_pago,
                    valor_pagado=f.valor_factura or Decimal("0"),
                    pagado_por=lote.pagado_por,
                    lote=lote,
                    notas=f"Pago perteneciente al Lote #{lote.id}.",
                    comprobante=comp_name or None,
                    usuario=request.user,
                    request=request,
                )
        ok, info, _motivo = enviar_correo_lote_si_aplica(request, lote)
        if ok:
            messages.success(request, f"Lote #{lote.id} creado y correo enviado.")
        else:
            messages.warning(request, f"Lote #{lote.id} creado, pero correo NO enviado: {info}")
        return redirect("pagos_list")


class ConfirmarPagoLoteView(View):
    template_name = "cartera/confirmacion_publica_lote.html"

    def _get_lote(self, token):
        ok, lote_id = validar_token_lote(token)
        if not ok:
            return None, "El enlace no es válido o expiró.", 400
        try:
            lote = PagoLote.objects.select_related("proveedor").prefetch_related("pagos__factura__punto_venta").get(pk=lote_id)
        except PagoLote.DoesNotExist:
            return None, "No encontramos el lote asociado a este enlace.", 404
        return lote, "", 200

    def get(self, request, token):
        lote, motivo, status_code = self._get_lote(token)
        if not lote:
            return render(request, "cartera/confirmacion_error.html", {"motivo": motivo}, status=status_code)
        pagos = list(lote.pagos.all())
        total_lote = sum((p.valor_pagado or Decimal("0")) for p in pagos)
        ya_confirmado = bool(pagos) and all(p.factura.confirmado_pago for p in pagos)
        fecha_confirmacion = next((p.factura.confirmado_fecha for p in pagos if p.factura.confirmado_fecha), None)
        return render(request, self.template_name, {
            "proveedor": lote.proveedor,
            "lote": lote,
            "facturas": [p.factura for p in pagos],
            "fecha_confirmacion": fecha_confirmacion,
            "total": total_lote,
            "ya_confirmado": ya_confirmado,
            "requiere_confirmacion": not ya_confirmado,
        }, status=200)

    def post(self, request, token):
        lote, motivo, status_code = self._get_lote(token)
        if not lote:
            return render(request, "cartera/confirmacion_error.html", {"motivo": motivo}, status=status_code)
        ahora, pagos = confirmar_lote(lote, request=request)
        total_lote = sum((p.valor_pagado or Decimal("0")) for p in pagos)
        return render(request, self.template_name, {
            "proveedor": lote.proveedor,
            "lote": lote,
            "facturas": [p.factura for p in pagos],
            "fecha_confirmacion": ahora,
            "total": total_lote,
            "ya_confirmado": True,
            "requiere_confirmacion": False,
        }, status=200)


class ProveedorViewSet(viewsets.ModelViewSet):
    queryset = Proveedor.objects.all().order_by("nombre", "id")
    serializer_class = ProveedorSerializer
    permission_classes = [permissions.IsAuthenticated]
    filter_backends = [DjangoFilterBackend, filters.SearchFilter, filters.OrderingFilter]
    search_fields = ["nombre", "nit", "email"]
    ordering_fields = ["nombre", "nit", "creado_en"]

    def _ensure_write_allowed(self):
        if not is_global_user(self.request.user):
            raise DRFPermissionDenied("Solo un usuario administrador puede modificar proveedores.")

    def perform_create(self, serializer):
        self._ensure_write_allowed()
        serializer.save()

    def perform_update(self, serializer):
        self._ensure_write_allowed()
        serializer.save()

    def perform_destroy(self, instance):
        self._ensure_write_allowed()
        instance.delete()


class FacturaViewSet(viewsets.ModelViewSet):
    serializer_class = FacturaSerializer
    permission_classes = [permissions.IsAuthenticated]
    filter_backends = [DjangoFilterBackend, filters.SearchFilter, filters.OrderingFilter]
    filterset_fields = ["estado", "proveedor", "punto_venta", "fecha_factura"]
    search_fields = ["numero_factura", "proveedor__nombre", "punto_venta__nombre"]
    ordering_fields = ["fecha_factura", "valor_factura", "creado_en"]

    def get_queryset(self):
        return scoped_facturas(self.request.user)

    def perform_destroy(self, instance):
        if instance.estado != "pendiente" or instance.pagos.exists() or instance.confirmado_pago:
            raise DRFValidationError("Solo se pueden eliminar facturas pendientes sin pago ni confirmación.")
        instance.delete()


class PagoViewSet(viewsets.ModelViewSet):
    serializer_class = PagoSerializer
    permission_classes = [permissions.IsAuthenticated]
    filter_backends = [DjangoFilterBackend, filters.SearchFilter, filters.OrderingFilter]
    filterset_fields = ["fecha_pago", "pagado_por", "factura", "factura__proveedor"]
    search_fields = ["pagado_por", "factura__numero_factura", "factura__proveedor__nombre"]
    ordering_fields = ["fecha_pago", "valor_pagado", "creado_en"]

    def get_queryset(self):
        return scoped_pagos(self.request.user)

    @transaction.atomic
    def perform_destroy(self, instance):
        try:
            eliminar_pago_seguro(instance, usuario=self.request.user, request=self.request)
        except ValidationError as exc:
            raise DRFValidationError(exc.messages)


@login_required
def analytics_dashboard(request):
    today = date.today()
    pv_scope = None if is_global_user(request.user) else ensure_user_scope(request.user)
    rango = (request.GET.get("rango") or "").strip() or "mes_actual"
    if rango == "mes_actual":
        d1_def, d2_def = _month_bounds(today)
    elif rango == "mes_anterior":
        y = today.year if today.month > 1 else today.year - 1
        m = today.month - 1 if today.month > 1 else 12
        d1_def = date(y, m, 1)
        d2_def = date(y, m, monthrange(y, m)[1])
    elif rango == "ult_30":
        d2_def = today
        d1_def = today - timedelta(days=29)
    elif rango == "este_anio":
        d1_def, d2_def = date(today.year, 1, 1), today
    elif rango == "anio_pasado":
        d1_def, d2_def = date(today.year - 1, 1, 1), date(today.year - 1, 12, 31)
    else:
        d1_def, d2_def = _month_bounds(today)
    if (request.GET.get("rango") or "") == "personalizado":
        d1 = _d(request.GET.get("d1"), d1_def)
        d2 = _d(request.GET.get("d2"), d2_def)
    else:
        d1, d2 = d1_def, d2_def

    qs_base = Factura.objects.select_related("proveedor", "punto_venta").all()
    pdv = request.GET.get("pdv")
    prov = request.GET.get("prov")
    if pv_scope:
        qs_base = qs_base.filter(punto_venta=pv_scope)
        pdv = str(pv_scope.id)
    elif pdv and str(pdv).isdigit():
        qs_base = qs_base.filter(punto_venta_id=int(pdv))
    if prov and str(prov).isdigit():
        qs_base = qs_base.filter(proveedor_id=int(prov))
    qs_periodo = qs_base.filter(fecha_factura__range=[d1, d2])

    agg = qs_periodo.aggregate(total=Sum("valor_factura"), cnt=Count("id"))
    total_compras = agg["total"] or Decimal("0")
    total_facturas = agg["cnt"] or 0
    pagos_qs = Pago.objects.filter(factura__in=qs_periodo)
    pagado = pagos_qs.aggregate(t=Sum("valor_pagado"))["t"] or Decimal("0")
    pagos_para_dias = pagos_qs.select_related("factura").only("fecha_pago", "factura__fecha_factura")
    dias_lista = []

    for pago in pagos_para_dias:
        if pago.fecha_pago and pago.factura and pago.factura.fecha_factura:
            try:
                dias_lista.append((pago.fecha_pago - pago.factura.fecha_factura).days)
            except Exception:
                pass

    dias_prom = round(sum(dias_lista) / len(dias_lista), 1) if dias_lista else 0.0
    pend_qs = Factura.objects.filter(estado="pendiente")
    if pv_scope:
        pend_qs = pend_qs.filter(punto_venta=pv_scope)
    elif pdv and str(pdv).isdigit():
        pend_qs = pend_qs.filter(punto_venta_id=int(pdv))
    if prov and str(prov).isdigit():
        pend_qs = pend_qs.filter(proveedor_id=int(prov))
    valor_pendiente = pend_qs.aggregate(t=Sum(F("valor_factura") - F("total_pagado")))["t"] or Decimal("0")
    num_pendientes = pend_qs.count()
    hoy_dt = timezone.localdate()
    if num_pendientes:
        edades = [(hoy_dt - f.fecha_factura).days for f in pend_qs.only("fecha_factura")]
        antig_prom_pend = round(sum(edades) / len(edades), 1)
        ticket_prom_pend = (valor_pendiente / num_pendientes) if num_pendientes else 0
    else:
        antig_prom_pend = 0.0
        ticket_prom_pend = 0.0
    pct_pagado = _safe_div(pagado, total_compras)
    total_pagos = pagos_qs.count() or 0
    pagos_contado = pagos_qs.filter(notas__icontains="auto-generado").count()
    pct_pagos_contado = _safe_div(pagos_contado, total_pagos)
    fact_con_pago = qs_periodo.filter(pagos__isnull=False).distinct().count() or 0
    fact_confirmadas = qs_periodo.filter(confirmado_pago=True).distinct().count() or 0
    tasa_confirmacion = _safe_div(fact_confirmadas, fact_con_pago)
    pagos_con_comp = pagos_qs.exclude(Q(comprobante__isnull=True) | Q(comprobante__exact="")).count()
    cobertura_comprobantes = _safe_div(pagos_con_comp, total_pagos)
    top_prov_agg = list(qs_periodo.values("proveedor__id", "proveedor__nombre").annotate(total=Sum("valor_factura")).order_by("-total")[:3])
    top_total = sum((r["total"] or 0) for r in top_prov_agg) or Decimal("0")
    share_top1 = _safe_div(top_prov_agg[0]["total"] if top_prov_agg else 0, total_compras)
    share_top3 = _safe_div(top_total, total_compras)
    if rango == "mes_actual":
        y = today.year if today.month > 1 else today.year - 1
        m = today.month - 1 if today.month > 1 else 12
        prev_d1 = date(y, m, 1)
        prev_d2 = date(y, m, monthrange(y, m)[1])
        prev_qs = Factura.objects.select_related("proveedor", "punto_venta")
        if pv_scope:
            prev_qs = prev_qs.filter(punto_venta=pv_scope)
        elif pdv and str(pdv).isdigit():
            prev_qs = prev_qs.filter(punto_venta_id=int(pdv))
        if prov and str(prov).isdigit():
            prev_qs = prev_qs.filter(proveedor_id=int(prov))
        prev_qs = prev_qs.filter(fecha_factura__range=[prev_d1, prev_d2])
        prev_total = prev_qs.aggregate(t=Sum("valor_factura"))["t"] or Decimal("0")
        delta_mes_ant = _safe_div(total_compras - prev_total, prev_total)
        delta_monto = total_compras - prev_total
    else:
        delta_mes_ant = None
        delta_monto = Decimal("0")
    start_7 = timezone.now() - timedelta(days=7)
    nuevas_7d = qs_base.filter(creado_en__gte=start_7).count()
    top_prov = [{"id": r["proveedor__id"], "nombre": r["proveedor__nombre"], "total": r["total"]} for r in qs_periodo.values("proveedor__id", "proveedor__nombre").annotate(total=Sum("valor_factura")).order_by("-total")[:10]]
    por_pdv = [{"id": r["punto_venta__id"], "nombre": r["punto_venta__nombre"], "total": r["total"]} for r in qs_periodo.values("punto_venta__id", "punto_venta__nombre").annotate(total=Sum("valor_factura")).order_by("-total")]
    by_month_qs = qs_periodo.annotate(m=TruncMonth("fecha_factura")).values("m").annotate(total=Sum("valor_factura")).order_by("m")
    by_month = []
    for r in by_month_qs:
        m = r["m"]
        if isinstance(m, datetime):
            m = m.date()
        by_month.append({"m": m.isoformat(), "total": r["total"]})
    top_facturas = list(qs_periodo.order_by("-valor_factura").values("id", "numero_factura", "fecha_factura", "proveedor__nombre", "punto_venta__nombre", "valor_factura")[:12])
    pdvs = PuntoVenta.objects.order_by("nombre").values("id", "nombre") if is_global_user(request.user) else [{"id": pv_scope.id, "nombre": pv_scope.nombre}] if pv_scope else []
    provs = Proveedor.objects.order_by("nombre").values("id", "nombre")
    return render(request, "cartera/analytics_dashboard.html", {
        "filters": {
            "pdv": int(pdv) if pdv and str(pdv).isdigit() else "",
            "prov": int(prov) if prov and str(prov).isdigit() else "",
            "d1": d1.isoformat(),
            "d2": d2.isoformat(),
            "rango": rango,
            "pdv_forzado": bool(pv_scope),
        },
        "pdvs": list(pdvs),
        "provs": list(provs),

        "kpi_total": round(total_compras, 0),
        "kpi_facturas": total_facturas,
        "kpi_pagado": round(pagado, 0),
        "kpi_dias": round(dias_prom, 1),
        "kpi_valor_pendiente": round(valor_pendiente, 0),
        "kpi_num_pendientes": num_pendientes,
        "kpi_pct_pagado": round(pct_pagado, 1),
        "kpi_pct_contado": round(pct_pagos_contado, 1),
        "kpi_tasa_conf": round(tasa_confirmacion, 1),
        "kpi_cob_comp": round(cobertura_comprobantes, 1),
        "kpi_share_top1": round(share_top1, 1),
        "kpi_share_top3": round(share_top3, 1),
        "kpi_delta_mes_ant": round(delta_mes_ant, 1) if delta_mes_ant is not None else None,
        "kpi_delta_monto": round(delta_monto, 0),
        "kpi_nuevas_7d": nuevas_7d,
        "kpi_antig_pend": antig_prom_pend,
        "kpi_ticket_pend": round(ticket_prom_pend, 0),

        "top_prov": top_prov,
        "por_pdv": por_pdv,
        "by_month": by_month,
        "top_facturas": top_facturas,

        "top_prov_json": json.dumps(top_prov, default=str),
        "por_pdv_json": json.dumps(por_pdv, default=str),
        "by_month_json": json.dumps(by_month, default=str),
    })
