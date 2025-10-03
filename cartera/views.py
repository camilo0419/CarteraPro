from decimal import Decimal
import json
from datetime import date, datetime

from django.conf import settings
from django.contrib import messages
from django.contrib.auth.decorators import login_required, user_passes_test
from django.contrib.auth.mixins import LoginRequiredMixin
from django.db import models, transaction
from django.db.models import Sum, F, Count, Q, Avg, FloatField
from django.db.models.functions import TruncMonth, Extract
from django.shortcuts import render, get_object_or_404, redirect
from django.urls import reverse_lazy
from django.utils import timezone
from django.utils.dateparse import parse_date
from django.views.generic import (
    TemplateView, CreateView, ListView, DetailView, UpdateView, View
)

from rest_framework import viewsets, permissions, filters
from django_filters.rest_framework import DjangoFilterBackend

from .forms import FacturaForm, PagoForm, PagoComprobanteForm, PagoLoteForm
from .models import PuntoVenta, Proveedor, Factura, Pago, PuntoVentaUsuario, PagoLote
from .serializers import ProveedorSerializer, FacturaSerializer, PagoSerializer
from .utils import (
    enviar_recibo_pago, enviar_recibo_lote,
    validar_token, validar_token_lote
)

# ---------------------- helpers ----------------------
def get_user_pdv(user):
    """
    Devuelve el Punto de Venta asignado al usuario (o None si es staff/superuser).
    """
    if not user or not user.is_authenticated or user.is_staff or user.is_superuser:
        return None
    try:
        return user.pv_map.punto_venta
    except PuntoVentaUsuario.DoesNotExist:
        return None


# ---------------------- dashboard ----------------------
class DashboardView(LoginRequiredMixin, TemplateView):
    template_name = 'cartera/dashboard.html'

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)

        # Solo facturas pendientes (limitadas por PDV si aplica)
        qs = Factura.objects.filter(estado='pendiente')
        pv = get_user_pdv(self.request.user)
        if pv:
            qs = qs.filter(punto_venta=pv)

        # Totales
        ctx['total_pendiente'] = qs.aggregate(
            total=Sum(F('valor_factura') - F('total_pagado'))
        )['total'] or 0
        ctx['pendientes_count'] = qs.count()

        # Resumen por proveedor (facturas + saldo)
        resumen_qs = (
            qs.values('proveedor__id', 'proveedor__nombre')
              .annotate(
                  facturas=Count('id'),
                  total=Sum(F('valor_factura') - F('total_pagado')),
              )
              .order_by('-total', 'proveedor__nombre')
        )
        ctx['resumen_por_proveedor'] = resumen_qs
        ctx['proveedores_con_saldo'] = resumen_qs.count()
        return ctx


# ---------------------- facturas ----------------------
class FacturaPendientesView(LoginRequiredMixin, ListView):
    model = Factura
    template_name = 'cartera/facturas_pendientes.html'
    context_object_name = 'facturas'
    paginate_by = 25

    # guardamos q y prov para reusarlos en get_context_data
    def _get_params(self):
        q = (self.request.GET.get('q') or '').strip()
        prov = (self.request.GET.get('prov') or '').strip()
        return q, prov

    def get_queryset(self):
        q, prov = self._get_params()
        qs = (Factura.objects
              .filter(estado='pendiente')
              .select_related('proveedor', 'punto_venta')
              .order_by('-fecha_factura', '-id'))

        # por punto de venta del usuario (si aplica)
        pv = get_user_pdv(self.request.user)
        if pv:
            qs = qs.filter(punto_venta=pv)

        # filtro de búsqueda
        if q:
            qs = qs.filter(
                models.Q(proveedor__nombre__icontains=q) |
                models.Q(punto_venta__nombre__icontains=q) |
                models.Q(numero_factura__icontains=q)
            )

        # filtro por proveedor
        if prov.isdigit():
            qs = qs.filter(proveedor_id=int(prov))

        return qs

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        q, prov = self._get_params()
        ctx['q'] = q
        ctx['prov_id'] = prov if prov.isdigit() else ''
        ctx['prov_nombre'] = ''
        if ctx['prov_id']:
            ctx['prov_nombre'] = (Proveedor.objects
                                  .filter(pk=ctx['prov_id'])
                                  .values_list('nombre', flat=True)
                                  .first() or '')

        # (opcional) si quieres que el resumen muestre el resultado filtrado actual:
        qs = self.get_queryset()
        ctx['resumen_por_proveedor'] = (
            qs.values('proveedor__id','proveedor__nombre')
              .annotate(facturas=Count('id'),
                        total=Sum(F('valor_factura') - F('total_pagado')))
              .order_by('proveedor__nombre')
        )
        ctx['total_general_pendiente'] = (
            qs.aggregate(t=Sum(F('valor_factura') - F('total_pagado')))['t'] or 0
        )
        return ctx


