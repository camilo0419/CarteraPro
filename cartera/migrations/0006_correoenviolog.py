# Generated manually for CarteraPro hardening update

import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('cartera', '0005_alter_pago_comprobante_pagolote_pago_lote'),
    ]

    operations = [
        migrations.CreateModel(
            name='CorreoEnvioLog',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('tipo', models.CharField(choices=[('individual', 'Individual'), ('lote', 'Lote')], max_length=20)),
                ('enviado_a', models.EmailField(blank=True, max_length=254)),
                ('asunto', models.CharField(blank=True, max_length=255)),
                ('exito', models.BooleanField(default=False)),
                ('detalle', models.TextField(blank=True)),
                ('creado_en', models.DateTimeField(auto_now_add=True)),
                ('factura', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.CASCADE, related_name='logs_correo', to='cartera.factura')),
                ('lote', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='logs_correo', to='cartera.pagolote')),
                ('pago', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='logs_correo', to='cartera.pago')),
            ],
            options={
                'verbose_name': 'Log de envío de correo',
                'verbose_name_plural': 'Logs de envío de correo',
                'ordering': ['-creado_en', '-id'],
            },
        ),
        migrations.AddIndex(
            model_name='correoenviolog',
            index=models.Index(fields=['tipo', 'creado_en'], name='cartera_cor_tipo_0d329d_idx'),
        ),
        migrations.AddIndex(
            model_name='correoenviolog',
            index=models.Index(fields=['factura', 'creado_en'], name='cartera_cor_factur_0df4e8_idx'),
        ),
        migrations.AddIndex(
            model_name='correoenviolog',
            index=models.Index(fields=['lote', 'creado_en'], name='cartera_cor_lote_4dff6b_idx'),
        ),
    ]
