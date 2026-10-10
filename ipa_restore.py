#!/usr/bin/env python3
"""Download owned App Store apps and restore them to a USB-connected iPhone."""
from __future__ import annotations

import argparse
import hashlib
import io
import json
import os
from pathlib import Path
import platform
import plistlib
import re
import shutil
import ssl
import subprocess
import sys
import tarfile
import time
import urllib.error
import urllib.parse
import urllib.request
import zipfile

VERSION = '0.1.1'
DEFAULT_HOME = Path(__file__).resolve().parent / '.ipa-restore'
RELEASES = {
    'arm64': ('ipatool-2.7.0-macos-arm64.tar.gz',
              '5ae100d4520cf4b3f4374239e5925a9ad9a8dc02abc16bae27479e7ae99aca33'),
    'x86_64': ('ipatool-2.7.0-macos-amd64.tar.gz',
               '05f48994d8b3cfefe7ea1172a0ac0b28715888e9df0af38cf3e490d2ed592263'),
}
IOS_SHA256 = '100f225bfdd039081bcbdaf45029df3e67032673f3e0ca1fa3c050898c230c57'
UNICORN = {
    'arm64': ('6c/ae/4943c6f8524d729ec7d5e69df6407ea05d710fe77471d91cecf3fc64eb57',
              'macosx_11_0_arm64',
              'd6c93e0f60328d8f4a1792af3f834137a28050fcc2305f2ec01efe8558a9844e',
              '7207c8e3d7a63118fb0bca73e01816797fd51b1d8a39a4cbc7abfd562ee59c85'),
    'x86_64': ('c8/a7/92b47771e2107a201632a199cec91e8a81ee8a071ca6b7e7d600d8c61ac9',
               'macosx_10_9_x86_64',
               '2a6f738fab5fabffa56af1e7bbf16ea1e91466c342f8dc64f125bd70f36c6b80',
               '51c4a6f3ce22628ecd3acd1c49b921a818ffb989ca2c473134cc7eb06094f256'),
}


class RestoreError(Exception):
    """An actionable error suitable for display without a traceback."""


def fetch(url: str, timeout: int = 60) -> bytes:
    """HTTPS with certificate verification; use macOS curl for its system CA store."""
    request = urllib.request.Request(url, headers={'User-Agent': f'ipa-restore/{VERSION}'})
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return response.read()
    except urllib.error.URLError as error:
        if isinstance(error.reason, ssl.SSLCertVerificationError) and platform.system() == 'Darwin':
            result = subprocess.run(['/usr/bin/curl', '--fail', '--silent', '--show-error',
                                     '--location', '--max-time', str(timeout), url],
                                    capture_output=True, timeout=timeout + 5)
            if result.returncode == 0:
                return result.stdout
            raise RestoreError(f'HTTPS download failed: {result.stderr.decode(errors="replace").strip()}') from error
        raise RestoreError(f'HTTPS request failed for {urllib.parse.urlsplit(url).hostname}: {error}') from error


def store_query(endpoint: str, **params) -> dict:
    return json.loads(fetch('https://itunes.apple.com/' + endpoint + '?' +
                            urllib.parse.urlencode(params)))


def parse_target(value: str) -> tuple[str, str]:
    """Return ('id', digits) or ('bundle', identifier); never execute input."""
    value = value.strip()
    if re.fullmatch(r'[1-9][0-9]{0,18}', value) and int(value) <= 2**63 - 1:
        return 'id', value
    if value.startswith('https://'):
        url = urllib.parse.urlsplit(value)
        try:
            allowed_port = url.port in (None, 443)
        except ValueError:
            allowed_port = False
        if (url.hostname == 'apps.apple.com' and not url.username and
                not url.password and allowed_port):
            match = re.search(r'/id([1-9][0-9]{0,18})(?:/|$)', url.path)
            if match:
                return parse_target(match.group(1))
        raise RestoreError('Expected an https://apps.apple.com/…/id123456789 link.')
    if re.fullmatch(r'[A-Za-z0-9-]+(?:\.[A-Za-z0-9-]+)+', value):
        return 'bundle', value
    raise RestoreError('Use a numeric App Store ID, an App Store link, or a bundle ID.')


def resolve_app_id(target: str, country: str = 'us') -> str:
    kind, value = parse_target(target)
    if kind == 'id':
        return value  # Numeric IDs can still work when public lookup returns no result.
    apps = store_query('lookup', bundleId=value, country=country).get('results', [])
    matches = [a for a in apps if a.get('bundleId') == value and a.get('trackId')]
    if len(matches) != 1:
        raise RestoreError('Bundle ID not found in this storefront. Try its numeric App Store ID.')
    return str(matches[0]['trackId'])


