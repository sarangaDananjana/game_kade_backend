"""Remove retired source artifacts after copying an upgrade over an old tree."""

from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
OBSOLETE_FILES = [
    'multivender/test_delivery_pricing.py',
    'multivender/test_service_area.py',
    'static/img/logo.jpeg',
]
SOURCE_DIRECTORIES = ['food_delivery', 'users', 'vendors', 'products', 'orders', 'multivender', 'core']


def remove_file(path):
    resolved = path.resolve()
    if ROOT not in resolved.parents:
        raise RuntimeError('Refusing to remove a file outside the backend source tree.')
    if resolved.is_file():
        resolved.unlink()
        return 1
    return 0


def main():
    removed = sum(remove_file(ROOT / name) for name in OBSOLETE_FILES)
    for name in SOURCE_DIRECTORIES:
        directory = ROOT / name
        if directory.exists():
            for path in directory.rglob('*'):
                if path.is_file() and '__pycache__' in path.parts and path.suffix in {'.pyc', '.pyo'}:
                    removed += remove_file(path)
    print(f'Removed {removed} obsolete source/bytecode files.')


if __name__ == '__main__':
    main()
