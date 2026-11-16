"""Startup and proof caches must respect an explicitly selected profile."""
import os
from pathlib import Path
import subprocess
import sys

def test_entrypoint_and_gui_imports_do_not_access_default_profile(tmp_path):
    script = '''
import os
import sys
default = os.path.expanduser('~/.electrum-sv')
def audit(event, args):
    if event in ('open', 'os.mkdir', 'os.listdir', 'os.scandir'):
        path = args[0]
        if isinstance(path, (str, bytes)):
            path = os.path.abspath(os.fsdecode(path))
            if path == default or path.startswith(default + os.sep):
                raise AssertionError('Touched default profile: ' + path)
sys.addaudithook(audit)
import electrumsv.main
import electrumsv.gui.qt.app_state
'''
    subprocess.run([sys.executable, '-c', script], check=True,
        cwd=Path(__file__).resolve().parents[2], env={**os.environ, 'QT_QPA_PLATFORM': 'offscreen'})


def test_verification_cache_and_headers_stay_in_selected_profile(tmp_path):
    # A fresh process prevents a prior test's module cache from choosing the profile.
    script = '''
import os
import sys
from electrumsv.app_state import AppStateProxy
from electrumsv.simple_config import SimpleConfig
config = SimpleConfig({'electrum_sv_path': sys.argv[1]})
AppStateProxy(config, 'qt')
from electrumsv import verification_utils as v
assert v.DATA_DIR == config.path
assert v.POSSIBLE_HEADER_PATHS == [os.path.join(config.path, 'headers')]
v.save_cache({'profile': 'test'}, v.MERKLE_CACHE_PATH)
assert v.load_cache(v.MERKLE_CACHE_PATH) == {'profile': 'test'}
assert os.path.commonpath([v.MERKLE_CACHE_PATH, config.path]) == config.path
assert v.get_headers_path() is None
'''
    subprocess.run([sys.executable, '-c', script, str(tmp_path)], check=True,
        cwd=Path(__file__).resolve().parents[2], env={**os.environ, 'QT_QPA_PLATFORM': 'offscreen'})
