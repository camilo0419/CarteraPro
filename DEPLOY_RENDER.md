# Despliegue en Render

Guía operativa para desplegar CarteraPro en Render sin incluir secretos reales.

## Variables de entorno requeridas

- `SECRET_KEY`: valor largo, aleatorio y privado.
- `APP_ENV=production`
- `DEBUG=False`
- `REQUIRE_PRODUCTION_SETTINGS=True`
- `DATABASE_URL`: URL interna/externa de PostgreSQL en Render.
- `ALLOWED_HOSTS`: dominios separados por coma. Ejemplo: `.onrender.com,fogonylena.com,www.fogonylena.com`.
- `CSRF_TRUSTED_ORIGINS`: orígenes HTTPS separados por coma. Ejemplo: `https://*.onrender.com,https://fogonylena.com,https://www.fogonylena.com`.
- `SITE_URL`: URL pública principal, sin slash final.
- `USE_S3_MEDIA=True`
- `AWS_STORAGE_BUCKET_NAME`
- `AWS_ACCESS_KEY_ID`
- `AWS_SECRET_ACCESS_KEY`
- `AWS_S3_REGION_NAME`
- `EMAIL_HOST_PASSWORD`

## Variables opcionales útiles

- `EMAIL_HOST`: por defecto `smtp.office365.com`.
- `EMAIL_PORT`: por defecto `587`.
- `EMAIL_USE_TLS`: por defecto `True`.
- `EMAIL_HOST_USER`: por defecto `cartera@fogonylena.net`.
- `EMAIL_FROM_NAME`
- `AWS_S3_ENDPOINT_URL`: solo si se usa un endpoint compatible con S3 distinto de AWS.
- `AWS_QUERYSTRING_EXPIRE`: duración de URLs firmadas, por defecto `3600`.
- `COMPROBANTE_MAX_UPLOAD_SIZE`: tamaño máximo en bytes, por defecto `10485760`.
- `SECURE_HSTS_SECONDS`: por defecto `31536000` cuando `DEBUG=False`.

## Build command

El repositorio incluye `build.sh`:

```bash
pip install -r requirements.txt
APP_ENV=production python manage.py migrate --noinput
APP_ENV=production python manage.py collectstatic --no-input
```

Antes de usarlo en producción, valida migraciones en staging. Si el proyecto crece, conviene separar `migrate` del build y ejecutarlo como paso controlado.

## Start command

Sugerido:

```bash
gunicorn carterapro.wsgi:application
```

## Checklist antes de deploy

```bash
APP_ENV=production python manage.py check
APP_ENV=production python manage.py makemigrations --check --dry-run
APP_ENV=test python manage.py test
APP_ENV=production python manage.py collectstatic --dry-run --noinput
python -m pip check
```

## Checklist después de deploy

- Abrir login y validar que `DEBUG=False`.
- Crear o revisar una factura de prueba en staging.
- Confirmar que static carga correctamente.
- Subir un comprobante PDF/imagen pequeño.
- Confirmar que la URL del comprobante viene de S3 y no del filesystem local.
- Enviar correo de prueba a un proveedor controlado.
- Abrir el enlace de confirmación: GET debe mostrar botón, POST debe confirmar.
- Revisar logs de Render sin exponer secretos.

## Notas de seguridad

- No subir `.env`, `db.sqlite3` ni `media/` al repositorio.
- Mantener S3 privado y con URLs firmadas.
- Rotar credenciales si alguna vez se copian en logs, tickets o capturas.
- Mantener `REQUIRE_PRODUCTION_SETTINGS=True` en Render para que falten rápido variables críticas.
