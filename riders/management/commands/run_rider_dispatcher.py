import time

from django.core.management.base import BaseCommand
from django.db import close_old_connections, DatabaseError

from riders.services import dispatch_due


class Command(BaseCommand):
    help = 'Expire rider offers and hand waiting sessions to the next available rider.'

    def add_arguments(self, parser):
        parser.add_argument('--once', action='store_true')
        parser.add_argument('--interval', type=float, default=2.0)

    def handle(self, *args, **options):
        while True:
            close_old_connections()
            try:
                changed = dispatch_due()
            except DatabaseError as exc:
                self.stderr.write(f'Dispatch will retry after a database error ({type(exc).__name__}).')
                changed = 0
            if changed:
                self.stdout.write(f'Updated {changed} rider offers.')
            if options['once']:
                return
            time.sleep(max(0.5, options['interval']))
