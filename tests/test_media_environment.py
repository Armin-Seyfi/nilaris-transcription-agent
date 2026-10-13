import os
import subprocess
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))
from media_environment import configure_media_path


class MediaEnvironmentTests(unittest.TestCase):
    def test_existing_priority_preserved_and_configuration_idempotent(self):
        env = {'PATH': '/custom/bin:/usr/bin:/bin'}
        with patch('media_environment.Path.is_dir', return_value=True):
            first = configure_media_path(env)
            self.assertEqual(first, '/custom/bin:/usr/bin:/bin:/opt/homebrew/bin:/usr/local/bin')
            self.assertEqual(configure_media_path(env), first)

    def test_missing_locations_not_added(self):
        env = {'PATH': '/usr/bin:/bin'}
        with patch('media_environment.Path.is_dir', return_value=False):
            self.assertEqual(configure_media_path(env), '/usr/bin:/bin')

    @unittest.skipUnless(Path('/opt/homebrew/bin/ffprobe').is_file(), 'Requires installed Homebrew tools')
    def test_gui_path_finds_installed_tools_in_child_process(self):
        env = dict(os.environ, PATH='/usr/bin:/bin:/usr/sbin:/sbin')
        configure_media_path(env)
        for name in ('ffprobe', 'ffmpeg'):
            result = subprocess.run([name, '-version'], env=env, capture_output=True, text=True, check=True)
            self.assertIn(name + ' version', result.stdout)


if __name__ == '__main__':
    unittest.main()