def _es_contado_por_notas(pago):
    """True si el pago parece auto-generado (contado en PDV)."""
    n = (pago.notas or "").lower()
    return "auto-generado" in n

# ---------------------- facturas ----------------------
class FacturaDetalleView(LoginRequiredMixin, DetailView):
    model = Factura
    template_name = 'cartera/factura_detalle.html'

    def get_queryset(self):
        # Prefetch de pagos para evitar N+1
        return (Factura.objects
                .select_related('proveedor', 'punto_venta')
                .prefetch_related('pagos'))

    # ⬇️ NUEVO: marcamos si alguno de los pagos fue auto (contado)
    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        f = self.object
        es_pago_contado = any(_es_contado_por_notas(p) for p in f.pagos.all())
        ctx['es_pago_contado'] = es_pago_contado
        return ctx



class FacturaUpdateView(LoginRequiredMixin, UpdateView):
    model = Factura
    form_class = FacturaForm
    template_name = 'cartera/factura_form.html'
    success_url = reverse_lazy('facturas_pendientes')

    def dispatch(self, request, *args, **kwargs):
        factura = self.get_object()
        if factura.pagos.exists() or factura.confirmado_pago:
            messages.info(request, "Esta factura ya tiene pago registrado o fue confirmada. No se puede editar.")
            return redirect('factura_detalle', pk=factura.pk)
        return super().dispatch(request, *args, **kwargs)

    def get_form_kwargs(self):
        kwargs = super().get_form_kwargs()
        kwargs['user'] = self.request.user
        return kwargs

    def form_valid(self, form):
        factura = form.save(commit=False)

        if (factura.estado or '').lower() == 'pagada':
            factura.total_pagado = factura.valor_factura or Decimal('0')
        else:
            factura.estado = 'pendiente'
            factura.total_pagado = factura.total_pagado or Decimal('0')

        factura.save()

        # Si quedó pagada y no hay pagos, crea el Pago (contado en PDV)
        if factura.estado == 'pagada' and not factura.pagos.exists():
            pagado_por = f"PDV - {factura.punto_venta.nombre}" if factura.punto_venta else "OFICINA"
            Pago.objects.create(
                factura=factura,
                fecha_pago=timezone.localdate(),
                valor_pagado=factura.valor_factura or Decimal('0'),
                pagado_por=pagado_por,
                notas="Pago auto-generado al marcar la factura como PAGADA en edición (contado en PDV).",
            )

        return redirect('facturas_pendientes')


# ---------------------- facturas ----------------------
class FacturaCreateView(LoginRequiredMixin, CreateView):
    model = Factura
    form_class = FacturaForm
    template_name = 'cartera/factura_form.html'
    success_url = reverse_lazy('facturas_pendientes')

    def get_initial(self):
        initial = super().get_initial()
        initial.setdefault('fecha_factura', timezone.localdate())
        return initial

    def get_form_kwargs(self):
        kwargs = super().get_form_kwargs()
        kwargs['user'] = self.request.user
        return kwargs

    def form_valid(self, form):
        factura = form.save(commit=False)

        if not factura.creado_por_id:
            factura.creado_por = self.request.user

        if (factura.estado or '').lower() == 'pagada':
            factura.total_pagado = factura.valor_factura or Decimal('0')
        else:
            factura.estado = 'pendiente'
            factura.total_pagado = factura.total_pagado or Decimal('0')

        factura.save()

        if factura.estado == 'pagada' and not factura.pagos.exists():
            pagado_por = f"PDV - {factura.punto_venta.nombre}" if factura.punto_venta else "OFICINA"
            Pago.objects.create(
                factura=factura,
                fecha_pago=timezone.localdate(),
                valor_pagado=factura.valor_factura or Decimal('0'),
                pagado_por=pagado_por,
                notas="Pago auto-generado al crear la factura como PAGADA (contado en PDV).",
            )

        return redirect('facturas_pendientes')



