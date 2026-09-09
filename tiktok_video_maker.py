from app.desktop import main

if __name__ == '__main__':
    try:
        main()
    except Exception as error:
        import os
        import logging
        logging.exception('Desktop startup failed')
        if os.name=='nt':
            import ctypes
            ctypes.windll.user32.MessageBoxW(None,str(error),'TikTokVideoMaker 启动失败',0x10)
        raise
