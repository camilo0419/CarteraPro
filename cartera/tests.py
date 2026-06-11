from datetime import date
from decimal import Decimal
from unittest import mock

from django.conf import settings
from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import SimpleTestCase, TestCase, override_settings
from django.urls import reverse
from rest_framework.test import APIClient

from .forms import FacturaForm
from .models import (
    CorreoEnvioLog,
    EventoAuditoria,
    Factura,
    NotificacionProveedor,
    Pago,
    PagoLote,
    Proveedor,
    ProveedorUsuario,
    PuntoVenta,
    PuntoVentaUsuario,
)
from .services.payments import confirmar_lote, crear_pago, eliminar_pago_seguro, recalcular_factura
from .utils import enviar_recibo_pago, firmar_token, firmar_token_lote
from .validators import validate_comprobante_file


User = get_user_model()

TEST_STORAGES = {
    "default": {"BACKEND": "django.core.files.storage.FileSystemStorage"},
    "staticfiles": {"BACKEND": "django.contrib.staticfiles.storage.StaticFilesStorage"},
}


class SettingsEnvironmentTests(SimpleTestCase):
    def test_manage_py_test_uses_sqlite_without_postgres(self):
        self.assertEqual(settings.APP_ENV, "test")
        self.assertEqual(settings.DATABASES["default"]["ENGINE"], "django.db.backends.sqlite3")
        self.assertEqual(settings.PASSWORD_HASHERS, ["django.contrib.auth.hashers.MD5PasswordHasher"])


@override_settings(STORAGES=TEST_STORAGES)
class CarteraBaseTestCase(TestCase):
    def setUp(self):
        self.staff = User.objects.create_user("staff", password="pass", is_staff=True)
        self.user = User.objects.create_user("pdv-user", password="pass")
        self.other_user = User.objects.create_user("other-user", password="pass")
        self.pv = PuntoVenta.objects.create(nombre="PDV Centro", ciudad="Medellín", usuario=self.user)
        self.other_pv = PuntoVenta.objects.create(nombre="PDV Norte", ciudad="Medellín", usuario=self.other_user)
        PuntoVentaUsuario.objects.create(user=self.user, punto_venta=self.pv)
        PuntoVentaUsuario.objects.create(user=self.other_user, punto_venta=self.other_pv)
        self.proveedor = Proveedor.objects.create(nombre="Proveedor Uno", nit="900", email="proveedor@example.com")
        self.factura = Factura.objects.create(
            proveedor=self.proveedor,
            punto_venta=self.pv,
            numero_factura="F-001",
            fecha_factura=date(2026, 1, 1),
            valor_factura=Decimal("100000.00"),
            creado_por=self.user,
        )
        self.other_factura = Factura.objects.create(
            proveedor=self.proveedor,
            punto_venta=self.other_pv,
            numero_factura="F-002",
            fecha_factura=date(2026, 1, 2),
            valor_factura=Decimal("200000.00"),
            creado_por=self.other_user,
        )


@override_settings(STORAGES=TEST_STORAGES)
class FacturaFormValueFormatTests(CarteraBaseTestCase):
    def _form_for_value(self, raw_value, numero):
        return FacturaForm(
            data={
                "proveedor": self.proveedor.id,
                "punto_venta": self.pv.id,
                "numero_factura": numero,
                "fecha_factura": "2026-03-01",
                "valor_factura": raw_value,
                "estado": "pendiente",
                "confirmar_valor_alto": "1",
            },
            user=self.staff,
        )

    def test_valor_factura_accepts_colombian_formats(self):
        cases = [
            ("300000", Decimal("300000.00")),
            ("300.000", Decimal("300000.00")),
            ("300000.00", Decimal("300000.00")),
            ("300.000,00", Decimal("300000.00")),
            ("1.200.000", Decimal("1200000.00")),
        ]
        for index, (raw_value, expected) in enumerate(cases, start=1):
            with self.subTest(raw_value=raw_value):
                form = self._form_for_value(raw_value, f"FMT-{index}")
                self.assertTrue(form.is_valid(), form.errors)
                self.assertEqual(form.cleaned_data["valor_factura"], expected)


@override_settings(STORAGES=TEST_STORAGES)
class PermissionScopeTests(CarteraBaseTestCase):
    def test_normal_user_cannot_see_other_pdv_invoice_detail(self):
        self.client.force_login(self.user)
        response = self.client.get(reverse("factura_detalle", args=[self.other_factura.pk]))
        self.assertEqual(response.status_code, 404)

    def test_staff_can_see_all_pdv_invoice_detail(self):
        self.client.force_login(self.staff)
        response = self.client.get(reverse("factura_detalle", args=[self.other_factura.pk]))
        self.assertEqual(response.status_code, 200)


