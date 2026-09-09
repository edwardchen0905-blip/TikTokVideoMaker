"""CI-only native picker driver. Resolves controls inside the tested process; no coordinate clicks."""
import sys
from pywinauto import Application
sys.stdout.reconfigure(encoding='utf-8')
sys.stderr.reconfigure(encoding='utf-8')
pid=int(sys.argv[1]);action=sys.argv[2];value=sys.argv[3] if len(sys.argv)>3 else ''
app=Application(backend='uia').connect(process=pid)
if action=='diagnose':
    from pathlib import Path
    directory=Path(value);directory.mkdir(parents=True,exist_ok=True)
    for n,window in enumerate(app.windows()):
        print(window.window_text(),window.class_name())
        window.capture_as_image().save(directory/f'native-window-{n}.png')
        for child in window.descendants():print(child.element_info.control_type,child.window_text(),child.element_info.automation_id)
elif action=='close':
    window=app.window(title='TikTokVideoMaker')
    window.close()
else:
    # Captured UIA tree places the native picker beneath the owned WinForms window.
    dialog=app.window(title='TikTokVideoMaker').child_window(control_type='Window')
    dialog.wait('visible',timeout=20)
    if action=='cancel':dialog.child_window(auto_id='2',control_type='Button').invoke()
    else:
        # Actual captured dialogs: Open uses 1148; Select Folder uses 1152.
        edit=dialog.child_window(auto_id='1152' if action=='folder' else '1148',control_type='Edit')
        edit.set_edit_text(value)
        dialog.child_window(auto_id='1',control_type='Button').invoke()
    dialog.wait_not('visible',timeout=20)
print('NATIVE_'+action.upper()+'_PASSED')
