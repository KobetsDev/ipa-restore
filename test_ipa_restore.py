import hashlib
import io
import json
from pathlib import Path
import plistlib
import subprocess
import tempfile
import unittest
from unittest.mock import patch
import zipfile

import ipa_restore as app


def make_ipa(path, app_id=12345, bundle='org.example.app', version='1.2', metadata=True):
    with zipfile.ZipFile(path, 'w') as archive:
        archive.writestr('Payload/Example.app/Info.plist', plistlib.dumps({
            'CFBundleIdentifier': bundle, 'CFBundleDisplayName': 'Example',
            'CFBundleShortVersionString': version, 'MinimumOSVersion': '15.0'}))
        if metadata:
            archive.writestr('iTunesMetadata.plist', plistlib.dumps({
                'itemId': app_id, 'softwareVersionBundleId': bundle,
                'AppleID': 'private-field-that-must-not-be-reported'}))


class TargetTests(unittest.TestCase):
    def test_numeric_and_store_link(self):
        for value in ('12345', ' https://apps.apple.com/ru/app/example/id12345?mt=8 '):
            self.assertEqual(app.parse_target(value), ('id', '12345'))

    def test_reject_untrusted_urls_and_shell_input(self):
        for value in ('0', '-1', '1;rm -rf .', '9223372036854775808',
                      'https://apps.apple.com.evil.test/id123',
                      'https://evil.test/id123', 'http://apps.apple.com/id123',
                      'https://user:secret@apps.apple.com/id123',
                      'https://apps.apple.com:444/id123', 'https://apps.apple.com:bad/id123'):
            with self.subTest(value=value), self.assertRaises(app.RestoreError):
                app.parse_target(value)

    @patch('ipa_restore.store_query')
    def test_numeric_id_needs_no_lookup(self, lookup):
        self.assertEqual(app.resolve_app_id('12345'), '12345')
        lookup.assert_not_called()

    @patch('ipa_restore.store_query', return_value={'results': []})
    def test_missing_bundle_gives_actionable_error(self, _):
        with self.assertRaisesRegex(app.RestoreError, 'numeric'):
            app.resolve_app_id('org.example.app')

    @patch('ipa_restore.store_query', return_value={'results': [
        {'bundleId': 'org.example.app', 'trackId': 12345}]})
    def test_bundle_resolution(self, _):
        self.assertEqual(app.resolve_app_id('org.example.app', 'ru'), '12345')


class PackageTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.path = Path(self.tmp.name) / 'example.ipa'
        make_ipa(self.path)

    def test_identity_hash_and_no_account_details(self):
        report = app.inspect_ipa(self.path, '12345')
        self.assertEqual(report['bundle_id'], 'org.example.app')
        self.assertEqual(report['sha256'], hashlib.sha256(self.path.read_bytes()).hexdigest())
        self.assertNotIn('private-field', json.dumps(report))

    def test_wrong_app_id_rejected(self):
        with self.assertRaisesRegex(app.RestoreError, 'App Store ID'):
            app.inspect_ipa(self.path, '99999')

    def test_missing_metadata_cannot_validate_a_download(self):
        make_ipa(self.path, metadata=False)
        with self.assertRaises(app.RestoreError):
            app.inspect_ipa(self.path, '12345')
        self.assertEqual(app.inspect_ipa(self.path)['bundle_id'], 'org.example.app')

    def test_conflicting_bundle_id(self):
        with zipfile.ZipFile(self.path, 'a') as archive:
            # Write a new archive below to avoid duplicate ZIP member semantics.
            info = archive.read('Payload/Example.app/Info.plist')
        with zipfile.ZipFile(self.path, 'w') as archive:
            archive.writestr('Payload/Example.app/Info.plist', info)
            archive.writestr('iTunesMetadata.plist', plistlib.dumps({
                'itemId': 12345, 'softwareVersionBundleId': 'org.wrong.app'}))
        with self.assertRaisesRegex(app.RestoreError, 'disagrees'):
            app.inspect_ipa(self.path)

    def test_multiple_main_apps_rejected(self):
        with zipfile.ZipFile(self.path, 'a') as archive:
            archive.writestr('Payload/Other.app/Info.plist', b'not a plist')
        with self.assertRaisesRegex(app.RestoreError, 'exactly one'):
            app.inspect_ipa(self.path)

    def test_crc_corruption_is_rejected(self):
        data = self.path.read_bytes()
        self.path.write_bytes(data.replace(b'<string>Example</string>', b'<string>Changed</string>', 1))
        with self.assertRaises(zipfile.BadZipFile):
            app.inspect_ipa(self.path)

    @patch('ipa_restore.subprocess.run')
    def test_cached_wrong_app_never_installed_or_downloaded(self, run):
        with self.assertRaises(app.RestoreError):
            app.download_app('99999', self.path, '/fake/ipatool')
        run.assert_not_called()

    @patch('ipa_restore.subprocess.run')
    def test_valid_cache_does_not_contact_apple(self, run):
        self.assertEqual(app.download_app('12345', self.path, '/fake/ipatool')['version'], '1.2')
        run.assert_not_called()

    @patch('ipa_restore.stream_download')
    def test_no_purchase_flag_and_explicit_ios_platform(self, run):
        self.path.unlink()
        def download(command, env):
            make_ipa(self.path)
            return 0, ''
        run.side_effect = download
        app.download_app('12345', self.path, '/fake/ipatool')
        command = run.call_args.args[0]
        self.assertNotIn('--purchase', command)
        self.assertIn('--non-interactive', command)
        self.assertIn('iphone', command)

    @patch('ipa_restore.stream_download', return_value=(1, 'license is required'))
    def test_license_errors_are_not_retried(self, run):
        self.path.unlink()
        with self.assertRaises(app.RestoreError):
            app.download_app('12345', self.path, '/fake/ipatool', retries=3)
        self.assertEqual(run.call_count, 1)

    @patch('ipa_restore.time.sleep')
    @patch('ipa_restore.stream_download', return_value=(1, 'Timeout was reached'))
    def test_network_retries_are_bounded(self, run, _):
        self.path.unlink()
        with self.assertRaises(app.RestoreError):
            app.download_app('12345', self.path, '/fake/ipatool', retries=1)
        self.assertEqual(run.call_count, 2)