@override_settings(STORAGES=TEST_STORAGES)
class ApiScopeTests(CarteraBaseTestCase):
    def setUp(self):
        super().setUp()
        self.api = APIClient()

    def test_api_list_is_scoped_to_user_pdv(self):
        self.api.force_authenticate(self.user)
        response = self.api.get(reverse("factura-list"))
        self.assertEqual(response.status_code, 200)
        rows = response.data.get("results", response.data)
        ids = {row["id"] for row in rows}
        self.assertIn(self.factura.id, ids)
        self.assertNotIn(self.other_factura.id, ids)

    def test_api_normal_user_cannot_create_invoice_for_other_pdv(self):
        self.api.force_authenticate(self.user)
        payload = {
            "proveedor": self.proveedor.id,
            "punto_venta": self.other_pv.id,
            "numero_factura": "API-001",
            "fecha_factura": "2026-02-01",
            "valor_factura": "50000.00",
            "estado": "pendiente",
        }
        response = self.api.post(reverse("factura-list"), payload, format="json")
        self.assertEqual(response.status_code, 400)
        self.assertFalse(Factura.objects.filter(numero_factura="API-001").exists())

    def test_api_normal_user_create_invoice_is_assigned_to_own_pdv(self):
        self.api.force_authenticate(self.user)
        payload = {
            "proveedor": self.proveedor.id,
            "numero_factura": "API-OWN",
            "fecha_factura": "2026-02-01",
            "valor_factura": "50000.00",
            "estado": "pendiente",
        }
        response = self.api.post(reverse("factura-list"), payload, format="json")
        self.assertEqual(response.status_code, 201)
        factura = Factura.objects.get(numero_factura="API-OWN")
        self.assertEqual(factura.punto_venta, self.pv)

    def test_api_normal_user_cannot_create_payment_for_other_pdv(self):
        self.api.force_authenticate(self.user)
        payload = {
            "factura": self.other_factura.id,
            "fecha_pago": "2026-02-10",
            "pagado_por": f"PDV - {self.pv.nombre}",
        }
        response = self.api.post(reverse("pago-list"), payload, format="json")
        self.assertEqual(response.status_code, 400)
        self.assertFalse(Pago.objects.filter(factura=self.other_factura).exists())

    def test_api_normal_user_cannot_update_invoice_outside_pdv(self):
        self.api.force_authenticate(self.user)
        response = self.api.patch(
            reverse("factura-detail", args=[self.other_factura.pk]),
            {"estado": "pagada"},
            format="json",
        )
        self.assertEqual(response.status_code, 404)
        self.other_factura.refresh_from_db()
        self.assertEqual(self.other_factura.estado, "pendiente")

    def test_api_normal_user_cannot_delete_invoice_outside_pdv(self):
        self.api.force_authenticate(self.user)
        response = self.api.delete(reverse("factura-detail", args=[self.other_factura.pk]))
        self.assertEqual(response.status_code, 404)
        self.assertTrue(Factura.objects.filter(pk=self.other_factura.pk).exists())

    def test_api_normal_user_can_list_proveedores(self):
        self.api.force_authenticate(self.user)
        response = self.api.get(reverse("proveedor-list"))
        self.assertEqual(response.status_code, 200)
        rows = response.data.get("results", response.data)
        ids = {row["id"] for row in rows}
        self.assertIn(self.proveedor.id, ids)

    def test_api_normal_user_cannot_create_proveedor(self):
        self.api.force_authenticate(self.user)
        payload = {
            "nombre": "Proveedor API",
            "nit": "901",
            "email": "proveedor-api@example.com",
            "telefono": "3001234567",
        }
        response = self.api.post(reverse("proveedor-list"), payload, format="json")
        self.assertEqual(response.status_code, 403)
        self.assertFalse(Proveedor.objects.filter(nit="901").exists())

    def test_api_normal_user_cannot_update_proveedor(self):
        self.api.force_authenticate(self.user)
        response = self.api.patch(
            reverse("proveedor-detail", args=[self.proveedor.pk]),
            {"telefono": "3001234567"},
            format="json",
        )
        self.assertEqual(response.status_code, 403)
        self.proveedor.refresh_from_db()
        self.assertNotEqual(self.proveedor.telefono, "3001234567")

    def test_api_normal_user_cannot_delete_proveedor(self):
        self.api.force_authenticate(self.user)
        response = self.api.delete(reverse("proveedor-detail", args=[self.proveedor.pk]))
        self.assertEqual(response.status_code, 403)
        self.assertTrue(Proveedor.objects.filter(pk=self.proveedor.pk).exists())

    def test_api_staff_can_create_proveedor(self):
        self.api.force_authenticate(self.staff)
        payload = {
            "nombre": "Proveedor Staff",
            "nit": "902",
            "email": "proveedor-staff@example.com",
            "telefono": "3007654321",
        }
        response = self.api.post(reverse("proveedor-list"), payload, format="json")
        self.assertEqual(response.status_code, 201)
        self.assertTrue(Proveedor.objects.filter(nit="902").exists())


