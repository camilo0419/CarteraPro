from decimal import Decimal
from urllib.parse import urlparse

from django.contrib import messages
from django.contrib.auth.mixins import LoginRequiredMixin
from django.core.paginator import EmptyPage, PageNotAnInteger, Paginator
from django.db.models import Count, F, Q, Sum
from django.shortcuts import redirect, render
from django.urls import reverse
from django.utils.dateparse import parse_date
from django.views.generic import DetailView, ListView, TemplateView, View

from .forms import NovedadProveedorForm
from .models import EventoAuditoria, Pago, PagoLote
from .services.audit import registrar_evento
from .services.payments import confirmar_factura, confirmar_lote
from .services.provider_notifications import marcar_notificacion_leida, notificar_confirmacion, notificar_novedad
from .services.provider_scope import (
    facturas_visibles,
    get_factura_for_user,
    get_lote_for_user,
    get_notificacion_for_user,
    get_pago_for_user,
    lotes_visibles,
    notificaciones_visibles,
    pagos_visibles,
    proveedor_links,
    proveedores_activos,
    require_can_confirm,
    require_portal_access,
    validate_comprobante_access,
)


def _paginate(request, qs, per_page=25):
    paginator = Paginator(qs, per_page)
    page = request.GET.get("page")
    try:
        return paginator.page(page)
    except PageNotAnInteger:
        return paginator.page(1)
    except EmptyPage:
        return paginator.page(paginator.num_pages)


def _parse_date(raw, fallback=None):
    return parse_date(raw) if raw else fallback


def _safe_portal_url(target):
    if not target:
        return ""
    parsed = urlparse(target)
    if parsed.scheme or parsed.netloc:
        return ""
    portal_prefix = reverse("portal_proveedor_dashboard")
    if parsed.path.startswith(portal_prefix):
        return target
    return ""


class PortalProveedorMixin(LoginRequiredMixin):
    proveedores = None

    def dispatch(self, request, *args, **kwargs):
        if not request.user.is_authenticated:
            return self.handle_no_permission()
        require_portal_access(request.user)
        self.proveedores = list(proveedores_activos(request.user))
        return super().dispatch(request, *args, **kwargs)

    def get_context_data(self, **kwargs):
        try:
            ctx = super().get_context_data(**kwargs)
        except AttributeError:
            ctx = {}
        notifs = notificaciones_visibles(self.request.user)
        ctx.update({
            "portal_proveedores": self.proveedores,
            "portal_unread_count": notifs.filter(leida=False).count(),
            "portal_latest_notifications": notifs[:5],
        })
        return ctx


class PortalProveedorDashboardView(PortalProveedorMixin, TemplateView):
    template_name = "cartera/portal_proveedor/dashboard.html"

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        facturas = facturas_visibles(self.request.user)
        pagos = pagos_visibles(self.request.user)
        lotes = lotes_visibles(self.request.user)
        pendientes = facturas.filter(estado="pendiente")
        ctx.update({
            "total_pendiente": pendientes.aggregate(total=Sum(F("valor_factura") - F("total_pagado")))["total"] or Decimal("0"),
            "total_pagado": pagos.aggregate(total=Sum("valor_pagado"))["total"] or Decimal("0"),
            "facturas_pendientes": pendientes.count(),
            "pagos_por_confirmar": pagos.filter(factura__confirmado_pago=False).count(),
            "lotes_por_confirmar": lotes.filter(pagos__factura__confirmado_pago=False).distinct().count(),
            "pagos_recientes": pagos.order_by("-fecha_pago", "-id")[:6],
            "resumen_pdv": facturas.values("punto_venta__nombre").annotate(
                facturas=Count("id"),
                total=Sum(F("valor_factura") - F("total_pagado")),
            ).order_by("punto_venta__nombre"),
            "notificaciones": notificaciones_visibles(self.request.user)[:6],
        })
        return ctx


class PortalFacturaListView(PortalProveedorMixin, ListView):
    template_name = "cartera/portal_proveedor/facturas.html"
    context_object_name = "facturas"
    paginate_by = 25

    def get_queryset(self):
        qs = facturas_visibles(self.request.user)
        q = (self.request.GET.get("q") or "").strip()
        pdv = (self.request.GET.get("pdv") or "").strip()
        estado = (self.request.GET.get("estado") or "").strip()
        confirmacion = (self.request.GET.get("confirmacion") or "").strip()
        desde = _parse_date(self.request.GET.get("desde"))
        hasta = _parse_date(self.request.GET.get("hasta"))

        if q:
            qs = qs.filter(Q(numero_factura__icontains=q) | Q(punto_venta__nombre__icontains=q))
        if pdv:
            qs = qs.filter(punto_venta__nombre__icontains=pdv)
        if estado in {"pendiente", "pagada"}:
            qs = qs.filter(estado=estado)
        if confirmacion == "confirmadas":
            qs = qs.filter(confirmado_pago=True)
        elif confirmacion == "sin_confirmar":
            qs = qs.filter(confirmado_pago=False)
        if desde:
            qs = qs.filter(fecha_factura__gte=desde)
        if hasta:
            qs = qs.filter(fecha_factura__lte=hasta)
        return qs.order_by("-fecha_factura", "-id")

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        ctx["filters"] = self.request.GET
        return ctx


