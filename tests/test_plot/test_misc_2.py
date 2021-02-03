import os
import sys
import pytest
import coverage
import logging
from subprocess import CalledProcessError
# Look for the 'utils' module from where the script is running
prev_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if prev_dir not in sys.path:
    sys.path.insert(0, prev_dir)
# Utils import
from utils import context
prev_dir = os.path.dirname(prev_dir)
if prev_dir not in sys.path:
    sys.path.insert(0, prev_dir)
from kibot.out_base import BaseOutput
from kibot.pre_base import BasePreFlight
from kibot.gs import GS
from kibot.kiplot import load_actions, _import
from kibot.registrable import RegOutput, RegFilter
from kibot.misc import (MISSING_TOOL, WRONG_INSTALL, BOM_ERROR)


cov = coverage.Coverage()
mocked_check_output_FNF = True
mocked_check_output_retOK = ''


# Important note:
# - We can't load the plug-ins twice, the import fails.
# - Once we patched them using monkey patch the patch isn't reverted unless we load them again.
# For this reason this patch is used for more than one case.
def mocked_check_output(cmd, stderr=None):
    logging.debug('mocked_check_output called')
    if mocked_check_output_FNF:
        raise FileNotFoundError()
    else:
        if mocked_check_output_retOK:
            return mocked_check_output_retOK
        e = CalledProcessError(10, 'rar')
        e.output = b'THE_ERROR'
        raise e


def run_compress(ctx, test_import_fail=False):
    # Start coverage
    cov.load()
    cov.start()
    # Load the plug-ins
    load_actions()
    # Create a compress object with the dummy file as source
    out = RegOutput.get_class_for('compress')()
    out.set_tree({'options': {'format': 'RAR', 'files': [{'source': ctx.get_out_path('*')}]}})
    out.config()
    # Setup the GS output dir, needed for the output path
    GS.out_dir = '.'
    # Run the compression and catch the error
    with pytest.raises(SystemExit) as pytest_wrapped_e:
        if test_import_fail:
            _import('out_bogus', os.path.abspath(os.path.join(os.path.dirname(__file__), 'fake_plugin/out_bogus.py')))
        else:
            out.run('')
    # Stop coverage
    cov.stop()
    cov.save()
    return pytest_wrapped_e


def test_no_rar(test_dir, caplog, monkeypatch):
    global mocked_check_output_FNF
    mocked_check_output_FNF = True
    # Create a silly context to get the output path
    ctx = context.TestContext(test_dir, 'test_no_rar', 'test_v5', 'empty_zip', '')
    # The file we pretend to compress
    ctx.create_dummy_out_file('Test.txt')
    # We will patch subprocess.check_output to make rar fail
    with monkeypatch.context() as m:
        m.setattr("subprocess.check_output", mocked_check_output)
        pytest_wrapped_e = run_compress(ctx)
    # Check we exited because rar isn't installed
    assert pytest_wrapped_e.type == SystemExit
    assert pytest_wrapped_e.value.code == MISSING_TOOL
    assert "Missing `rar` command" in caplog.text


def test_rar_fail(test_dir, caplog, monkeypatch):
    global mocked_check_output_FNF
    mocked_check_output_FNF = False
    # Create a silly context to get the output path
    ctx = context.TestContext(test_dir, 'test_no_rar', 'test_v5', 'empty_zip', '')
    # The file we pretend to compress
    ctx.create_dummy_out_file('Test.txt')
    # We will patch subprocess.check_output to make rar fail
    with monkeypatch.context() as m:
        m.setattr("subprocess.check_output", mocked_check_output)
        pytest_wrapped_e = run_compress(ctx)
        pytest_wrapped_e2 = run_compress(ctx, test_import_fail=True)
    # Check we exited because rar isn't installed
    assert pytest_wrapped_e.type == SystemExit
    assert pytest_wrapped_e.value.code == WRONG_INSTALL
    assert "Failed to invoke rar command, error 10" in caplog.text
    # Not in the docker image ... pytest issue?
    # assert "THE_ERROR" in caplog.text
    # Check we exited because the import failed
    assert pytest_wrapped_e2.type == SystemExit
    assert pytest_wrapped_e2.value.code == WRONG_INSTALL
    assert "Unable to import plug-ins:" in caplog.text


class NoGetTargets(BaseOutput):
    def __init__(self):
        self.options = True
        self.comment = 'Fake'
        self.name = 'dummy'
        self.type = 'none'
        self._sch_related = True


class DummyPre(BasePreFlight):
    def __init__(self):
        super().__init__('dummy', True)
        self._sch_related = True


def test_no_get_targets(caplog):
    test = NoGetTargets()
    test_pre = DummyPre()
    # Also check the dependencies fallback
    GS.sch = None
    GS.sch_file = 'fake'
    # Start coverage
    cov.load()
    cov.start()
    test.get_targets('')
    files = test.get_dependencies()
    files_pre = test_pre.get_dependencies()
    # Stop coverage
    cov.stop()
    cov.save()
    assert "Output 'Fake' (dummy) [none] doesn't implement get_targets(), plese report it" in caplog.text
    assert files == [GS.sch_file]
    assert files_pre == [GS.sch_file]


def test_ibom_parse_fail(test_dir, caplog, monkeypatch):
    global mocked_check_output_FNF
    mocked_check_output_FNF = False
    global mocked_check_output_retOK
    mocked_check_output_retOK = b'ERROR Parsing failed'
    # We will patch subprocess.check_output to make ibom fail
    with monkeypatch.context() as m:
        m.setattr("subprocess.check_output", mocked_check_output)
        # Start coverage
        cov.load()
        cov.start()
        # Load the plug-ins
        load_actions()
        # Create an ibom object
        out = RegOutput.get_class_for('ibom')()
        out.set_tree({})
        out.config()
        # Setup the GS output dir, needed for the output path
        #GS.out_dir = '.'
        with pytest.raises(SystemExit) as pytest_wrapped_e:
            out.run('')
        # Stop coverage
        cov.stop()
        cov.save()
    assert pytest_wrapped_e.type == SystemExit
    assert pytest_wrapped_e.value.code == BOM_ERROR
    # logging.debug(caplog.text)
    assert "Failed to create BoM" in caplog.text
    mocked_check_output_retOK = ''


def test_var_rename_no_variant():
    # Start coverage
    cov.load()
    cov.start()
    # Load the plug-ins
    load_actions()
    # Create an ibom object
    filter = RegFilter.get_class_for('var_rename')()
    GS.variant = None
    # Should just return
    filter.filter(None)
    # Stop coverage
    cov.stop()
    cov.save()
