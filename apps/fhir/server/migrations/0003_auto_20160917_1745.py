# -*- coding: utf-8 -*-
# Generated by Django 1.9.7 on 2016-09-17 17:45
from __future__ import unicode_literals

from django.db import migrations


class Migration(migrations.Migration):

    dependencies = [
        ('server', '0002_auto_20160917_1523'),
    ]

    operations = [
        migrations.RenameField(
            model_name='resourcerouter',
            old_name='resource_name',
            new_name='supported_resource',
        ),
    ]