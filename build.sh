#!/usr/bin/env bash
# abortar si hay un error
set -o errexit

# instalar dependencias
pip install -r requirements.txt

# recolectar estáticos
python manage.py collectstatic --no-input

# aplicar migraciones
python manage.py migrate
