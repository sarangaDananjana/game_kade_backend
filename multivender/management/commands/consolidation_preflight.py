import json

from django.core.management.base import BaseCommand
from django.db import connections

from multivender.migrations._consolidation import inspect_legacy_data


class Command(BaseCommand):
    help = 'Read-only inventory of obsolete single-shop data and the current multi-vendor records.'

    def add_arguments(self, parser):
        parser.add_argument('--database', default='default')

    def handle(self, *args, **options):
        plan = inspect_legacy_data(connections[options['database']])
        self.stdout.write(json.dumps(plan, indent=2))
        self.stdout.write('Migration deletes the old single-shop products, orders, and order items.')
        self.stdout.write('Saved addresses are consolidated; current multi-vendor records and map zones are retained.')
