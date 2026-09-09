# TikTokVideoMaker — 当前开发工程

最终目标是 TikTokVideoMaker_Portable.zip：解压后双击 TikTokVideoMaker.exe，不要求用户安装 Python 或配置开发环境。

本工程尚未完成 Windows 绿色版构建与桌面验收，不能作为成品交付。

当前调用链：tiktok_video_maker.py → app/desktop.py → core/workspace.py 与 renderer/video_engine.py。ui/ 是同一桌面应用内的界面，不是独立网站或另一个产品。

所有商品、SKU、素材关联、生产任务、视频、发布记录、图片下载规则均保存在 data/workspace.sqlite3。所有原始素材保留；导入副本、下载目录、渲染缓存和视频输出分别管理。

店铺同步与发布仅保存配置和等待连接状态，没有实现真实采集或上传。下载规则支持第一张 / 全部 / 指定主图序号、全部详情图、SKU 图；商品视频开关关闭。同步配置保存规则快照。图片关联记录商品 ID、SKU ID、图片类型与排序，素材文件记录来源及路径。

本地验证：python -m unittest discover -s tests -v。界面路由检查：node tests/check_ui.cjs。后者不是浏览器布局或 Windows 启动验收。

构建工具 build_windows.py 只能在 Windows 构建机使用，需要 PyInstaller 和 requirements.txt，以及 runtime/ffmpeg.exe、runtime/ffprobe.exe、完整的 runtime/webview2 固定运行时和相应再分发许可。工具不在 Linux 伪造 EXE，不要求最终用户安装这些开发依赖。生成包还必须执行真实 Windows 桌面验收后才能交付。
