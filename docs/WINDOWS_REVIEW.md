# Windows 风险审查（构建前）

基线：原始 ZIP；GitHub main fd7d9fa50e02a84c051807845f275378ee218df4。
依据：原版 AGENTS.md、用户 18 章节及后续明确要求。

| 链路 | 源码证据及处理 | 必须取得的运行证据 |
|---|---|---|
| pywebview 生命周期 | 主线程调用 webview.start；HTTP服务处理业务。关闭先停止接单，等待当前任务，正常退出后关闭服务。不能在关闭事件中阻塞 UI 线程等待文件对话框 | 原 EXE 首次加载、正常关闭、运行中关闭 |
| 文件选择 | 复用 window.create_file_dialog；互斥防重复弹框；区别取消与错误；对话框活动时拒绝退出，避免句柄销毁竞争 | 文件/文件夹/CSV/输出目录选择、取消、再次打开 |
| WebView与通信 | 固定本地127.0.0.1端口、随机会话Cookie、Host/Origin校验；前端首次错误显示重试而不是假成功；网络恢复不丢正在编辑表单 | 未刷新首次初始化、实际按钮和错误恢复 |
| 路径与权限 | 持久化任务最终输出目录；所有打开/预览/导出读取已登记路径；创建输出前真实试写；禁止写进缓存/素材区；原文件不归软件删除 | 中文、空格、单引号、外部目录、不同cwd、只读目录 |
| FFmpeg/FFprobe | 优先随包二进制；参数列表不用shell；统一隐藏Windows子进程窗口；检查返回码和真实完整解码 | 转场/逐图时长/BGM/损坏文件/音轨检查 |
| SQLite | 复用单库事务和锁；新增列保留老数据；创建批次原子写入；每条最终配置快照；崩溃中的任务恢复为可重试失败 | 升级迁移、重启、单条失败、原配置重新生成 |
| 文件清理 | 原文件和软件副本分开；待执行/运行/失败待重试的素材受保护；状态保留；按保存策略触发；无真实上传不能删除输出 | 共用素材保护、删除后历史、重新生成缺失提示 |
| 打包资源 | 必须含ui/templates、Python/pywebview依赖、FFmpeg/FFprobe、固定WebView2及许可；记录源码和运行组件 | 从便携ZIP解压的EXE运行，不是源码启动 |
| 验收程序 | 区分脚本/环境/产品错误；路由测试不算交互；强制清理不算正常退出；不清理无关浏览器进程 | 原EXE首次页面、正常退出码和交互记录 |

pywebview 官方 API：https://pywebview.flowrl.com/api/ 。GUI循环要求主线程，业务由后台线程处理。
本文件是风险记录，不替代 AGENTS.md，不代表 Windows 已验收。

2026-09-09 构建输入核对：旧脚本从静态HTML提取CAB地址失败，Windows构建尚未开始。已通过微软实际固定版下载对话框确认152.0.4191.62 x64，构建脚本固定该输入，不增加网页抓取依赖。按微软文档为Windows 10随包runtime目录赋予App Container读取/执行权限，并明确拒绝UNC运行目录；Windows 10尚待实机验证。
依据：https://learn.microsoft.com/en-us/microsoft-edge/webview2/concepts/distribution

运行34338722526：26项业务检查和真实Linux界面生产通过；Windows构建完成，EXE未进入WebView。核对依赖源码发现6.0的ImmutableDict不允许WEBVIEW2_RUNTIME_PATH，且运行组件探测仅查注册表。改为已核实的6.2.1：支持该设置、固定组件探测和BrowserExecutableFolder；沿用同一pythonnet/WinForms依赖，无额外引擎或注册表伪装。构建前检查所需设置是否存在，启动异常写入日志。

已重新核对6.2.1的文件选择与生命周期：文件对话框仍未自行进行WinForms STA调度，捕获异常返回None。统一入口继续通过既有window.native.Invoke在UI线程调用，区分错误和取消。
源码：https://github.com/r0x0r/pywebview/blob/6.2.1/webview/platforms/winforms.py
固定组件：https://github.com/r0x0r/pywebview/blob/6.2.1/webview/platforms/edgechromium.py