# ---------------------- pagos ----------------------
class PagosListView(LoginRequiredMixin, ListView):
    model = Pago
    template_name = 'cartera/pagos_list.html'
    context_object_name = 'pagos'
    paginate_by = 25

    def get_queryset(self):
        qs = (Pago.objects
              .select_related('factura', 'factura__proveedor', 'factura__punto_venta')
              .order_by('-fecha_pago', '-id'))

        pv = get_user_pdv(self.request.user)
        if pv:
            qs = qs.filter(factura__punto_venta=pv)

        # --- BÚSQUEDA GLOBAL ---
        q = (self.request.GET.get('q') or '').strip()
        if q:
            qnum = q.replace('.', '').replace(',', '')
            num_q = Q()
            if qnum.isdigit():
                try:
                    n = Decimal(qnum)
                    num_q = Q(valor_pagado=n)
                except Exception:
                    pass

            qs = qs.filter(
                Q(factura__numero_factura__icontains=q) |
                Q(factura__proveedor__nombre__icontains=q) |
                Q(factura__punto_venta__nombre__icontains=q) |
                Q(pagado_por__icontains=q) |
                Q(notas__icontains=q) |
                num_q
            )

        return qs



# ---------------------- pagos (individual) ----------------------
class PagoCreateView(LoginRequiredMixin, CreateView):
    model = Pago
    form_class = PagoForm
    template_name = 'cartera/pago_form.html'

    def dispatch(self, request, *args, **kwargs):
        self.factura = Factura.objects.get(pk=kwargs['pk'])
        # Regla: un pago por factura
        if self.factura.pagos.exists():
            messages.info(request, "Esta factura ya tiene un pago registrado.")
            return redirect('factura_detalle', pk=self.factura.pk)
        return super().dispatch(request, *args, **kwargs)

    def get_initial(self):
        initial = super().get_initial()
        initial.setdefault('fecha_pago', timezone.localdate())
        if self.factura and self.factura.valor_factura is not None:
            initial.setdefault('valor_pagado', self.factura.valor_factura)
        return initial

    def get_form_kwargs(self):
        kwargs = super().get_form_kwargs()
        kwargs['user'] = self.request.user
        kwargs['factura'] = self.factura
        return kwargs

    def form_valid(self, form):
        pago = form.save(commit=False)
        pago.factura = self.factura
        pago.valor_pagado = self.factura.valor_factura
        if not pago.fecha_pago:
            pago.fecha_pago = timezone.localdate()
        pago.save()

        self.factura.total_pagado = (self.factura.total_pagado or 0) + pago.valor_pagado
        self.factura.estado = 'pagada' if self.factura.total_pagado >= self.factura.valor_factura else 'pendiente'
        self.factura.save(update_fields=['total_pagado', 'estado'])

        messages.success(self.request, 'Pago registrado.')
        return redirect('factura_detalle', pk=self.factura.pk)

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        ctx['factura'] = self.factura
        return ctx



class PagoAdjuntarComprobanteView(LoginRequiredMixin, UpdateView):
    model = Pago
    form_class = PagoComprobanteForm
    template_name = "cartera/pago_adjuntar.html"

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