class DeviceTests(unittest.TestCase):
    @patch('ipa_restore.ios_json', return_value={'deviceList': ['phone-1']})
    def test_single_device(self, _):
        self.assertEqual(app.select_device('/fake/ios'), 'phone-1')

    @patch('ipa_restore.ios_json', return_value={'deviceList': ['phone-1', 'phone-2']})
    def test_multiple_devices_need_selection(self, _):
        with self.assertRaises(app.RestoreError):
            app.select_device('/fake/ios')
        self.assertEqual(app.select_device('/fake/ios', 'phone-2'), 'phone-2')

    @patch('ipa_restore.ios_json', return_value={'deviceList': []})
    def test_disconnected_device(self, _):
        with self.assertRaises(app.RestoreError):
            app.select_device('/fake/ios', 'phone-1')

    @patch('ipa_restore.inspect_ipa', return_value={'ipa': '/fake/app.ipa',
        'bundle_id': 'org.example.app', 'version': '1.2'})
    @patch('ipa_restore.select_device', return_value='phone-1')
    @patch('ipa_restore.subprocess.run', return_value=subprocess.CompletedProcess([], 0))
    @patch('ipa_restore.ios_json')
    def test_install_is_verified_not_just_attempted(self, query, run, _, __):
        for item in ({'CFBundleIdentifier': 'org.example.app', 'CFBundleShortVersionString': '1.1'},
                     {'CFBundleIdentifier': 'org.example.app', 'CFBundleShortVersionString': '1.2', 'IsDemotedApp': True}):
            query.return_value = [item]
            with self.assertRaises(app.RestoreError):
                app.install_app(Path('/fake/app.ipa'), '/fake/ios')
        query.return_value = [{'CFBundleIdentifier': 'org.example.app',
            'CFBundleShortVersionString': '1.2', 'IsDemotedApp': False}]
        self.assertTrue(app.install_app(Path('/fake/app.ipa'), '/fake/ios')['installed'])
        for call in run.call_args_list:
            self.assertEqual(call.args[0][1], 'install')
            self.assertNotIn('uninstall', call.args[0])


class SetupTests(unittest.TestCase):
    @patch('ipa_restore.platform.machine', return_value='arm64')
    @patch('ipa_restore.platform.system', return_value='Darwin')
    @patch('ipa_restore.fetch', return_value=b'tampered release')
    def test_checksum_failure_writes_no_executable(self, *_):
        with tempfile.TemporaryDirectory() as directory:
            with self.assertRaisesRegex(app.RestoreError, 'checksum'):
                app.setup(Path(directory))
            self.assertEqual(list((Path(directory) / 'bin').iterdir()), [])


class RuntimeTests(unittest.TestCase):
    @patch('ipa_restore.platform.machine', return_value='arm64')
    @patch('ipa_restore.platform.system', return_value='Darwin')
    def test_verified_library_needs_no_network_and_links_project_cache(self, *_):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            binary = b'verified-test-runtime'
            digest = hashlib.sha256(binary).hexdigest()
            cache = root / 'project/cache/unicorn/2.1.4' / digest
            cache.mkdir(parents=True)
            library = cache / 'libunicorn.2.dylib'
            library.write_bytes(binary)
            with patch.dict(app.UNICORN, {'arm64': ('package', 'tag', 'archive-hash', digest)}), \
                    patch('ipa_restore.Path.home', return_value=root / 'user'), \
                    patch('ipa_restore.subprocess.run') as run:
                report = app.prepare_runtime(root / 'project')
                run.assert_not_called()
                self.assertTrue(report['unicorn_ready'])
                link = root / 'user/Library/Caches/ipatool/unicorn/2.1.4' / digest / library.name
                self.assertTrue(link.is_symlink())
                self.assertEqual(link.resolve(), library.resolve())

    @patch('ipa_restore.platform.machine', return_value='arm64')
    @patch('ipa_restore.platform.system', return_value='Darwin')
    def test_bad_wheel_rejected_before_cache_link(self, *_):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            digest = app.UNICORN['arm64'][3]
            cache = root / 'project/cache/unicorn/2.1.4' / digest
            cache.mkdir(parents=True)
            (cache / 'unicorn.whl.part').write_bytes(b'tampered')
            with patch('ipa_restore.Path.home', return_value=root / 'user'), \
                    patch('ipa_restore.subprocess.run', return_value=subprocess.CompletedProcess([], 0)):
                with self.assertRaisesRegex(app.RestoreError, 'checksum'):
                    app.prepare_runtime(root / 'project')
            self.assertFalse((root / 'user').exists())
            self.assertFalse((cache / 'libunicorn.2.dylib').exists())


if __name__ == '__main__':
    unittest.main()
