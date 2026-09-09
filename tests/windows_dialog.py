"""CI-only native picker driver. Resolves controls inside the tested process; no coordinate clicks."""
import sys
from pywinauto import Application
pid=int(sys.argv[1]);action=sys.argv[2];value=sys.argv[3] if len(sys.argv)>3 else ''
app=Application(backend='uia').connect(process=pid)
if action=='close':
    window=app.window(title='TikTokVideoMaker')
    window.close()
else:
    dialog=app.window(class_name='#32770')
    dialog.wait('visible',timeout=20)
    if action=='cancel':dialog.child_window(auto_id='2',control_type='Button').invoke()
    else:
        edit=dialog.child_window(auto_id='1148',control_type='Edit')
        if not edit.exists(timeout=1):
            combo=dialog.child_window(auto_id='1148',control_type='ComboBox')
            edit=combo.child_window(control_type='Edit')
        edit.set_edit_text(value)
        dialog.child_window(auto_id='1',control_type='Button').invoke()
    dialog.wait_not('visible',timeout=20)
print('NATIVE_'+action.upper()+'_PASSED')