def public_app(app: dict) -> dict:
    return {key: app.get(key) for key in
            ('trackId', 'trackName', 'bundleId', 'version', 'minimumOsVersion', 'trackViewUrl')}


def prepare_runtime(home: Path) -> dict:
    """Preload ipatool 2.7.0's pinned runtime before asking for credentials."""
    if platform.system() != 'Darwin' or platform.machine() not in UNICORN:
        raise RestoreError('Runtime preparation supports macOS arm64/x86_64.')
    package, tag, archive_hash, library_hash = UNICORN[platform.machine()]
    directory = home / 'cache' / 'unicorn' / '2.1.4' / library_hash
    directory.mkdir(parents=True, mode=0o700, exist_ok=True)
    library = directory / 'libunicorn.2.dylib'
    if not library.is_file() or hashlib.sha256(library.read_bytes()).hexdigest() != library_hash:
        archive = directory / 'unicorn.whl.part'
        url = f'https://files.pythonhosted.org/packages/{package}/unicorn-2.1.4-cp37-abi3-{tag}.whl'
        print('Preparing Unicorn 2.1.4 (verified official PyPI wheel; download can resume)…', file=sys.stderr)
        command = ['/usr/bin/curl', '--fail', '--show-error', '--location',
                   '--connect-timeout', '30', '--max-time', '600', '--retry', '2',
                   '--retry-delay', '2', '--continue-at', '-', '--output', str(archive), url]
        result = subprocess.run(command)
        if result.returncode:
            raise RestoreError('Unicorn download failed. Run prepare-runtime again to resume; Apple login has not started.')
        if hashlib.sha256(archive.read_bytes()).hexdigest() != archive_hash:
            archive.unlink()
            raise RestoreError('Unicorn archive checksum mismatch; nothing loaded. Run prepare-runtime again.')
        with zipfile.ZipFile(archive) as wheel:
            binary = wheel.read('unicorn/lib/libunicorn.2.dylib')
        if hashlib.sha256(binary).hexdigest() != library_hash:
            raise RestoreError('Unicorn library checksum mismatch; nothing loaded.')
        temporary = library.with_suffix('.new')
        temporary.write_bytes(binary)
        temporary.chmod(0o600)
        temporary.replace(library)
        archive.unlink()
    # Go's os.UserCacheDir ignores XDG_CACHE_HOME on macOS. Keep the actual
    # dependency in the project and expose only a symlink in ipatool's cache.
    upstream = Path.home() / 'Library' / 'Caches' / 'ipatool' / 'unicorn' / '2.1.4' / library_hash
    upstream.mkdir(parents=True, mode=0o700, exist_ok=True)
    destination = upstream / library.name
    if destination.is_file() and hashlib.sha256(destination.read_bytes()).hexdigest() == library_hash:
        return {'unicorn_ready': True, 'library': str(destination)}
    if destination.exists() or destination.is_symlink():
        raise RestoreError(f'Unicorn cache contains an invalid file: {destination}. Inspect it before retrying.')
    destination.symlink_to(library.resolve())
    return {'unicorn_ready': True, 'library': str(library.resolve())}


def setup(home: Path) -> dict:
    if platform.system() != 'Darwin' or platform.machine() not in RELEASES:
        raise RestoreError('Automatic setup supports macOS arm64/x86_64. Supply your own tool paths elsewhere.')
    asset, digest = RELEASES[platform.machine()]
    downloads = [
        ('ipatool', f'https://github.com/majd/ipatool/releases/download/v2.7.0/{asset}', digest),
        ('ios', 'https://github.com/danielpaulus/go-ios/releases/download/v1.3.2/go-ios-mac.zip', IOS_SHA256),
    ]
    tools = home / 'bin'
    tools.mkdir(parents=True, exist_ok=True)
    for name, url, expected in downloads:
        print(f'Downloading {name} from its official GitHub release…', file=sys.stderr)
        data = fetch(url, timeout=300)
        if hashlib.sha256(data).hexdigest() != expected:
            raise RestoreError(f'{name}: release checksum mismatch; nothing executed.')
        if url.endswith('.zip'):
            with zipfile.ZipFile(io.BytesIO(data)) as archive:
                matches = [n for n in archive.namelist() if Path(n).name == name and not n.endswith('/')]
                if len(matches) != 1:
                    raise RestoreError(f'{name}: ambiguous binary in archive.')
                binary = archive.read(matches[0])
        else:
            with tarfile.open(fileobj=io.BytesIO(data), mode='r:gz') as archive:
                matches = [m for m in archive.getmembers()
                           if m.isfile() and (Path(m.name).name == name or
                                             Path(m.name).name.startswith(name + '-'))]
                if len(matches) != 1:
                    raise RestoreError(f'{name}: ambiguous binary in archive.')
                binary = archive.extractfile(matches[0]).read()
        temporary = tools / (name + '.new')
        temporary.write_bytes(binary)
        temporary.chmod(0o700)
        temporary.replace(tools / name)
    return {'ipatool': str(tools / 'ipatool'), 'ios': str(tools / 'ios')}