class PagoEnviarEmailView(View):
    def post(self, request, pk):
        pago = Pago.objects.select_related("factura__proveedor").get(pk=pk)

        # ⬇️ CORTAFUEGOS: no enviar correo si es pago de contado
        if _es_contado_por_notas(pago):
            messages.info(request, "Pago de contado: no se envía correo de confirmación.")
            return redirect("factura_detalle", pk=pago.factura.id)

        if not (pago.comprobante and pago.comprobante.name):
            messages.error(request, "Este pago no tiene comprobante adjunto.")
            return redirect("factura_detalle", pk=pago.factura.id)

        try:
            ok, info = enviar_recibo_pago(request, pago)
            if ok:
                messages.success(request, "Comprobante enviado al proveedor.")
            else:
                messages.error(request, f"No se envió: {info}")
        except Exception as e:
            messages.error(request, f"Error al enviar correo: {e}")

        return redirect("factura_detalle", pk=pago.factura.id)

# ------------------ confirmación proveedor (individual) ------------------
class ConfirmarPagoView(View):
    """
    Vista pública (sin login). Confirma el pago individual vía token.
    """
    def get(self, request, token):
        ok, valor = validar_token(token)
        if not ok:
            ctx = {"motivo": "El enlace no es válido o expiró."}
            return render(request, "cartera/confirmacion_error.html", ctx, status=400)

        try:
            pago = Pago.objects.select_related("factura__proveedor").get(id=valor)
        except Pago.DoesNotExist:
            ctx = {"motivo": "No encontramos el pago asociado a este enlace."}
            return render(request, "cartera/confirmacion_error.html", ctx, status=404)

        factura = pago.factura
        if not factura.confirmado_pago:
            factura.confirmado_pago = True
            factura.confirmado_fecha = timezone.now()
            factura.confirmado_por_email = factura.proveedor.email
            factura.save(update_fields=["confirmado_pago", "confirmado_fecha", "confirmado_por_email"])

        ctx = {
            "proveedor": factura.proveedor,
            "numero_factura": factura.numero_factura,
            "valor_factura": factura.valor_factura,
            "fecha_confirmacion": factura.confirmado_fecha,
        }
        return render(request, "cartera/confirmacion_publica.html", ctx, status=200)


# ------------------ pago por lote ------------------
# ---------------------- pago por lote ----------------------
class PagoLoteCreateView(LoginRequiredMixin, View):
    template_name = "cartera/pago_lote_form.html"

    # --- Helpers ---
    def _parse_ids(self, request):
        raw = (request.POST.get("ids") or request.GET.get("ids") or "").strip()
        ids = []
        for chunk in raw.replace(",", " ").split():
            if chunk.isdigit():
                ids.append(int(chunk))
        return ids

    def _facturas_validas(self, request, ids):
        qs = (Factura.objects
              .filter(pk__in=ids, estado='pendiente')
              .select_related('proveedor', 'punto_venta'))
        # sin pagos previos (regla actual)
        qs = qs.filter(pagos__isnull=True)
        pv = get_user_pdv(request.user)
        if pv:
            qs = qs.filter(punto_venta=pv)
        return list(qs)

    # --- GET ---
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
            messages.error(request, "Debes seleccionar facturas del mismo proveedor.")
            return redirect("facturas_pendientes")

        total = sum((f.valor_factura or 0) for f in facturas)

        # ⬇️ Forzamos fecha hoy en el primer render
        form = PagoLoteForm(
            user=request.user,
            pdv_default=facturas[0].punto_venta,
            initial={"fecha_pago": timezone.localdate()},
        )
        ctx = {
            "form": form,
            "proveedor": prov,
            "facturas": facturas,
            "total": total,
            "ids": ",".join(str(f.id) for f in facturas),
        }
        return render(request, self.template_name, ctx)

    # --- POST ---
    def post(self, request):
        ids = self._parse_ids(request)
        facturas = self._facturas_validas(request, ids)
        if not facturas:
            messages.error(request, "Las facturas seleccionadas no son válidas para pago.")
            return redirect("facturas_pendientes")

        prov = facturas[0].proveedor
        if any(f.proveedor_id != prov.id for f in facturas):
            messages.error(request, "Debes seleccionar facturas del mismo proveedor.")
            return redirect("facturas_pendientes")

        form = PagoLoteForm(request.POST, request.FILES, user=request.user,
                            pdv_default=facturas[0].punto_venta)
        if not form.is_valid():
            total = sum((f.valor_factura or 0) for f in facturas)
            return render(request, self.template_name, {
                "form": form,
                "proveedor": prov,
                "facturas": facturas,
                "total": total,
                "ids": ",".join(str(f.id) for f in facturas),
            })

        with transaction.atomic():
            # 1) Crear el lote (y guardar archivo)
            lote: PagoLote = form.save(commit=False)
            lote.proveedor = prov
            lote.save()  # aquí ya existe lote.comprobante.name si se subió

            # Nombre del archivo para clonarlo en cada Pago
            comp_name = lote.comprobante.name if getattr(lote, "comprobante", None) else None

            # 2) Crear pagos y actualizar facturas
            pagos_creados = []
            for f in facturas:
                pago = Pago(
                    factura=f,
                    fecha_pago=lote.fecha_pago,
                    valor_pagado=f.valor_factura or Decimal('0'),
                    pagado_por=lote.pagado_por,
                    lote=lote,
                    notas=f"Pago perteneciente al Lote #{lote.id}.",
                    # Clonamos el comprobante del lote al pago
                    comprobante=comp_name or None,
                )
                pagos_creados.append(pago)
                f.total_pagado = f.valor_factura or Decimal('0')
                f.estado = 'pagada'

            Pago.objects.bulk_create(pagos_creados)
            Factura.objects.bulk_update(facturas, ['total_pagado', 'estado'])

        # 3) Enviar correo del lote (UN solo correo)
        try:
            ok, info = enviar_recibo_lote(request, lote)
            if ok:
                messages.success(request, f"Lote #{lote.id} creado y correo enviado.")
            else:
                messages.warning(request, f"Lote #{lote.id} creado, pero correo NO enviado: {info}")
        except Exception as e:
            messages.warning(request, f"Lote #{lote.id} creado, pero el correo falló: {e}")

        return redirect("pagos_list")