@override_settings(STORAGES=TEST_STORAGES)
class BusinessServiceTests(CarteraBaseTestCase):
    def test_recalcular_factura_from_service(self):
        Pago.objects.create(
            factura=self.factura,
            fecha_pago=date(2026, 2, 1),
            valor_pagado=Decimal("40000.00"),
            pagado_por="OFICINA",
        )
        recalcular_factura(self.factura)
        self.factura.refresh_from_db()
        self.assertEqual(self.factura.total_pagado, Decimal("40000.00"))
        self.assertEqual(self.factura.estado, "pendiente")

    def test_crear_pago_creates_audit_event(self):
        pago = crear_pago(
            factura=self.factura,
            fecha_pago=date(2026, 2, 2),
            valor_pagado=Decimal("100000.00"),
            pagado_por=f"PDV - {self.pv.nombre}",
            usuario=self.user,
        )
        self.factura.refresh_from_db()
        self.assertEqual(self.factura.total_pagado, Decimal("100000.00"))
        self.assertEqual(self.factura.estado, "pagada")
        self.assertTrue(
            EventoAuditoria.objects.filter(
                tipo=EventoAuditoria.TIPO_PAGO_CREADO,
                factura=self.factura,
                pago=pago,
                usuario=self.user,
            ).exists()
        )

    def test_eliminar_pago_recalculates_and_creates_audit_event(self):
        pago_a = crear_pago(
            factura=self.factura,
            fecha_pago=date(2026, 2, 2),
            valor_pagado=Decimal("60000.00"),
            pagado_por="OFICINA",
            usuario=self.staff,
        )
        crear_pago(
            factura=self.factura,
            fecha_pago=date(2026, 2, 3),
            valor_pagado=Decimal("40000.00"),
            pagado_por="OFICINA",
            usuario=self.staff,
        )
        pago_a_id = pago_a.pk
        eliminar_pago_seguro(pago_a, usuario=self.staff)
        self.factura.refresh_from_db()
        self.assertEqual(self.factura.total_pagado, Decimal("40000.00"))
        self.assertEqual(self.factura.estado, "pendiente")
        evento = EventoAuditoria.objects.get(tipo=EventoAuditoria.TIPO_PAGO_ELIMINADO)
        self.assertEqual(evento.factura, self.factura)
        self.assertEqual(evento.usuario, self.staff)
        self.assertEqual(evento.metadata["pago_id"], pago_a_id)


@override_settings(STORAGES=TEST_STORAGES)
class ComprobanteValidationTests(TestCase):
    def test_rejects_dangerous_extension(self):
        uploaded = SimpleUploadedFile("comprobante.exe", b"fake", content_type="application/octet-stream")
        with self.assertRaises(ValidationError):
            validate_comprobante_file(uploaded)

    @override_settings(COMPROBANTE_MAX_UPLOAD_SIZE=4)
    def test_rejects_large_file(self):
        uploaded = SimpleUploadedFile("comprobante.pdf", b"12345", content_type="application/pdf")
        with self.assertRaises(ValidationError):
            validate_comprobante_file(uploaded)

    def test_accepts_pdf(self):
        uploaded = SimpleUploadedFile("comprobante.pdf", b"%PDF-1.4", content_type="application/pdf")
        validate_comprobante_file(uploaded)


@override_settings(STORAGES=TEST_STORAGES)
class PublicConfirmationTests(CarteraBaseTestCase):
    def setUp(self):
        super().setUp()
        self.pago = Pago.objects.create(
            factura=self.factura,
            fecha_pago=date(2026, 2, 5),
            valor_pagado=self.factura.valor_factura,
            pagado_por=f"PDV - {self.pv.nombre}",
        )
        self.factura.total_pagado = self.factura.valor_factura
        self.factura.estado = "pagada"
        self.factura.save(update_fields=["total_pagado", "estado"])
        self.url = reverse("pago_confirmar", args=[firmar_token(self.pago.id)])

    def test_get_does_not_confirm_payment(self):
        response = self.client.get(self.url)
        self.assertEqual(response.status_code, 200)
        self.factura.refresh_from_db()
        self.assertFalse(self.factura.confirmado_pago)
        self.assertContains(response, "Confirmar recepción")

    def test_post_confirms_payment(self):
        response = self.client.post(self.url, REMOTE_ADDR="10.0.0.10", HTTP_USER_AGENT="CarteraTest/1.0")
        self.assertEqual(response.status_code, 200)
        self.factura.refresh_from_db()
        self.assertTrue(self.factura.confirmado_pago)
        evento = EventoAuditoria.objects.get(tipo=EventoAuditoria.TIPO_CONFIRMACION_FACTURA_PUBLICA)
        self.assertEqual(evento.factura, self.factura)
        self.assertEqual(evento.pago, self.pago)
        self.assertEqual(evento.ip_address, "10.0.0.10")
        self.assertEqual(evento.user_agent, "CarteraTest/1.0")

    def test_post_confirms_lote_and_creates_audit_event(self):
        lote = PagoLote.objects.create(
            proveedor=self.proveedor,
            fecha_pago=date(2026, 2, 6),
            pagado_por="OFICINA",
            comprobante="comprobantes/lote-test.pdf",
        )
        pago_lote = Pago.objects.create(
            factura=self.other_factura,
            fecha_pago=date(2026, 2, 6),
            valor_pagado=self.other_factura.valor_factura,
            pagado_por="OFICINA",
            lote=lote,
        )
        self.other_factura.total_pagado = self.other_factura.valor_factura
        self.other_factura.estado = "pagada"
        self.other_factura.save(update_fields=["total_pagado", "estado"])

        response = self.client.post(
            reverse("pago_lote_confirmar", args=[firmar_token_lote(lote.id)]),
            REMOTE_ADDR="10.0.0.11",
            HTTP_USER_AGENT="CarteraLoteTest/1.0",
        )

        self.assertEqual(response.status_code, 200)
        self.other_factura.refresh_from_db()
        self.assertTrue(self.other_factura.confirmado_pago)
        evento = EventoAuditoria.objects.get(tipo=EventoAuditoria.TIPO_CONFIRMACION_LOTE_PUBLICA)
        self.assertEqual(evento.lote, lote)
        self.assertEqual(evento.metadata["pagos"], [pago_lote.pk])
        self.assertEqual(evento.metadata["facturas_confirmadas"], [self.other_factura.pk])
        self.assertEqual(evento.ip_address, "10.0.0.11")