class PortalFacturaDetailView(PortalProveedorMixin, DetailView):
    template_name = "cartera/portal_proveedor/factura_detail.html"
    context_object_name = "factura"

    def get_object(self, queryset=None):
        return get_factura_for_user(self.request.user, self.kwargs["pk"])

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        factura = self.object
        pagos = pagos_visibles(self.request.user).filter(factura=factura).order_by("-fecha_pago", "-id")
        ctx.update({
            "pagos": pagos,
            "eventos": EventoAuditoria.objects.filter(
                Q(factura=factura) | Q(pago__factura=factura),
            ).order_by("-creado_en", "-id")[:20],
            "novedades": EventoAuditoria.objects.filter(
                tipo=EventoAuditoria.TIPO_NOVEDAD_PROVEEDOR,
            ).filter(Q(factura=factura) | Q(pago__factura=factura)).order_by("-creado_en", "-id"),
        })
        return ctx


class PortalPagoListView(PortalProveedorMixin, ListView):
    template_name = "cartera/portal_proveedor/pagos.html"
    context_object_name = "pagos"
    paginate_by = 25

    def get_queryset(self):
        qs = pagos_visibles(self.request.user)
        estado = (self.request.GET.get("confirmacion") or "").strip()
        if estado == "confirmados":
            qs = qs.filter(factura__confirmado_pago=True)
        elif estado == "sin_confirmar":
            qs = qs.filter(factura__confirmado_pago=False)
        return qs.order_by("-fecha_pago", "-id")


class PortalPagoConfirmView(PortalProveedorMixin, View):
    def post(self, request, pk):
        pago = get_pago_for_user(request.user, pk)
        proveedor = pago.factura.proveedor
        require_can_confirm(request.user, proveedor)
        if pago.factura.confirmado_pago:
            messages.info(request, "Este pago ya estaba confirmado.")
            return redirect("portal_proveedor_factura_detail", pk=pago.factura_id)

        confirmar_factura(
            pago.factura,
            pago=pago,
            request=request,
            usuario=request.user,
            email=proveedor.email,
            event_type=EventoAuditoria.TIPO_CONFIRMACION_PAGO_PORTAL,
            metadata={"origen": "portal_proveedor", "proveedor_id": proveedor.pk},
        )
        notificar_confirmacion(proveedor=proveedor, usuario_actor=request.user, factura=pago.factura, pago=pago, request=request)
        messages.success(request, "Recepción del pago confirmada correctamente.")
        return redirect("portal_proveedor_factura_detail", pk=pago.factura_id)

    def get(self, request, pk):
        pago = get_pago_for_user(request.user, pk)
        messages.info(request, "Usa el boton de confirmacion para registrar la recepcion.")
        return redirect("portal_proveedor_factura_detail", pk=pago.factura_id)


class PortalLoteDetailView(PortalProveedorMixin, DetailView):
    template_name = "cartera/portal_proveedor/lote_detail.html"
    context_object_name = "lote"

    def get_object(self, queryset=None):
        return get_lote_for_user(self.request.user, self.kwargs["pk"])

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        lote = self.object
        pagos = lote.pagos.select_related("factura", "factura__punto_venta").order_by("factura__numero_factura", "id")
        ctx.update({
            "pagos": pagos,
            "facturas": [p.factura for p in pagos],
            "total": pagos.aggregate(total=Sum("valor_pagado"))["total"] or Decimal("0"),
            "confirmado": pagos.exists() and not pagos.filter(factura__confirmado_pago=False).exists(),
            "novedades": EventoAuditoria.objects.filter(tipo=EventoAuditoria.TIPO_NOVEDAD_PROVEEDOR, lote=lote).order_by("-creado_en", "-id"),
        })
        return ctx


class PortalLoteConfirmView(PortalProveedorMixin, View):
    def post(self, request, pk):
        lote = get_lote_for_user(request.user, pk)
        proveedor = lote.proveedor
        require_can_confirm(request.user, proveedor)
        pagos = lote.pagos.select_related("factura")
        if pagos.exists() and not pagos.filter(factura__confirmado_pago=False).exists():
            messages.info(request, "Este lote ya estaba confirmado.")
            return redirect("portal_proveedor_lote_detail", pk=lote.pk)

        confirmar_lote(
            lote,
            request=request,
            usuario=request.user,
            proveedor=proveedor,
            event_type=EventoAuditoria.TIPO_CONFIRMACION_LOTE_PORTAL,
            metadata={"origen": "portal_proveedor", "proveedor_id": proveedor.pk},
        )
        notificar_confirmacion(proveedor=proveedor, usuario_actor=request.user, lote=lote, request=request)
        messages.success(request, "Recepción del lote confirmada correctamente.")
        return redirect("portal_proveedor_lote_detail", pk=lote.pk)

    def get(self, request, pk):
        get_lote_for_user(request.user, pk)
        messages.info(request, "Usa el boton de confirmacion para registrar la recepcion del lote.")
        return redirect("portal_proveedor_lote_detail", pk=pk)