# ------------------ confirmación proveedor (LOTE) ------------------
class ConfirmarPagoLoteView(View):
    """Confirma TODO el lote. Vista pública."""
    def get(self, request, token):
        ok, lote_id = validar_token_lote(token)
        if not ok:
            ctx = {"motivo": "El enlace no es válido o expiró."}
            return render(request, "cartera/confirmacion_error.html", ctx, status=400)

        try:
            lote = (PagoLote.objects
                    .select_related("proveedor")
                    .prefetch_related("pagos__factura")
                    .get(pk=lote_id))
        except PagoLote.DoesNotExist:
            ctx = {"motivo": "No encontramos el lote asociado a este enlace."}
            return render(request, "cartera/confirmacion_error.html", ctx, status=404)

        ahora = timezone.now()
        for p in lote.pagos.all():
            f = p.factura
            if not f.confirmado_pago:
                f.confirmado_pago = True
                f.confirmado_fecha = ahora
                f.confirmado_por_email = lote.proveedor.email
                f.save(update_fields=["confirmado_pago", "confirmado_fecha", "confirmado_por_email"])

        # Total del lote para la vista pública
        total_lote = sum((p.valor_pagado or Decimal("0")) for p in lote.pagos.all())

        ctx = {
            "proveedor": lote.proveedor,
            "lote": lote,
            "facturas": [p.factura for p in lote.pagos.all()],
            "fecha_confirmacion": ahora,
            "total": total_lote,
        }
        return render(request, "cartera/confirmacion_publica_lote.html", ctx, status=200)


# --------------------------- API ---------------------------
class ProveedorViewSet(viewsets.ModelViewSet):
    queryset = Proveedor.objects.all()
    serializer_class = ProveedorSerializer
    permission_classes = [permissions.IsAuthenticated]
    filter_backends = [DjangoFilterBackend, filters.SearchFilter, filters.OrderingFilter]
    search_fields = ['nombre', 'nit', 'email']
    ordering_fields = ['nombre', 'nit', 'creado_en']