@override_settings(STORAGES=TEST_STORAGES)
class PaidInvoiceListTests(CarteraBaseTestCase):
    def test_paid_invoice_list_shows_payment_date(self):
        Pago.objects.create(
            factura=self.factura,
            fecha_pago=date(2026, 2, 5),
            valor_pagado=self.factura.valor_factura,
            pagado_por=f"PDV - {self.pv.nombre}",
        )
        self.factura.total_pagado = self.factura.valor_factura
        self.factura.estado = "pagada"
        self.factura.save(update_fields=["total_pagado", "estado"])
        self.client.force_login(self.user)
        response = self.client.get(reverse("pagos_list"))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Fecha de pago")
        self.assertContains(response, "05/02/2026")


@override_settings(STORAGES=TEST_STORAGES)
class EmailTests(CarteraBaseTestCase):
    def test_enviar_recibo_pago_uses_attachment_and_logs_success(self):
        pago = Pago.objects.create(
            factura=self.factura,
            fecha_pago=date(2026, 2, 5),
            valor_pagado=self.factura.valor_factura,
            pagado_por=f"PDV - {self.pv.nombre}",
            comprobante="comprobantes/test.pdf",
        )
        with mock.patch("cartera.utils._attach_fieldfile") as attach, mock.patch(
            "cartera.utils.EmailMultiAlternatives.send", return_value=1
        ):
            ok, info = enviar_recibo_pago(None, pago)
        self.assertTrue(ok)
        self.assertEqual(info, "Enviado")
        attach.assert_called_once()
        self.assertTrue(CorreoEnvioLog.objects.filter(pago=pago, exito=True).exists())
        self.assertTrue(
            EventoAuditoria.objects.filter(
                tipo=EventoAuditoria.TIPO_CORREO_ENVIADO,
                factura=self.factura,
                pago=pago,
                metadata__exito=True,
            ).exists()
        )


