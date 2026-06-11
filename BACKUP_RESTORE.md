# Backup y restauración

CarteraPro maneja datos sensibles de cartera, pagos, proveedores y comprobantes. El backup debe cubrir base de datos y archivos media.

## Alcance del backup actual

Existe un proceso externo en Windows que ejecuta `pg_dump` diario contra PostgreSQL. Ese backup cubre la base de datos, incluyendo facturas, pagos, usuarios, logs y referencias a comprobantes.

Importante: `pg_dump` no respalda archivos en S3 ni media local. Los comprobantes deben respaldarse aparte.

## Backup recomendado de base de datos

Ejemplo general:

```bash
pg_dump "$DATABASE_URL" --format=custom --no-owner --file cartera_YYYYMMDD.dump
```

Buenas prácticas:

- Guardar dumps fuera del servidor de producción.
- Mantener retención diaria/semanal/mensual.
- Cifrar los respaldos si salen de una red controlada.
- Registrar fecha, tamaño, hash y resultado del comando.

## Backup recomendado de S3/media

Los comprobantes viven en S3 cuando `USE_S3_MEDIA=True`. Respaldar el bucket o al menos el prefijo `media/`.

Ejemplo con AWS CLI:

```bash
aws s3 sync s3://NOMBRE_BUCKET/media ./backup-media/media --only-show-errors
```

Buenas prácticas:

- Activar versionado del bucket si es viable.
- Proteger el bucket contra borrado accidental.
- Probar descarga de una muestra de comprobantes.
- Documentar región, bucket y prefijo usado.

## Restauración de base de datos

En un ambiente aislado:

```bash
createdb cartera_restore
pg_restore --dbname cartera_restore --clean --if-exists cartera_YYYYMMDD.dump
APP_ENV=local python manage.py migrate --noinput
APP_ENV=local python manage.py check
```

No restaurar sobre producción sin ventana de mantenimiento y backup reciente confirmado.

## Restauración de media/S3

Ejemplo:

```bash
aws s3 sync ./backup-media/media s3://NOMBRE_BUCKET/media --only-show-errors
```

Después de restaurar:

- Verificar que las rutas guardadas en `Pago.comprobante` y `PagoLote.comprobante` existen en S3.
- Abrir comprobantes desde la app con un usuario autorizado.
- Enviar correo de prueba con adjunto.

## Prueba mensual de restauración

Checklist mínimo:

- Restaurar dump en base temporal.
- Configurar `.env` temporal sin secretos de producción innecesarios.
- Ejecutar `APP_ENV=local python manage.py check`.
- Contar facturas, pagos y proveedores.
- Verificar al menos tres comprobantes en S3.
- Probar confirmación pública en staging.
- Registrar resultado y responsable.

## Comandos útiles

```bash
APP_ENV=local python manage.py check
APP_ENV=local python manage.py makemigrations --check --dry-run
APP_ENV=local python manage.py migrate
APP_ENV=local python manage.py collectstatic --noinput
APP_ENV=local python manage.py createsuperuser
APP_ENV=test python manage.py test
python -m pip check
```