class FacturaViewSet(viewsets.ModelViewSet):
    serializer_class = FacturaSerializer
    permission_classes = [permissions.IsAuthenticated]
    filter_backends = [DjangoFilterBackend, filters.SearchFilter, filters.OrderingFilter]
    filterset_fields = ['estado', 'proveedor', 'punto_venta', 'fecha_factura']
    search_fields = ['numero_factura', 'proveedor__nombre', 'punto_venta__nombre']
    ordering_fields = ['fecha_factura', 'valor_factura', 'creado_en']

    def get_queryset(self):
        qs = Factura.objects.select_related('proveedor', 'punto_venta')
        pv = get_user_pdv(self.request.user)
        if pv:
            qs = qs.filter(punto_venta=pv)
        return qs


class PagoViewSet(viewsets.ModelViewSet):
    serializer_class = PagoSerializer
    permission_classes = [permissions.IsAuthenticated]
    filter_backends = [DjangoFilterBackend, filters.SearchFilter, filters.OrderingFilter]
    filterset_fields = ['fecha_pago', 'pagado_por', 'factura', 'factura__proveedor']
    search_fields = ['pagado_por', 'factura__numero_factura', 'factura__proveedor__nombre']
    ordering_fields = ['fecha_pago', 'valor_pagado', 'creado_en']

    def get_queryset(self):
        qs = Pago.objects.select_related('factura', 'factura__proveedor', 'factura__punto_venta')
        pv = get_user_pdv(self.request.user)
        if pv:
            qs = qs.filter(factura__punto_venta=pv)
        return qs
    

# ---------------------- dashboard analítica ----------------------

from datetime import date, datetime, timedelta
from decimal import Decimal, InvalidOperation

from django.contrib.auth.decorators import login_required, user_passes_test
from django.db.models import Sum, Count, F, Avg, FloatField, Q
from django.db.models.functions import TruncMonth, Extract
from django.utils import timezone
from django.utils.dateparse import parse_date
from django.shortcuts import render

from .models import Factura, Pago, PuntoVenta, Proveedor


def _d(s, fallback):
    try:
        x = parse_date(s) if isinstance(s, str) else None
        return x or fallback
    except Exception:
        return fallback

def _j(x):
    import json
    return json.dumps(x, ensure_ascii=False, default=str)

def _month_bounds(today: date):
    first = date(today.year, today.month, 1)
    return first, today

def _safe_div(num, den):
    try:
        if not den or den == 0:
            return Decimal('0')
        return (Decimal(num) / Decimal(den)) * Decimal('100')
    except (InvalidOperation, ZeroDivisionError, TypeError):
        return Decimal('0')


