"""The resolution patch's tables and helpers, loaded out of vo_patch.py
for the tools. Import as: import vo_patch_hires as hires."""
import importlib.util
import os
import sys

_here = os.path.dirname(os.path.abspath(__file__))
os.environ.setdefault('VO_PATCH_BOOTSTRAP', '1')
_spec = importlib.util.spec_from_file_location(
    'vo_patch', os.path.join(os.path.dirname(_here), 'vo_patch.py'))
_vp = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_vp)
sys.modules[__name__].__dict__.update(
    {k: v for k, v in _vp.__dict__.items() if not k.startswith('__')})
