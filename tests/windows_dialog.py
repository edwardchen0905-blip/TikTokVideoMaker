"""CI-only native picker driver. Resolves controls inside the tested process; no coordinate clicks."""
import sys
from pywinauto import Application
sys.stdout.reconfigure(encoding='utf-8')
sys.stderr.reconfigure(encoding='utf-8')
pid=int(sys.argv[1]);action=sys.argv[2];value=sys.argv[3] if len(sys.argv)>3 else ''
app=Application(backend='uia').connect(process=pid)
if action=='diagnose':
    from pathlib import Path
    import json
    import win32gui,win32process,win32con
    from pywinauto import Desktop
    from windows_acceptance import capture_processes
    directory=Path(value);directory.mkdir(parents=True,exist_ok=True)
    expected_edit='1152' if len(sys.argv)>4 and sys.argv[4]=='folder' else '1148' if len(sys.argv)>4 else None
    if expected_edit:
        # Readiness only: these edit IDs were observed in the actual Open/Select Folder dumps.
        app.window(title='TikTokVideoMaker').child_window(control_type='Edit',auto_id=expected_edit).wait('visible',timeout=20)
    process_evidence=capture_processes(pid,directory)
    owned={p['ProcessId'] for p in process_evidence['processes']}
    def window_info(hwnd):
        thread_id,process_id=win32process.GetWindowThreadProcessId(hwnd)
        return {'hwnd':hwnd,'class':win32gui.GetClassName(hwnd),'title':win32gui.GetWindowText(hwnd),
                'pid':process_id,'thread_id':thread_id,'owner_hwnd':win32gui.GetWindow(hwnd,win32con.GW_OWNER),
                'parent_hwnd':win32gui.GetAncestor(hwnd,1),'get_parent_raw':win32gui.GetParent(hwnd),
                'root_hwnd':win32gui.GetAncestor(hwnd,2),'visible':bool(win32gui.IsWindowVisible(hwnd)),
                'enabled':bool(win32gui.IsWindowEnabled(hwnd)),'rect':win32gui.GetWindowRect(hwnd)}
    top=[]
    def collect_top(hwnd,_):
        if win32process.GetWindowThreadProcessId(hwnd)[1] in owned:top.append(window_info(hwnd))
    win32gui.EnumWindows(collect_top,None)
    native={w['hwnd']:w for w in top}
    for info in top:
        win32gui.EnumChildWindows(info['hwnd'],lambda hwnd,_:native.update({hwnd:window_info(hwnd)}),None)
    # Win32 ancestry and UIA ancestry are different; retain both, including HWND=0 web nodes.
    (directory/'win32-windows.json').write_text(json.dumps(list(native.values()),ensure_ascii=False,indent=2),encoding='utf-8')
    errors=[];nodes=[]
    def walk(info,parent_index=None):
        index=len(nodes)
        node={'index':index,'parent_index':parent_index};nodes.append(node)
        try:
            node.update(hwnd=int(info.handle or 0),pid=info.process_id,name=info.name,class_name=info.class_name,
                        control_type=info.control_type,automation_id=info.automation_id,visible=info.visible,enabled=info.enabled)
            if info.handle and info.handle not in native:native[info.handle]=window_info(info.handle)
            # iter_children propagates COM errors; children() would log and return an empty list.
            for child in info.iter_children():walk(child,index)
        except Exception as error:node['capture_error']=repr(error);errors.append(repr(error))
    for info in top:
        try:
            window=Desktop(backend='uia').window(handle=info['hwnd']).wrapper_object()
            walk(window.element_info)
            if info['visible']:window.capture_as_image().save(directory/f"native-{info['hwnd']}.png")
        except Exception as error:errors.append(repr(error))
    for item in list(native.values()):
        for key in ('owner_hwnd','parent_hwnd'):
            handle=item[key]
            if handle and handle not in native:native[handle]=window_info(handle)
    (directory/'win32-windows.json').write_text(json.dumps(list(native.values()),ensure_ascii=False,indent=2),encoding='utf-8')
    (directory/'uia-tree.json').write_text(json.dumps(nodes,ensure_ascii=False,indent=2),encoding='utf-8')
    (directory/'capture.json').write_text(json.dumps({'pid':pid,'top_level_hwnds':[w['hwnd'] for w in top],
        'expected_edit':expected_edit,'errors':errors,'complete':bool(top) and not errors},ensure_ascii=False,indent=2),encoding='utf-8')
    if not top or errors:raise RuntimeError('Window evidence incomplete: '+repr(errors))
    print('Captured',len(native),'Win32 windows and',len(nodes),'UIA nodes')
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
