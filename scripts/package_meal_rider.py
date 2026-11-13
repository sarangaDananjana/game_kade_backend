"""Create the development handover without environment files or local build caches."""

import argparse
import hashlib
import json
import re
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
    parser.add_argument('--backend-tests', type=int, default=122)
    parser.add_argument('--native-tests', type=int, default=6)
    options = parser.parse_args()
    rider = options.rider_root.resolve()
    gradle = (rider / 'app/build.gradle.kts').read_text(encoding='utf-8')
    version = re.search(r'versionName\s*=\s*"([^"]+)"', gradle).group(1)
    version_code = int(re.search(r'versionCode\s*=\s*(\d+)', gradle).group(1))
    import xml.etree.ElementTree as ET
    unit_tests = sum(int(ET.parse(file).getroot().attrib['tests']) for file in (rider/'app/build/test-results/testDebugUnitTest').glob('TEST-*.xml'))
    backend_name = f'MealDrop-riders-backend-{version}.zip'
    source_name = f'MealRider-{version}-source.zip'
    apk_name = f'MealRider-{version}-debug.apk'
    output = BACKEND / 'handover'
    output.mkdir(exist_ok=True)
    required = [rider / 'DESIGN.md', rider / '.impeccable/design.json', rider / 'app/build/outputs/apk/debug/app-debug.apk']
    for source in required:
        if not source.is_file():
            raise RuntimeError(f'Finish the build and design documentation first: {source.name}')

    names = subprocess.check_output(['git', 'ls-files', '--cached', '--others', '--exclude-standard'], cwd=BACKEND, text=True).splitlines()
    backend_files = [Path(name) for name in names if name and not name.startswith('handover/')]
    counts = {backend_name: write_archive(output / backend_name, BACKEND, backend_files)}

    source_files = [Path(name) for name in [
        '.gitignore', 'build.gradle.kts', 'settings.gradle.kts', 'gradle.properties', 'gradlew', 'gradlew.bat',
        'app/build.gradle.kts', 'app/proguard-rules.pro', 'README.md', 'PRODUCT.md', 'DESIGN.md',
        '.impeccable/design.json', '.impeccable/direction-contract.md', '.impeccable/surfaces/meal-rider.md',
        '.impeccable/review/finish-review.md', '.impeccable/review/native-tests.txt',
    ]]
    for folder in ['gradle', 'app/src', 'docs', 'scripts', '.impeccable/review/verdict', '.impeccable/review/profile-tasks', '.impeccable/review/slides', '.impeccable/review/login', '.impeccable/review/wallet-routes']:
        source_files.extend(path.relative_to(rider) for path in (rider / folder).rglob('*') if path.is_file())
    counts[source_name] = write_archive(output / source_name, rider, source_files)
    shutil.copyfile(required[-1], output / apk_name)

    artifacts = []
    for name in [apk_name, backend_name, source_name, f'MealRider-{version}-preview.png', f'MealRider-{version}-handover.md']:
        file = output / name
        if file.is_file():
            artifacts.append({'file': name, 'bytes': file.stat().st_size, 'sha256': hashlib.sha256(file.read_bytes()).hexdigest(),
                              **({'source_files': counts[name]} if name in counts else {})})
    manifest = {'application': 'Meal Rider', 'version': version, 'version_code': version_code, 'build': 'debug',
                'created_at': datetime.now(timezone.utc).isoformat(), 'artifacts': artifacts,
                'validation': {'backend_tests': options.backend_tests, 'android_unit_tests': unit_tests, 'native_instrumentation_tests': options.native_tests,
                               'migration_drift': False},
                'deployment': 'Deploy and migrate the backend, and run the rider_dispatcher worker before live use.'}
    (output / f'MealRider-{version}-manifest.json').write_text(json.dumps(manifest, indent=2) + '\n', encoding='utf-8')
    print(json.dumps({'artifacts': [{'file': item['file'], 'bytes': item['bytes']} for item in artifacts], 'archive_files': counts}, indent=2))


if __name__ == '__main__':
    main()
