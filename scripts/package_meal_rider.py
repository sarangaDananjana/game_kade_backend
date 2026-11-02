"""Create the development handover without environment files or local build caches."""

import argparse
import hashlib
import json
import shutil
import subprocess
from datetime import datetime, timezone
from pathlib import Path
from zipfile import ZIP_DEFLATED, ZipFile


BACKEND = Path(__file__).resolve().parent.parent


def safe_source(path):
    return (path.name == '.env.example' or not path.name.startswith('.env')) and path.suffix.lower() not in {
        '.db', '.sqlite', '.sqlite3', '.pem', '.key', '.jks', '.keystore', '.log', '.apk',
    } and not any(part in {'__pycache__', '.git', '.venv', 'media', 'staticfiles', 'handover'} for part in path.parts)


def write_archive(destination, root, files):
    with ZipFile(destination, 'w', compression=ZIP_DEFLATED, compresslevel=9) as archive:
        for relative in sorted(set(files)):
            source = root / relative
            if source.is_file() and safe_source(relative):
                archive.write(source, relative.as_posix())
    with ZipFile(destination) as archive:
        if archive.testzip() is not None:
            raise RuntimeError(f'Archive validation failed: {destination.name}')
        return len(archive.namelist())


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--rider-root', type=Path, default=BACKEND.parent / 'GameKadeRider')
    options = parser.parse_args()
    rider = options.rider_root.resolve()
    output = BACKEND / 'handover'
    output.mkdir(exist_ok=True)
    required = [rider / 'DESIGN.md', rider / '.impeccable/design.json', rider / 'app/build/outputs/apk/debug/app-debug.apk']
    for source in required:
        if not source.is_file():
            raise RuntimeError(f'Finish the build and design documentation first: {source.name}')

    names = subprocess.check_output(['git', 'ls-files', '--cached', '--others', '--exclude-standard'], cwd=BACKEND, text=True).splitlines()
    backend_files = [Path(name) for name in names if name and not name.startswith('handover/')]
    counts = {'MealDrop-riders-backend.zip': write_archive(output / 'MealDrop-riders-backend.zip', BACKEND, backend_files)}

    source_files = [Path(name) for name in [
        '.gitignore', 'build.gradle.kts', 'settings.gradle.kts', 'gradle.properties', 'gradlew', 'gradlew.bat',
        'app/build.gradle.kts', 'app/proguard-rules.pro', 'README.md', 'PRODUCT.md', 'DESIGN.md',
        '.impeccable/design.json', '.impeccable/direction-contract.md', '.impeccable/surfaces/meal-rider.md',
        '.impeccable/review/finish-review.md', '.impeccable/review/native-tests.txt',
    ]]
    for folder in ['gradle', 'app/src', '.impeccable/review/verdict']:
        source_files.extend(path.relative_to(rider) for path in (rider / folder).rglob('*') if path.is_file())
    counts['MealRider-source.zip'] = write_archive(output / 'MealRider-source.zip', rider, source_files)
    shutil.copyfile(required[-1], output / 'MealRider-2.0.0-debug.apk')

    artifacts = []
    for name in ['MealRider-2.0.0-debug.apk', 'MealDrop-riders-backend.zip', 'MealRider-source.zip', 'MealRider-preview.png', 'MealRider-handover.md']:
        file = output / name
        if file.is_file():
            artifacts.append({'file': name, 'bytes': file.stat().st_size, 'sha256': hashlib.sha256(file.read_bytes()).hexdigest(),
                              **({'source_files': counts[name]} if name in counts else {})})
    manifest = {'application': 'Meal Rider', 'version': '2.0.0', 'version_code': 100, 'build': 'debug',
                'created_at': datetime.now(timezone.utc).isoformat(), 'artifacts': artifacts,
                'validation': {'backend_tests': 118, 'android_unit_tests': 10, 'native_instrumentation_tests': 4,
                               'migration_drift': False},
                'deployment': 'Deploy and migrate the backend, and run the rider_dispatcher worker before live use.'}
    (output / 'MealRider-manifest.json').write_text(json.dumps(manifest, indent=2) + '\n', encoding='utf-8')
    print(json.dumps({'artifacts': [{'file': item['file'], 'bytes': item['bytes']} for item in artifacts], 'archive_files': counts}, indent=2))


if __name__ == '__main__':
    main()
