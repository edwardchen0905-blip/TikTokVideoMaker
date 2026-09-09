"""Linux UI-test fixture. Native Windows dialogs are tested separately on the packaged EXE."""
import json
from pathlib import Path
import shutil
import sys
import tempfile
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from app.desktop import DesktopService
from core.workspace import Workspace
with tempfile.TemporaryDirectory(prefix='tvm_ui_') as tmp:
    root=Path(tmp);source=root/'sources';source.mkdir()
    repo=Path(__file__).resolve().parents[1]
    for p in (repo/'examples/input').iterdir():shutil.copy2(p,source/p.name)
    workspace=Workspace(root/'data')
    imported=workspace.import_assets(list(source.iterdir()))
    assert not imported['errors'], imported
    service=DesktopService(workspace);service.start()
    print(json.dumps({'url':service.url,'output':str(root/'external output')}),flush=True)
    try:sys.stdin.readline()
    finally:service.close()
