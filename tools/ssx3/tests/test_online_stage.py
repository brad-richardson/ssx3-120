"""Android stage() must carry the online preset like the iOS preset does."""
import importlib.util
import json
import tempfile
import types
import unittest
from pathlib import Path
from unittest.mock import patch

BUILD = Path(__file__).resolve().parents[1] / 'build.py'
SPEC = importlib.util.spec_from_file_location('ssx3_build', BUILD)
build = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(build)


class OnlineStageTest(unittest.TestCase):
    def test_stage_appends_online_rows_and_keeps_stock120_off(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp) / 'root'
            presets = root / 'presets'
            presets.mkdir(parents=True)
            (presets / 'ps2x.env').write_text('PS2X_SOUND=1\n')
            (presets / 'full120.env').write_text('PS2X_SSX3_FULL120=events\n')
            (presets / 'online.env').write_text(
                '# comment\nPS2X_NET=1\nPS2X_NET_SERVER=ssx.bradr.dev\n'
                'PS2X_NET_QUICKPLAY=1\nPS2X_NET_QUICKPLAY_NAV=online\n')
            work = Path(temp) / 'work'
            (work / 'stage/files').mkdir(parents=True)
            (work / 'app-id.txt').write_text('com.ssx3.recomp\n')
            (work / 'inputs.json').write_text(json.dumps({}))
            (work / 'android-resources').mkdir(parents=True)
            args = types.SimpleNamespace(work=work, app_id='com.ssx3.recomp',
                                         step='android', serial=None)
            with patch.object(build, 'ROOT', root), \
                 patch.object(build, 'stage_fonts', return_value=None):
                files = build.stage(args)
            staged = (files / 'ps2x.env').read_text()
            for row in ('PS2X_NET=1', 'PS2X_NET_SERVER=ssx.bradr.dev',
                        'PS2X_NET_QUICKPLAY=1', 'PS2X_NET_QUICKPLAY_NAV=online'):
                self.assertIn(row, staged)
            self.assertNotIn('STOCK120', staged)


if __name__ == '__main__':
    unittest.main()
