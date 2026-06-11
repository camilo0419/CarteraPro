# Despliegue seguro en Render

Guia operativa para desplegar CarteraPro en Render sin incluir secretos reales. No ejecutes pruebas contra la base de datos de produccion: usa `APP_ENV=test` para la suite local y staging para validaciones de negocio.

## Variables requeridas en Render

- `APP_ENV=production`
- `DEBUG=False`
- `REQUIRE_PRODUCTION_SETTINGS=True`
- `SECRET_KEY`
- `DATABASE_URL`
- `ALLOWED_HOSTS`
- `CSRF_TRUSTED_ORIGINS`
- `SITE_URL`
- `USE_S3_MEDIA=True`
- `AWS_STORAGE_BUCKET_NAME`
- `AWS_ACCESS_KEY_ID`
- `AWS_SECRET_ACCESS_KEY`
- `AWS_S3_REGION_NAME`
- `EMAIL_HOST_PASSWORD`

Variables opcionales habituales:

- `EMAIL_HOST`
- `EMAIL_PORT`
- `EMAIL_USE_TLS`
- `EMAIL_HOST_USER`
- `EMAIL_FROM_NAME`
- `AWS_S3_ENDPOINT_URL`
- `AWS_QUERYSTRING_EXPIRE`
- `COMPROBANTE_MAX_UPLOAD_SIZE`
- `SECURE_HSTS_SECONDS`

## Revision actual de migraciones

Migraciones nuevas revisadas:

- `cartera/migrations/0008_eventoauditoria.py`: crea la tabla `EventoAuditoria` con relaciones opcionales a factura, pago, lote y usuario.
- `cartera/migrations/0009_alter_eventoauditoria_tipo_notificacionproveedor_and_more.py`: amplia choices de auditoria y crea `NotificacionProveedor` y `ProveedorUsuario`.

No hay operaciones de borrado de tablas, renombrado destructivo ni data migrations. Aun asi, ejecutar `migrate` en produccion exige backup reciente verificado.

## Validacion local antes de staging

```bash
APP_ENV=test python manage.py test
python manage.py check
python manage.py makemigrations --check --dry-run
python manage.py collectstatic --dry-run --noinput
python -m pip check
git diff --check
```

## Backup obligatorio antes de migrar

Base de datos:

```bash
pg_dump "$DATABASE_URL" --format=custom --no-owner --file cartera_YYYYMMDD_HHMM.dump
```

Media/S3:

```bash
aws s3 sync s3://NOMBRE_BUCKET/media ./backup-media/media --only-show-errors
```

Verifica que el dump tenga tamano esperado y que al menos una muestra de comprobantes exista en el backup de media.

## Build command

El repositorio incluye `build.sh`:

```bash
pip install -r requirements.txt
APP_ENV=production python manage.py migrate --noinput
APP_ENV=production python manage.py collectstatic --no-input
```

Para un despliegue mas controlado, separar `migrate` del build y ejecutarlo como paso manual despues del backup.

## Start command

```bash
gunicorn carterapro.wsgi:application
```

## Secuencia recomendada en staging

```bash
APP_ENV=production python manage.py check
APP_ENV=production python manage.py makemigrations --check --dry-run
APP_ENV=production python manage.py migrate --noinput
APP_ENV=production python manage.py collectstatic --noinput
```

Validar:

- Login interno y portal de proveedores.
- Facturas y pagos visibles segun scoping.
- Confirmacion publica: GET muestra pantalla, POST confirma.
- Confirmacion desde portal: pago y lote siguen usando POST.
- PagoLote sigue siendo monoproveedor.
- Comprobantes se sirven desde S3 con permisos.
- Correo de prueba a destinatario controlado.
- Novedades no se pueden reportar sobre pago o lote ya confirmado.

## Secuencia recomendada en produccion

1. Confirmar ventana de mantenimiento o bajo trafico.
2. Generar backup de base de datos y media.
3. Verificar variables de Render sin exponer valores.
4. Desplegar el build aprobado.
5. Ejecutar migraciones si no corren en build:

```bash
APP_ENV=production python manage.py migrate --noinput
```

6. Ejecutar validaciones postdeploy:

```bash
APP_ENV=production python manage.py check
APP_ENV=production python manage.py collectstatic --dry-run --noinput
```

7. Revisar logs de Render sin copiar secretos.

## Rollback

Si el deploy falla antes de migrar:

- Revertir al release anterior desde Render.
- Verificar `APP_ENV=production python manage.py check`.

Si el deploy falla despues de migrar:

- No ejecutar comandos destructivos de forma impulsiva.
- Revisar si el release anterior es compatible con las migraciones aplicadas.
- Si se requiere restaurar, usar el dump tomado antes del deploy en una ventana controlada:

```bash
pg_restore --dbname "$DATABASE_URL" --clean --if-exists cartera_YYYYMMDD_HHMM.dump
```

- Restaurar media si el incidente afecto comprobantes:

```bash
aws s3 sync ./backup-media/media s3://NOMBRE_BUCKET/media --only-show-errors
```

## Archivos que no deben subirse

- `.env`
- `.env.postgres`
- `db.sqlite3`
- `media/`
- `__pycache__/`
- `.venv/`
- backups locales, dumps y archivos `.zip`

## Notas de seguridad

- `DATABASE_URL` mantiene prioridad sobre `POSTGRES_*` y `DB_*`.
- `APP_ENV=test` fuerza SQLite en memoria para tests.
- S3 debe permanecer privado y con URLs firmadas.
- `REQUIRE_PRODUCTION_SETTINGS=True` debe permanecer activo en Render.
- Rotar credenciales si aparecen en logs, tickets o capturas.
