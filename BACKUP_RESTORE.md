# Backup y restauracion

CarteraPro maneja datos sensibles de cartera, pagos, proveedores y comprobantes. El backup debe cubrir base de datos y archivos de comprobantes.

## Alcance

El backup de base de datos cubre facturas, pagos, proveedores, usuarios, auditoria, notificaciones y referencias a comprobantes. No cubre automaticamente archivos en S3 ni media local.

## Backup de base de datos

Ejemplo general:

```bash
pg_dump "$DATABASE_URL" --format=custom --no-owner --file cartera_YYYYMMDD_HHMM.dump
```

Buenas practicas:

- Guardar dumps fuera del servidor de produccion.
- Mantener retencion diaria, semanal y mensual.
- Cifrar backups si salen de una red controlada.
- Registrar fecha, tamano, hash y resultado.
- Probar restauracion periodicamente en un ambiente aislado.

## Backup de S3/media

Cuando `USE_S3_MEDIA=True`, los comprobantes viven en S3. Respaldar el bucket o el prefijo `media/`.

```bash
aws s3 sync s3://NOMBRE_BUCKET/media ./backup-media/media --only-show-errors
```

Buenas practicas:

- Activar versionado del bucket si es viable.
- Proteger el bucket contra borrado accidental.
- Verificar una muestra de comprobantes descargados.
- Documentar region, bucket y prefijo usado.

## Restauracion en ambiente aislado

Base de datos:

```bash
createdb cartera_restore
pg_restore --dbname cartera_restore --clean --if-exists cartera_YYYYMMDD_HHMM.dump
APP_ENV=local python manage.py migrate --noinput
APP_ENV=local python manage.py check
```

Media/S3:

```bash
aws s3 sync ./backup-media/media s3://NOMBRE_BUCKET/media --only-show-errors
```

Despues de restaurar:

- Verificar conteos de facturas, pagos, proveedores y usuarios.
- Confirmar que `Pago.comprobante` y `PagoLote.comprobante` apuntan a objetos existentes.
- Abrir comprobantes desde la app con un usuario autorizado.
- Ejecutar una prueba de correo con destinatario controlado.
- Probar confirmacion publica en staging: GET no muta, POST confirma.
- Probar portal proveedor: scoping, confirmaciones POST y novedades.

## Restauracion en produccion

No restaurar sobre produccion sin:

- Backup reciente confirmado.
- Ventana de mantenimiento.
- Aprobacion operativa.
- Plan de comunicacion.
- Verificacion de compatibilidad entre codigo desplegado y esquema de base de datos.

Comando base:

```bash
pg_restore --dbname "$DATABASE_URL" --clean --if-exists cartera_YYYYMMDD_HHMM.dump
```

Restaurar media si aplica:

```bash
aws s3 sync ./backup-media/media s3://NOMBRE_BUCKET/media --only-show-errors
```

## Prueba mensual de restauracion

Checklist minimo:

- Restaurar dump en base temporal.
- Configurar `.env` temporal sin secretos de produccion innecesarios.
- Ejecutar `APP_ENV=local python manage.py check`.
- Ejecutar `APP_ENV=test python manage.py test` contra SQLite local.
- Contar facturas, pagos, proveedores y eventos de auditoria.
- Verificar al menos tres comprobantes en S3.
- Probar confirmacion publica en staging.
- Registrar resultado, fecha y responsable.

## Comandos utiles

```bash
APP_ENV=local python manage.py check
APP_ENV=local python manage.py makemigrations --check --dry-run
APP_ENV=local python manage.py migrate
APP_ENV=local python manage.py collectstatic --noinput
APP_ENV=test python manage.py test
python -m pip check
```

## Archivos locales que no deben versionarse

- `.env`
- `.env.postgres`
- `db.sqlite3`
- `media/`
- `__pycache__/`
- `.venv/`
- dumps, backups y archivos `.zip`