def tool_path(name: str, home: Path, override: str | None = None) -> str:
    candidate = override or str(home / 'bin' / name)
    if not override and not Path(candidate).is_file():
        candidate = shutil.which(name) or candidate
    if not Path(candidate).is_file() or not os.access(candidate, os.X_OK):
        raise RestoreError(f'{name} not found. Run setup or pass --{name} /path/to/{name}.')
    return str(Path(candidate).resolve())


def apple_environment(home: Path, bmrng: bool = False) -> dict:
    env = os.environ.copy()
    state = home / 'state'
    state.mkdir(parents=True, mode=0o700, exist_ok=True)
    env['XDG_STATE_HOME'] = str(state.resolve())
    env['XDG_DATA_HOME'] = str((home / 'data').resolve())
    env['XDG_CACHE_HOME'] = str((home / 'cache').resolve())
    if bmrng:
        if platform.system() != 'Darwin':
            raise RestoreError('--bmrng requires macOS and an existing Bmrng login.')
        state = home / 'bmrng-state'
        state.mkdir(mode=0o700, exist_ok=True)
        source = Path.home() / 'Library/Application Support/bmrng/appstore-cpp-v1'
        for name in ('account', 'cookies'):
            if not (state / name).exists() and (source / name).is_file():
                shutil.copyfile(source / name, state / name)
                (state / name).chmod(0o600)
        if not (state / 'account').is_file():
            raise RestoreError('No saved Bmrng Apple session. Log into Bmrng first or use ordinary login.')
        result = subprocess.run(['/usr/bin/security', 'find-generic-password', '-s',
                                 'app.bmrng.ipatool-cpp.v1', '-a', 'storage-key', '-w'],
                                capture_output=True, timeout=120)
        if result.returncode:
            raise RestoreError('Cannot read Bmrng session key. Allow the macOS Keychain prompt.')
        env['IPATOOL_CONFIG_DIR'] = str(state.resolve())
        env['IPATOOL_PASSPHRASE'] = result.stdout.decode().rstrip('\r\n')
    return env


def inspect_ipa(path: Path, expected_id: str | None = None) -> dict:
    """Read the app manifest, verify store identity when downloading, and hash it."""
    with zipfile.ZipFile(path) as archive:
        manifests = [n for n in archive.namelist() if
                     re.fullmatch(r'Payload/[^/]+\.app/Info\.plist', n)]
        if len(manifests) != 1:
            raise RestoreError('IPA must contain exactly one main application manifest.')
        info = plistlib.loads(archive.read(manifests[0]))
        if not isinstance(info, dict):
            raise RestoreError('IPA application manifest must be a dictionary.')
        bundle = info.get('CFBundleIdentifier')
        if not isinstance(bundle, str) or parse_target(bundle)[0] != 'bundle':
            raise RestoreError('IPA has no valid bundle identifier.')
        metadata = (plistlib.loads(archive.read('iTunesMetadata.plist'))
                    if 'iTunesMetadata.plist' in archive.namelist() else {})
        if not isinstance(metadata, dict):
            raise RestoreError('IPA iTunes metadata must be a dictionary.')
        if expected_id and str(metadata.get('itemId')) != str(expected_id):
            raise RestoreError('Downloaded IPA does not match the requested App Store ID.')
        if metadata.get('softwareVersionBundleId', bundle) != bundle:
            raise RestoreError('IPA bundle identifier disagrees with iTunes metadata.')
        bad = archive.testzip()
        if bad:
            raise RestoreError(f'Corrupt IPA member: {bad}')
    digest = hashlib.sha256()
    with path.open('rb') as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b''):
            digest.update(chunk)
    return dict(app_id=metadata.get('itemId'), bundle_id=bundle,
                name=info.get('CFBundleDisplayName', info.get('CFBundleName')),
                version=info.get('CFBundleShortVersionString'),
                minimum_ios=info.get('MinimumOSVersion'), bytes=path.stat().st_size,
                sha256=digest.hexdigest(), ipa=str(path.resolve()))