@login_required
@user_passes_test(lambda u: u.is_staff)
def analytics_dashboard(request):
    """
    Tablero Staff:
    - Rango por defecto: mes_actual
    - Pendientes ignoran fechas (pero respetan PDV/Proveedor)
    - Se agregan KPIs avanzadas.
    """
    today = date.today()

    # ---- RANGO ----
    rango = (request.GET.get("rango") or "").strip() or "mes_actual"
    if rango == "mes_actual":
        d1_def, d2_def = _month_bounds(today)
    elif rango == "mes_anterior":
        y = today.year if today.month > 1 else today.year - 1
        m = today.month - 1 if today.month > 1 else 12
        d1_def = date(y, m, 1)
        from calendar import monthrange
        d2_def = date(y, m, monthrange(y, m)[1])
    elif rango == "ult_30":
        d2_def = today
        d1_def = today - timedelta(days=29)
    elif rango == "este_anio":
        d1_def, d2_def = date(today.year, 1, 1), today
    elif rango == "anio_pasado":
        d1_def, d2_def = date(today.year-1, 1, 1), date(today.year-1, 12, 31)
    else:
        d1_def, d2_def = _month_bounds(today)

    if (request.GET.get("rango") or "") == "personalizado":
        d1 = _d(request.GET.get("d1"), d1_def)
        d2 = _d(request.GET.get("d2"), d2_def)
    else:
        d1, d2 = d1_def, d2_def

    # ---- BASE con filtros PDV/PROV ----
    qs_base = Factura.objects.select_related("proveedor", "punto_venta").all()
    pdv = request.GET.get("pdv")
    prov = request.GET.get("prov")
    if pdv and str(pdv).isdigit():
        qs_base = qs_base.filter(punto_venta_id=int(pdv))
    if prov and str(prov).isdigit():
        qs_base = qs_base.filter(proveedor_id=int(prov))

    # rango para métricas de período
    qs_periodo = qs_base.filter(fecha_factura__range=[d1, d2])

    # ---- KPIs del período ----
    agg = qs_periodo.aggregate(total=Sum('valor_factura'), cnt=Count('id'))
    total_compras = agg['total'] or Decimal('0')
    total_facturas = agg['cnt'] or 0

    pagos_qs = Pago.objects.filter(factura__in=qs_periodo)
    pagado = pagos_qs.aggregate(t=Sum('valor_pagado'))['t'] or Decimal('0')

    dias_prom = (
        pagos_qs.annotate(
            secs=Extract(F('fecha_pago') - F('factura__fecha_factura'),
                         'epoch', output_field=FloatField())
        ).aggregate(avg_days=Avg(F('secs') / 86400.0))
    )['avg_days'] or 0.0

    # ---- Pendientes (IGNORA fechas) ----
    pend_qs = Factura.objects.filter(estado='pendiente')
    if pdv and str(pdv).isdigit():
        pend_qs = pend_qs.filter(punto_venta_id=int(pdv))
    if prov and str(prov).isdigit():
        pend_qs = pend_qs.filter(proveedor_id=int(prov))

    valor_pendiente = pend_qs.aggregate(
        t=Sum(F('valor_factura') - F('total_pagado'))
    )['t'] or Decimal('0')
    num_pendientes = pend_qs.count()

    # Antigüedad promedio y ticket prom de pendientes
    hoy_dt = timezone.localdate()
    if num_pendientes:
        edades = [
            (hoy_dt - f.fecha_factura).days
            for f in pend_qs.only('fecha_factura')
        ]
        antig_prom_pend = round(sum(edades) / len(edades), 1)
        from django.db.models import FloatField as FF
        ticket_prom_pend = pend_qs.aggregate(
            avg=Avg(F('valor_factura') - F('total_pagado'), output_field=FF())
        )['avg'] or 0.0
    else:
        antig_prom_pend = 0.0
        ticket_prom_pend = 0.0

    # ---- Extra KPIs del período ----
    pct_pagado = _safe_div(pagado, total_compras)

    total_pagos = pagos_qs.count() or 0
    pagos_contado = pagos_qs.filter(notas__icontains='auto-generado').count()
    pct_pagos_contado = _safe_div(pagos_contado, total_pagos)

    fact_con_pago = qs_periodo.filter(pagos__isnull=False).distinct().count() or 0
    fact_confirmadas = qs_periodo.filter(confirmado_pago=True).distinct().count() or 0
    tasa_confirmacion = _safe_div(fact_confirmadas, fact_con_pago)

    pagos_con_comp = pagos_qs.exclude(Q(comprobante__isnull=True) | Q(comprobante__exact='')).count()
    cobertura_comprobantes = _safe_div(pagos_con_comp, total_pagos)

    # shares por proveedor (sobre total del período)
    top_prov_agg = list(
        qs_periodo.values('proveedor__id', 'proveedor__nombre')
                  .annotate(total=Sum('valor_factura'))
                  .order_by('-total')[:3]
    )
    top_total = sum((r['total'] or 0) for r in top_prov_agg) or Decimal('0')
    share_top1 = _safe_div(top_prov_agg[0]['total'] if top_prov_agg else 0, total_compras)
    share_top3 = _safe_div(top_total, total_compras)

    # Δ vs mes anterior (sólo si rango es mes_actual)
    if rango == "mes_actual":
        # mismo filtro PDV/PROV pero en mes anterior
        y = today.year if today.month > 1 else today.year - 1
        m = today.month - 1 if today.month > 1 else 12
        from calendar import monthrange
        prev_d1 = date(y, m, 1)
        prev_d2 = date(y, m, monthrange(y, m)[1])
        prev_qs = Factura.objects.select_related("proveedor", "punto_venta")
        if pdv and str(pdv).isdigit():
            prev_qs = prev_qs.filter(punto_venta_id=int(pdv))
        if prov and str(prov).isdigit():
            prev_qs = prev_qs.filter(proveedor_id=int(prov))
        prev_qs = prev_qs.filter(fecha_factura__range=[prev_d1, prev_d2])
        prev_total = prev_qs.aggregate(t=Sum('valor_factura'))['t'] or Decimal('0')
        delta_mes_ant = _safe_div(total_compras - prev_total, prev_total)
        delta_monto = total_compras - prev_total
    else:
        delta_mes_ant = None   # lo mostramos como “—”
        delta_monto = Decimal('0')

    # Nuevas 7 días (por creado_en)
    start_7 = timezone.now() - timedelta(days=7)
    nuevas_7d = qs_base.filter(creado_en__gte=start_7).count()

    # ---- Series para gráficas ----
    top_prov = [
        {"id": r["proveedor__id"], "nombre": r["proveedor__nombre"], "total": r["total"]}
        for r in qs_periodo.values('proveedor__id', 'proveedor__nombre')
                          .annotate(total=Sum('valor_factura'))
                          .order_by('-total')[:10]
    ]
    por_pdv = [
        {"id": r["punto_venta__id"], "nombre": r["punto_venta__nombre"], "total": r["total"]}
        for r in qs_periodo.values('punto_venta__id', 'punto_venta__nombre')
                          .annotate(total=Sum('valor_factura'))
                          .order_by('-total')
    ]
    by_month_qs = (qs_periodo
                   .annotate(m=TruncMonth('fecha_factura'))
                   .values('m')
                   .annotate(total=Sum('valor_factura'))
                   .order_by('m'))
    by_month = []
    for r in by_month_qs:
        m = r["m"]
        if isinstance(m, datetime):
            m = m.date()
        by_month.append({"m": m.isoformat(), "total": r["total"]})

    top_facturas = list(
        qs_periodo.order_by('-valor_factura')
                  .values('id','numero_factura','fecha_factura',
                          'proveedor__nombre','punto_venta__nombre','valor_factura')[:12]
    )

    # ---- combos ----
    pdvs = PuntoVenta.objects.order_by('nombre').values('id', 'nombre')
    provs = Proveedor.objects.order_by('nombre').values('id', 'nombre')

    ctx = {
        "filters": {
            "pdv": int(pdv) if pdv and str(pdv).isdigit() else "",
            "prov": int(prov) if prov and str(prov).isdigit() else "",
            "d1": d1.isoformat(),
            "d2": d2.isoformat(),
            "rango": rango,
        },
        "pdvs": list(pdvs),
        "provs": list(provs),

        # KPIs básicas
        "kpi_total": total_compras,
        "kpi_facturas": total_facturas,
        "kpi_pagado": pagado,
        "kpi_dias": round(float(dias_prom), 1),

        # Pendientes (sin rango)
        "kpi_valor_pendiente": valor_pendiente,
        "kpi_num_pendientes": num_pendientes,
        "kpi_antig_pend": antig_prom_pend,
        "kpi_ticket_pend": ticket_prom_pend,

        # Avanzadas
        "kpi_pct_pagado": round(pct_pagado, 1),
        "kpi_pct_contado": round(pct_pagos_contado, 1),
        "kpi_tasa_conf": round(tasa_confirmacion, 1),
        "kpi_cob_comp": round(cobertura_comprobantes, 1),
        "kpi_share_top1": round(share_top1, 1),
        "kpi_share_top3": round(share_top3, 1),
        "kpi_delta_mes_ant": None if delta_mes_ant is None else round(delta_mes_ant, 1),
        "kpi_delta_monto": delta_monto,
        "kpi_nuevas_7d": nuevas_7d,

        # series
        "top_prov_json": _j(top_prov),
        "por_pdv_json": _j(por_pdv),
        "by_month_json": _j(by_month),
        "top_facturas": top_facturas,
    }
    return render(request, "cartera/analytics_dashboard.html", ctx)
