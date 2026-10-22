import json
import sys
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch

from cast_to_tv import __version__
from cast_to_tv.about import get_app_info
from cast_to_tv.build_info import write_build_info


class AppInfoTests(unittest.TestCase):
    def test_source_run_ignores_packaging_metadata(self):
        with patch.object(sys, 'frozen', False, create=True), \
                patch('cast_to_tv.about._BUILD_INFO_PATH') as metadata:
            info = get_app_info()
        metadata.read_text.assert_not_called()
        self.assertEqual(info.version, __version__)
        self.assertEqual(info.fields(), [('Version', __version__), ('Build date', 'Not packaged')])
        self.assertEqual(info.as_text(), f'Cast to TV\nVersion: {__version__}\nBuild date: Not packaged')

    def test_packaging_stamp_round_trips(self):
        with tempfile.TemporaryDirectory() as directory:
            metadata = Path(directory) / 'build_info.json'
            before = datetime.now(timezone.utc).replace(microsecond=0)
            write_build_info(metadata)
            after = datetime.now(timezone.utc)
            data = json.loads(metadata.read_text(encoding='utf-8'))
            timestamp = datetime.fromisoformat(data['built_at_utc'])
            self.assertLessEqual(before, timestamp)
            self.assertLessEqual(timestamp, after)
            with patch.object(sys, 'frozen', True, create=True), \
                    patch('cast_to_tv.about._BUILD_INFO_PATH', metadata):
                info = get_app_info()
            self.assertEqual(info.version, data['version'])
            self.assertEqual(info.build_date, timestamp.strftime('%Y-%m-%d %H:%M:%S UTC'))
            self.assertEqual([label for label, _ in info.fields()], ['Version', 'Build date'])

    def test_missing_corrupt_or_stale_metadata_does_not_claim_a_build_date(self):
        with tempfile.TemporaryDirectory() as directory:
            metadata = Path(directory) / 'build_info.json'
            for contents in (None, 'bad json', '[]', '{"version": "other"}',
                             json.dumps({'version': __version__, 'built_at_utc': '2026-10-19T12:00:00'}),
                             json.dumps({'version': __version__, 'built_at_utc': None})):
                with self.subTest(contents=contents):
                    if contents is not None:
                        metadata.write_text(contents, encoding='utf-8')
                    with patch.object(sys, 'frozen', True, create=True), \
                            patch('cast_to_tv.about._BUILD_INFO_PATH', metadata), \
                            self.assertLogs('cast_to_tv.about', level='WARNING'):
                        info = get_app_info()
                    self.assertEqual(info.build_date, 'Unavailable')
                    self.assertEqual(info.version, __version__)


if __name__ == '__main__':
    unittest.main()