class PortalNovedadListView(PortalProveedorMixin, TemplateView):
    template_name = "cartera/portal_proveedor/novedades.html"

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        ids = [p.id for p in self.proveedores]
        qs = EventoAuditoria.objects.filter(tipo=EventoAuditoria.TIPO_NOVEDAD_PROVEEDOR).filter(
            Q(factura__proveedor_id__in=ids)
            | Q(pago__factura__proveedor_id__in=ids)
            | Q(lote__proveedor_id__in=ids)
        )
        ctx["page_obj"] = _paginate(self.request, qs.order_by("-creado_en", "-id"))
        ctx["novedades"] = ctx["page_obj"].object_list
        return ctx


class PortalNovedadBaseView(PortalProveedorMixin, View):
    template_name = "cartera/portal_proveedor/novedad_form.html"
    target_type = ""

    def get_target(self):
        raise NotImplementedError

    def get(self, request, pk):
        target = self.get_target()
        return render(request, self.template_name, self.get_context(target, NovedadProveedorForm()))

    def post(self, request, pk):
        target = self.get_target()
        form = NovedadProveedorForm(request.POST)
        if not form.is_valid():
            return render(request, self.template_name, self.get_context(target, form))
        proveedor = self.get_proveedor(target)
        factura = getattr(target, "factura", None) if isinstance(target, Pago) else None
        pago = target if isinstance(target, Pago) else None
        lote = target if isinstance(target, PagoLote) else None
        registrar_evento(
            EventoAuditoria.TIPO_NOVEDAD_PROVEEDOR,
            factura=factura,
            pago=pago,
            lote=lote,
            usuario=request.user,
            request=request,
            metadata={
                "origen": "portal_proveedor",
                "proveedor_id": proveedor.pk,
                "motivo": form.cleaned_data["motivo"],
                "detalle": form.cleaned_data["detalle"],
                "target_type": self.target_type,
            },
        )
        notificar_novedad(
            proveedor=proveedor,
            usuario_actor=request.user,
            factura=factura,
            pago=pago,
            lote=lote,
            motivo=form.cleaned_data["motivo"],
            request=request,
        )
        messages.success(request, "Novedad reportada correctamente.")
        if lote:
            return redirect("portal_proveedor_lote_detail", pk=lote.pk)
        return redirect("portal_proveedor_factura_detail", pk=factura.pk)

    def get_proveedor(self, target):
        return target.proveedor if isinstance(target, PagoLote) else target.factura.proveedor

    def get_context(self, target, form):
        ctx = self.get_context_data()
        ctx.update({"form": form, "target": target, "target_type": self.target_type})
        return ctx


class PortalPagoNovedadView(PortalNovedadBaseView):
    target_type = "pago"

    def get_target(self):
        return get_pago_for_user(self.request.user, self.kwargs["pk"])


class PortalLoteNovedadView(PortalNovedadBaseView):
    target_type = "lote"

    def get_target(self):
        return get_lote_for_user(self.request.user, self.kwargs["pk"])


class PortalNotificacionListView(PortalProveedorMixin, ListView):
    template_name = "cartera/portal_proveedor/notificaciones.html"
    context_object_name = "notificaciones"
    paginate_by = 25

    def get_queryset(self):
        return notificaciones_visibles(self.request.user).order_by("-creada_en", "-id")


class PortalNotificacionLeerView(PortalProveedorMixin, View):
    def post(self, request, pk):
        notif = get_notificacion_for_user(request.user, pk)
        marcar_notificacion_leida(notif, request=request)
        target = _safe_portal_url(notif.url_destino)
        if target:
            return redirect(target)
        return redirect("portal_proveedor_notificaciones")

    def get(self, request, pk):
        notif = get_notificacion_for_user(request.user, pk)
        target = _safe_portal_url(notif.url_destino)
        if target:
            return redirect(target)
        return redirect("portal_proveedor_notificaciones")


class PortalComprobanteView(PortalProveedorMixin, View):
    def get(self, request, pago_id):
        pago = get_pago_for_user(request.user, pago_id)
        validate_comprobante_access(request.user, pago)
        registrar_evento(
            EventoAuditoria.TIPO_COMPROBANTE_VISUALIZADO,
            factura=pago.factura,
            pago=pago,
            lote=pago.lote,
            usuario=request.user,
            request=request,
            metadata={
                "origen": "portal_proveedor",
                "proveedor_id": pago.factura.proveedor_id,
                "filename": pago.comprobante.name,
            },
        )
        return redirect(pago.comprobante.url)