def stream_download(command: list[str], env: dict | None) -> tuple[int, str]:
    process = subprocess.Popen(command, env=env, stdout=subprocess.PIPE,
                               stderr=subprocess.STDOUT, text=True, errors='replace')
    tail = ''
    try:
        for line in process.stdout:
            sys.stderr.write(line)
            tail = (tail + line)[-8192:]
        return process.wait(), tail
    except BaseException:
        process.terminate()
        try:
            process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait()
        raise
    finally:
        process.stdout.close()


def download_app(app_id: str, output: Path, ipatool: str, env: dict | None = None,
                 retries: int = 2, version_id: str | None = None,
                 bmrng: bool = False) -> dict:
    kind, app_id = parse_target(str(app_id))
    if kind != 'id':
        raise RestoreError('download_app requires a numeric App Store ID.')
    output = Path(output).resolve()
    if output.exists():
        report = inspect_ipa(output, app_id)
        if version_id:
            raise RestoreError('A cached IPA exists. Use a new --output path for a specific version.')
        return report
    output.parent.mkdir(parents=True, exist_ok=True)
    command = [ipatool, 'download', '--app-id', app_id, '--output', str(output), '--format', 'json']
    if not bmrng:
        command += ['--non-interactive', '--platform', 'iphone']
    if version_id:
        command += ['--external-version-id', version_id]
    for attempt in range(retries + 1):
        # Progress belongs on stderr; reserve our stdout for the final JSON report.
        code, diagnostics = stream_download(command, env)
        if code == 0:
            return inspect_ipa(output, app_id)
        if output.exists():
            raise RestoreError('Downloader failed after producing a final file; inspect it before retrying.')
        transient = re.search(r'timeout|timed out|connection reset|connection refused|'
                              r'temporary failure|unexpected EOF|TLS handshake|'
                              r'could not resolve|network is unreachable', diagnostics, re.I)
        if attempt < retries and transient:
            print(f'Download failed; retry {attempt + 1}/{retries} (no new license requested).', file=sys.stderr)
            time.sleep(2)
        else:
            break
    raise RestoreError('Download failed. Check login, account ownership and storefront; a delisted app may be unavailable.')


def ios_json(ios: str, *args: str):
    result = subprocess.run([ios, *args], capture_output=True, text=True, timeout=60)
    if result.returncode:
        raise RestoreError(result.stderr.strip() or 'Cannot access iPhone. Unlock it and trust this computer.')
    return json.loads(result.stdout)


def select_device(ios: str, udid: str | None = None) -> str:
    devices = ios_json(ios, 'list').get('deviceList', [])
    if udid:
        if udid not in devices:
            raise RestoreError('The selected iPhone is not connected. Run devices.')
        return udid
    if len(devices) != 1:
        raise RestoreError('Connect exactly one iPhone, or choose it with --udid (see devices).')
    return devices[0]


def install_app(ipa: Path, ios: str, udid: str | None = None) -> dict:
    report = inspect_ipa(Path(ipa))
    device = select_device(ios, udid)
    result = subprocess.run([ios, 'install', '--path=' + report['ipa'], '--udid=' + device],
                            stdout=sys.stderr, stderr=sys.stderr)
    if result.returncode:
        raise RestoreError('Installation failed. Keep the IPA; no uninstall was performed.')
    apps = ios_json(ios, 'apps', '--all', '--udid=' + device)
    matches = [a for a in apps if a.get('CFBundleIdentifier') == report['bundle_id']]
    if (len(matches) != 1 or matches[0].get('IsDemotedApp', False) or
            matches[0].get('CFBundleShortVersionString') != report['version']):
        raise RestoreError('Installer returned success, but the installed app/version could not be verified.')
    return dict(report, installed=True)


def parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--version', action='version', version=VERSION)
    p.add_argument('--home', type=Path, default=Path(os.getenv('IPA_RESTORE_HOME', str(DEFAULT_HOME))),
                   help='local tools, downloads and private session directory')
    p.add_argument('--ipatool', help='override downloader executable')
    p.add_argument('--ios', help='override go-ios executable')
    p.add_argument('--bmrng', action='store_true', help='explicitly reuse an existing Bmrng Apple login (macOS)')
    sub = p.add_subparsers(dest='command', required=True)
    sub.add_parser('setup', help='install pinned, checksum-verified tools locally (macOS)')
    sub.add_parser('login', help='interactive Apple Account login through ipatool')
    sub.add_parser('prepare-runtime', help='download and verify the login runtime with resumable transfers')
    sub.add_parser('devices', help='list USB-connected iPhone identifiers')
    search = sub.add_parser('search', help='find App Store IDs by name; no Apple login needed')
    search.add_argument('query')
    search.add_argument('--country', default='us', type=country)
    search.add_argument('--limit', type=int, choices=range(1, 51), default=10, metavar='1..50')
    lookup = sub.add_parser('lookup', help='read public metadata for an ID, link or bundle ID')
    lookup.add_argument('target')
    lookup.add_argument('--country', default='us', type=country)
    restore = sub.add_parser('restore', help='download an owned app and install it over USB')
    restore.add_argument('target')
    restore.add_argument('--country', default='us', type=country)
    restore.add_argument('--download-only', action='store_true')
    restore.add_argument('--udid')
    restore.add_argument('--output', type=Path)
    restore.add_argument('--external-version-id')
    restore.add_argument('--retries', type=int, choices=range(0, 4), default=2)
    install = sub.add_parser('install', help='install and verify an already downloaded IPA')
    install.add_argument('ipa', type=Path)
    install.add_argument('--udid')
    inspect = sub.add_parser('inspect', help='validate an IPA and print non-personal metadata')
    inspect.add_argument('ipa', type=Path)
    return p


def country(value: str) -> str:
    if not re.fullmatch('[A-Za-z]{2}', value):
        raise argparse.ArgumentTypeError('Use a two-letter storefront code, e.g. us or ru.')
    return value.lower()


def main(argv=None) -> int:
    args = parser().parse_args(argv)
    os.umask(0o077)
    home = args.home.expanduser().resolve()
    try:
        if args.command == 'setup':
            result = setup(home)
        elif args.command == 'prepare-runtime':
            result = prepare_runtime(home)
        elif args.command == 'search':
            result = [public_app(a) for a in store_query('search', term=args.query,
                       country=args.country, entity='software', limit=args.limit).get('results', [])]
        elif args.command == 'lookup':
            kind, value = parse_target(args.target)
            result = [public_app(a) for a in store_query('lookup', country=args.country,
                       **{'id' if kind == 'id' else 'bundleId': value}).get('results', [])]
        elif args.command == 'inspect':
            result = inspect_ipa(args.ipa)
        elif args.command in ('devices', 'install'):
            ios = tool_path('ios', home, args.ios)
            result = (ios_json(ios, 'list') if args.command == 'devices' else
                      install_app(args.ipa, ios, args.udid))
        else:
            if args.bmrng and args.command == 'login':
                raise RestoreError('For --bmrng, log into the Bmrng application itself.')
            if args.bmrng and not args.ipatool:
                arch = {'arm64': 'arm64', 'x86_64': 'amd64'}.get(platform.machine(), '')
                args.ipatool = f'/Applications/bmrng.app/Contents/Resources/tools/ipatool-cpp-1.0.9-bmrng.1-darwin-{arch}'
            ipatool = tool_path('ipatool', home, args.ipatool)
            if args.command == 'login':
                if not args.ipatool:
                    prepare_runtime(home)
                env = apple_environment(home)
                completed = subprocess.run([ipatool, 'auth', 'login'], env=env)
                if completed.returncode:
                    raise RestoreError('Apple login failed; see ipatool error above. Session state is kept for the next attempt.')
                result = {'login_completed': True}
            else:
                app_id = resolve_app_id(args.target, args.country)
                output = args.output or home / 'downloads' / f'app-{app_id}.ipa'
                # Select the phone before contacting Apple; do not guess among devices.
                ios = None if args.download_only else tool_path('ios', home, args.ios)
                device = None if ios is None else select_device(ios, args.udid)
                env = None if output.exists() else apple_environment(home, args.bmrng)
                result = download_app(app_id, output, ipatool, env, args.retries,
                                      args.external_version_id, args.bmrng)
                if ios:
                    result = install_app(output, ios, device)
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0
    except (RestoreError, OSError, subprocess.SubprocessError, ValueError,
            KeyError, zipfile.BadZipFile, plistlib.InvalidFileException, tarfile.TarError) as error:
        print(f'Error: {error}', file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        print('Cancelled. Partial download files are kept for the downloader to resume.', file=sys.stderr)
        return 130


if __name__ == '__main__':
    sys.exit(main())