@override_settings(STORAGES=TEST_STORAGES)
class PortalProveedorTests(CarteraBaseTestCase):
    def setUp(self):
        super().setUp()
        self.portal_user = User.objects.create_user("proveedor-user", password="pass")
        self.portal_user_sin_permiso = User.objects.create_user("proveedor-sin-confirmar", password="pass")
        self.portal_user_inactivo = User.objects.create_user("proveedor-inactivo", password="pass")
        self.proveedor_b = Proveedor.objects.create(nombre="Proveedor Dos", nit="901", email="proveedor2@example.com")
        self.factura_b = Factura.objects.create(
            proveedor=self.proveedor_b,
            punto_venta=self.other_pv,
            numero_factura="FB-001",
            fecha_factura=date(2026, 1, 5),
            valor_factura=Decimal("300000.00"),
        )
        self.pago = Pago.objects.create(
            factura=self.factura,
            fecha_pago=date(2026, 2, 1),
            valor_pagado=self.factura.valor_factura,
            pagado_por="OFICINA",
            comprobante="comprobantes/pago-a.pdf",
        )
        self.factura.total_pagado = self.factura.valor_factura
        self.factura.estado = "pagada"
        self.factura.save(update_fields=["total_pagado", "estado"])
        self.pago_b = Pago.objects.create(
            factura=self.factura_b,
            fecha_pago=date(2026, 2, 2),
            valor_pagado=self.factura_b.valor_factura,
            pagado_por="OFICINA",
            comprobante="comprobantes/pago-b.pdf",
        )
        self.factura_b.total_pagado = self.factura_b.valor_factura
        self.factura_b.estado = "pagada"
        self.factura_b.save(update_fields=["total_pagado", "estado"])
        ProveedorUsuario.objects.create(user=self.portal_user, proveedor=self.proveedor)
        ProveedorUsuario.objects.create(
            user=self.portal_user_sin_permiso,
            proveedor=self.proveedor,
            puede_confirmar_pagos=False,
        )
        ProveedorUsuario.objects.create(user=self.portal_user_inactivo, proveedor=self.proveedor, activo=False)
        self.api = APIClient()

    def test_anonymous_user_redirects_to_login_before_portal_scope(self):
        response = self.client.get(reverse("portal_proveedor_dashboard"))
        self.assertEqual(response.status_code, 302)
        self.assertIn("/accounts/login/", response["Location"])

    def test_active_provider_user_can_enter_portal(self):
        self.client.force_login(self.portal_user)
        response = self.client.get(reverse("portal_proveedor_dashboard"))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Panel del proveedor")

    def test_user_without_active_provider_cannot_enter_portal(self):
        self.client.force_login(self.other_user)
        response = self.client.get(reverse("portal_proveedor_dashboard"))
        self.assertEqual(response.status_code, 403)

    def test_inactive_provider_user_cannot_enter_portal(self):
        self.client.force_login(self.portal_user_inactivo)
        response = self.client.get(reverse("portal_proveedor_dashboard"))
        self.assertEqual(response.status_code, 403)

    def test_staff_internal_dashboard_still_works(self):
        self.client.force_login(self.staff)
        response = self.client.get(reverse("dashboard"))
        self.assertEqual(response.status_code, 200)

    def test_provider_user_without_staff_cannot_access_internal_panel(self):
        self.client.force_login(self.portal_user)
        for url in [reverse("dashboard"), reverse("analytics_dashboard"), reverse("facturas_pendientes")]:
            with self.subTest(url=url):
                response = self.client.get(url)
                self.assertEqual(response.status_code, 403)

    def test_provider_user_without_staff_cannot_list_internal_factura_api(self):
        self.api.force_authenticate(self.portal_user)
        response = self.api.get(reverse("factura-list"))
        self.assertEqual(response.status_code, 403)

    def test_provider_a_does_not_see_provider_b_invoice(self):
        self.client.force_login(self.portal_user)
        response = self.client.get(reverse("portal_proveedor_facturas"))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, self.factura.numero_factura)
        self.assertNotContains(response, self.factura_b.numero_factura)

    def test_provider_a_cannot_open_provider_b_invoice_detail(self):
        self.client.force_login(self.portal_user)
        response = self.client.get(reverse("portal_proveedor_factura_detail", args=[self.factura_b.pk]))
        self.assertEqual(response.status_code, 404)

    def test_provider_a_does_not_see_provider_b_payment(self):
        self.client.force_login(self.portal_user)
        response = self.client.get(reverse("portal_proveedor_pagos"))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, self.factura.numero_factura)
        self.assertNotContains(response, self.factura_b.numero_factura)

    def test_provider_a_cannot_view_provider_b_comprobante(self):
        self.client.force_login(self.portal_user)
        response = self.client.get(reverse("portal_proveedor_comprobante", args=[self.pago_b.pk]))
        self.assertEqual(response.status_code, 404)

    def test_provider_can_view_own_comprobante_and_audit_is_created(self):
        self.client.force_login(self.portal_user)
        response = self.client.get(reverse("portal_proveedor_comprobante", args=[self.pago.pk]))
        self.assertEqual(response.status_code, 302)
        self.assertIn("pago-a.pdf", response["Location"])
        self.assertTrue(
            EventoAuditoria.objects.filter(
                tipo=EventoAuditoria.TIPO_COMPROBANTE_VISUALIZADO,
                pago=self.pago,
                usuario=self.portal_user,
            ).exists()
        )

    def test_pago_without_comprobante_returns_controlled_error(self):
        factura = Factura.objects.create(
            proveedor=self.proveedor,
            punto_venta=self.pv,
            numero_factura="SC-001",
            fecha_factura=date(2026, 2, 6),
            valor_factura=Decimal("90000.00"),
        )
        pago = Pago.objects.create(
            factura=factura,
            fecha_pago=date(2026, 2, 7),
            valor_pagado=factura.valor_factura,
            pagado_por="OFICINA",
        )
        self.client.force_login(self.portal_user)
        response = self.client.get(reverse("portal_proveedor_comprobante", args=[pago.pk]))
        self.assertEqual(response.status_code, 403)
        self.assertFalse(EventoAuditoria.objects.filter(tipo=EventoAuditoria.TIPO_COMPROBANTE_VISUALIZADO, pago=pago).exists())

    def test_provider_with_permission_can_confirm_own_payment(self):
        self.client.force_login(self.portal_user)
        response = self.client.post(reverse("portal_proveedor_pago_confirmar", args=[self.pago.pk]))
        self.assertEqual(response.status_code, 302)
        self.factura.refresh_from_db()
        self.assertTrue(self.factura.confirmado_pago)
        self.assertTrue(
            EventoAuditoria.objects.filter(
                tipo=EventoAuditoria.TIPO_CONFIRMACION_PAGO_PORTAL,
                factura=self.factura,
                pago=self.pago,
                usuario=self.portal_user,
            ).exists()
        )

    def test_get_payment_confirmation_does_not_mutate(self):
        self.client.force_login(self.portal_user)
        response = self.client.get(reverse("portal_proveedor_pago_confirmar", args=[self.pago.pk]))
        self.assertEqual(response.status_code, 302)
        self.factura.refresh_from_db()
        self.assertFalse(self.factura.confirmado_pago)
        self.assertFalse(EventoAuditoria.objects.filter(tipo=EventoAuditoria.TIPO_CONFIRMACION_PAGO_PORTAL, pago=self.pago).exists())

    def test_provider_without_confirm_permission_cannot_confirm_payment(self):
        self.client.force_login(self.portal_user_sin_permiso)
        response = self.client.post(reverse("portal_proveedor_pago_confirmar", args=[self.pago.pk]))
        self.assertEqual(response.status_code, 403)
        self.factura.refresh_from_db()
        self.assertFalse(self.factura.confirmado_pago)

    def test_double_payment_confirmation_does_not_duplicate_portal_event(self):
        self.client.force_login(self.portal_user)
        url = reverse("portal_proveedor_pago_confirmar", args=[self.pago.pk])
        self.client.post(url)
        self.client.post(url)
        self.factura.refresh_from_db()
        self.assertTrue(self.factura.confirmado_pago)
        self.assertEqual(
            EventoAuditoria.objects.filter(
                tipo=EventoAuditoria.TIPO_CONFIRMACION_PAGO_PORTAL,
                factura=self.factura,
                pago=self.pago,
            ).count(),
            1,
        )

    def test_provider_a_cannot_confirm_provider_b_payment(self):
        self.client.force_login(self.portal_user)
        response = self.client.post(reverse("portal_proveedor_pago_confirmar", args=[self.pago_b.pk]))
        self.assertEqual(response.status_code, 404)
        self.factura_b.refresh_from_db()
        self.assertFalse(self.factura_b.confirmado_pago)

    def test_provider_can_confirm_own_lote(self):
        lote = PagoLote.objects.create(
            proveedor=self.proveedor,
            fecha_pago=date(2026, 2, 3),
            pagado_por="OFICINA",
            comprobante="comprobantes/lote-a.pdf",
        )
        factura_a2 = Factura.objects.create(
            proveedor=self.proveedor,
            punto_venta=self.pv,
            numero_factura="FA-002",
            fecha_factura=date(2026, 1, 6),
            valor_factura=Decimal("250000.00"),
        )
        self.pago.lote = lote
        self.pago.save(update_fields=["lote"])
        pago_a2 = Pago.objects.create(
            factura=factura_a2,
            fecha_pago=date(2026, 2, 3),
            valor_pagado=factura_a2.valor_factura,
            pagado_por="OFICINA",
            comprobante="comprobantes/pago-a2.pdf",
            lote=lote,
        )
        self.client.force_login(self.portal_user)
        response = self.client.post(reverse("portal_proveedor_lote_confirmar", args=[lote.pk]))
        self.assertEqual(response.status_code, 302)
        self.factura.refresh_from_db()
        factura_a2.refresh_from_db()
        self.assertTrue(self.factura.confirmado_pago)
        self.assertTrue(factura_a2.confirmado_pago)
        evento = EventoAuditoria.objects.get(tipo=EventoAuditoria.TIPO_CONFIRMACION_LOTE_PORTAL, lote=lote)
        self.assertEqual(evento.usuario, self.portal_user)
        self.assertEqual(evento.metadata["origen"], "portal_proveedor")
        self.assertCountEqual(evento.metadata["pagos"], [self.pago.pk, pago_a2.pk])
        self.assertCountEqual(evento.metadata["facturas"], [self.factura.pk, factura_a2.pk])

    def test_provider_can_view_own_lote_detail(self):
        lote = PagoLote.objects.create(
            proveedor=self.proveedor,
            fecha_pago=date(2026, 2, 3),
            pagado_por="OFICINA",
            comprobante="comprobantes/lote-a.pdf",
        )
        self.pago.lote = lote
        self.pago.save(update_fields=["lote"])
        self.client.force_login(self.portal_user)
        response = self.client.get(reverse("portal_proveedor_lote_detail", args=[lote.pk]))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, self.factura.numero_factura)

    def test_get_lote_confirmation_does_not_mutate(self):
        lote = PagoLote.objects.create(
            proveedor=self.proveedor,
            fecha_pago=date(2026, 2, 3),
            pagado_por="OFICINA",
            comprobante="comprobantes/lote-a.pdf",
        )
        self.pago.lote = lote
        self.pago.save(update_fields=["lote"])
        self.client.force_login(self.portal_user)
        response = self.client.get(reverse("portal_proveedor_lote_confirmar", args=[lote.pk]))
        self.assertEqual(response.status_code, 302)
        self.factura.refresh_from_db()
        self.assertFalse(self.factura.confirmado_pago)
        self.assertFalse(EventoAuditoria.objects.filter(tipo=EventoAuditoria.TIPO_CONFIRMACION_LOTE_PORTAL, lote=lote).exists())

    def test_pago_clean_rejects_lote_from_other_provider(self):
        lote_b = PagoLote.objects.create(
            proveedor=self.proveedor_b,
            fecha_pago=date(2026, 2, 3),
            pagado_por="OFICINA",
            comprobante="comprobantes/lote-b.pdf",
        )
        pago = Pago(
            factura=self.factura,
            fecha_pago=date(2026, 2, 3),
            valor_pagado=self.factura.valor_factura,
            pagado_por="OFICINA",
            comprobante="comprobantes/pago-a.pdf",
            lote=lote_b,
        )
        with self.assertRaisesMessage(ValidationError, "Un lote solo puede contener pagos del mismo proveedor."):
            pago.full_clean()

    def test_crear_pago_rejects_lote_from_other_provider(self):
        lote_b = PagoLote.objects.create(
            proveedor=self.proveedor_b,
            fecha_pago=date(2026, 2, 3),
            pagado_por="OFICINA",
            comprobante="comprobantes/lote-b.pdf",
        )
        with self.assertRaisesMessage(ValidationError, "Un lote solo puede contener pagos del mismo proveedor."):
            crear_pago(
                factura=self.factura,
                fecha_pago=date(2026, 2, 3),
                valor_pagado=self.factura.valor_factura,
                pagado_por="OFICINA",
                lote=lote_b,
            )

    def test_confirmar_lote_rejects_existing_mixed_lote(self):
        lote = PagoLote.objects.create(
            proveedor=self.proveedor,
            fecha_pago=date(2026, 2, 3),
            pagado_por="OFICINA",
            comprobante="comprobantes/lote-mixto.pdf",
        )
        self.pago.lote = lote
        self.pago.save(update_fields=["lote"])
        self.pago_b.lote = lote
        self.pago_b.save(update_fields=["lote"])
        with self.assertRaisesMessage(ValidationError, "Un lote solo puede contener pagos del mismo proveedor."):
            confirmar_lote(lote, proveedor=self.proveedor)
        self.factura.refresh_from_db()
        self.factura_b.refresh_from_db()
        self.assertFalse(self.factura.confirmado_pago)
        self.assertFalse(self.factura_b.confirmado_pago)

    def test_provider_cannot_confirm_other_provider_lote(self):
        lote_b = PagoLote.objects.create(
            proveedor=self.proveedor_b,
            fecha_pago=date(2026, 2, 4),
            pagado_por="OFICINA",
            comprobante="comprobantes/lote-b.pdf",
        )
        self.pago_b.lote = lote_b
        self.pago_b.save(update_fields=["lote"])
        self.client.force_login(self.portal_user)
        detail_response = self.client.get(reverse("portal_proveedor_lote_detail", args=[lote_b.pk]))
        self.assertEqual(detail_response.status_code, 404)
        response = self.client.post(reverse("portal_proveedor_lote_confirmar", args=[lote_b.pk]))
        self.assertEqual(response.status_code, 404)
        self.factura_b.refresh_from_db()
        self.assertFalse(self.factura_b.confirmado_pago)

    def test_provider_without_confirm_permission_cannot_confirm_lote(self):
        lote = PagoLote.objects.create(
            proveedor=self.proveedor,
            fecha_pago=date(2026, 2, 5),
            pagado_por="OFICINA",
            comprobante="comprobantes/lote-sin-permiso.pdf",
        )
        self.pago.lote = lote
        self.pago.save(update_fields=["lote"])
        self.client.force_login(self.portal_user_sin_permiso)
        response = self.client.post(reverse("portal_proveedor_lote_confirmar", args=[lote.pk]))
        self.assertEqual(response.status_code, 403)
        self.factura.refresh_from_db()
        self.assertFalse(self.factura.confirmado_pago)

    def test_payment_creation_generates_provider_notification(self):
        factura = Factura.objects.create(
            proveedor=self.proveedor,
            punto_venta=self.pv,
            numero_factura="NF-001",
            fecha_factura=date(2026, 3, 1),
            valor_factura=Decimal("120000.00"),
        )
        pago = crear_pago(factura=factura, valor_pagado=factura.valor_factura, pagado_por="OFICINA")
        self.assertTrue(
            NotificacionProveedor.objects.filter(
                usuario=self.portal_user,
                proveedor=self.proveedor,
                tipo=NotificacionProveedor.TIPO_PAGO_REGISTRADO,
                pago=pago,
            ).exists()
        )

    def test_payment_creation_does_not_notify_inactive_provider_user(self):
        factura = Factura.objects.create(
            proveedor=self.proveedor,
            punto_venta=self.pv,
            numero_factura="NF-INACTIVO",
            fecha_factura=date(2026, 3, 2),
            valor_factura=Decimal("130000.00"),
        )
        pago = crear_pago(factura=factura, valor_pagado=factura.valor_factura, pagado_por="OFICINA")
        self.assertFalse(
            NotificacionProveedor.objects.filter(
                usuario=self.portal_user_inactivo,
                proveedor=self.proveedor,
                tipo=NotificacionProveedor.TIPO_PAGO_REGISTRADO,
                pago=pago,
            ).exists()
        )

    def test_notification_bell_count_and_mark_read(self):
        notif = NotificacionProveedor.objects.create(
            usuario=self.portal_user,
            proveedor=self.proveedor,
            tipo=NotificacionProveedor.TIPO_SISTEMA,
            titulo="Prueba",
            mensaje="Mensaje",
            url_destino=reverse("portal_proveedor_dashboard"),
        )
        self.client.force_login(self.portal_user)
        response = self.client.get(reverse("portal_proveedor_dashboard"))
        self.assertContains(response, "Notificaciones <strong>1</strong>")
        response = self.client.get(reverse("portal_proveedor_notificacion_leer", args=[notif.pk]))
        self.assertEqual(response.status_code, 302)
        notif.refresh_from_db()
        self.assertFalse(notif.leida)
        response = self.client.post(reverse("portal_proveedor_notificacion_leer", args=[notif.pk]))
        self.assertEqual(response.status_code, 302)
        notif.refresh_from_db()
        self.assertTrue(notif.leida)

    def test_notification_external_url_is_not_redirected(self):
        notif = NotificacionProveedor.objects.create(
            usuario=self.portal_user,
            proveedor=self.proveedor,
            tipo=NotificacionProveedor.TIPO_SISTEMA,
            titulo="URL externa",
            mensaje="No debe redirigir fuera.",
            url_destino="https://example.com/phishing",
        )
        self.client.force_login(self.portal_user)
        response = self.client.post(reverse("portal_proveedor_notificacion_leer", args=[notif.pk]))
        self.assertEqual(response.status_code, 302)
        self.assertEqual(response["Location"], reverse("portal_proveedor_notificaciones"))
        notif.refresh_from_db()
        self.assertTrue(notif.leida)

    def test_provider_a_does_not_receive_provider_b_notification(self):
        NotificacionProveedor.objects.create(
            usuario=self.portal_user,
            proveedor=self.proveedor_b,
            tipo=NotificacionProveedor.TIPO_SISTEMA,
            titulo="Notificacion cruzada",
            mensaje="No debe verse",
        )
        self.client.force_login(self.portal_user)
        response = self.client.get(reverse("portal_proveedor_notificaciones"))
        self.assertEqual(response.status_code, 200)
        self.assertNotContains(response, "Notificacion cruzada")

    def test_provider_cannot_mark_other_user_notification_as_read(self):
        notif = NotificacionProveedor.objects.create(
            usuario=self.portal_user_sin_permiso,
            proveedor=self.proveedor,
            tipo=NotificacionProveedor.TIPO_SISTEMA,
            titulo="Privada",
            mensaje="No debe marcarse",
        )
        self.client.force_login(self.portal_user)
        response = self.client.post(reverse("portal_proveedor_notificacion_leer", args=[notif.pk]))
        self.assertEqual(response.status_code, 404)
        notif.refresh_from_db()
        self.assertFalse(notif.leida)

    def test_provider_reports_payment_novedad(self):
        self.client.force_login(self.portal_user)
        response = self.client.post(
            reverse("portal_proveedor_pago_novedad", args=[self.pago.pk]),
            {"motivo": "valor_no_coincide", "detalle": "El valor no coincide con mi extracto."},
        )
        self.assertEqual(response.status_code, 302)
        self.assertTrue(
            EventoAuditoria.objects.filter(
                tipo=EventoAuditoria.TIPO_NOVEDAD_PROVEEDOR,
                pago=self.pago,
                usuario=self.portal_user,
                metadata__motivo="valor_no_coincide",
            ).exists()
        )

    def test_provider_cannot_report_payment_novedad_for_other_provider(self):
        self.client.force_login(self.portal_user)
        response = self.client.post(
            reverse("portal_proveedor_pago_novedad", args=[self.pago_b.pk]),
            {"motivo": "valor_no_coincide", "detalle": "No debe registrarse."},
        )
        self.assertEqual(response.status_code, 404)
        self.assertFalse(EventoAuditoria.objects.filter(tipo=EventoAuditoria.TIPO_NOVEDAD_PROVEEDOR, pago=self.pago_b).exists())

    def test_provider_reports_lote_novedad(self):
        lote = PagoLote.objects.create(
            proveedor=self.proveedor,
            fecha_pago=date(2026, 2, 8),
            pagado_por="OFICINA",
            comprobante="comprobantes/lote-novedad.pdf",
        )
        self.pago.lote = lote
        self.pago.save(update_fields=["lote"])
        self.client.force_login(self.portal_user)
        response = self.client.post(
            reverse("portal_proveedor_lote_novedad", args=[lote.pk]),
            {"motivo": "comprobante_no_abre", "detalle": "No puedo abrir el comprobante."},
        )
        self.assertEqual(response.status_code, 302)
        self.assertTrue(EventoAuditoria.objects.filter(tipo=EventoAuditoria.TIPO_NOVEDAD_PROVEEDOR, lote=lote).exists())

    def test_provider_cannot_report_lote_novedad_for_other_provider(self):
        lote_b = PagoLote.objects.create(
            proveedor=self.proveedor_b,
            fecha_pago=date(2026, 2, 9),
            pagado_por="OFICINA",
            comprobante="comprobantes/lote-b-novedad.pdf",
        )
        self.pago_b.lote = lote_b
        self.pago_b.save(update_fields=["lote"])
        self.client.force_login(self.portal_user)
        response = self.client.post(
            reverse("portal_proveedor_lote_novedad", args=[lote_b.pk]),
            {"motivo": "comprobante_no_abre", "detalle": "No debe registrarse."},
        )
        self.assertEqual(response.status_code, 404)
        self.assertFalse(EventoAuditoria.objects.filter(tipo=EventoAuditoria.TIPO_NOVEDAD_PROVEEDOR, lote=lote_b).exists())

    def test_main_portal_pages_return_200(self):
        self.client.force_login(self.portal_user)
        for url in [
            reverse("portal_proveedor_dashboard"),
            reverse("portal_proveedor_facturas"),
            reverse("portal_proveedor_pagos"),
            reverse("portal_proveedor_novedades"),
            reverse("portal_proveedor_notificaciones"),
        ]:
            with self.subTest(url=url):
                response = self.client.get(url)
                self.assertEqual(response.status_code, 200)
