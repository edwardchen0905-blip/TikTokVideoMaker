# 开发状态 — 未通过最终交付验收

现有工程已建立统一数据模型、桌面界面与本地生产服务。九个界面入口包括首页、店铺商品、商品库、素材中心、模板、任务、视频管理、发布中心、设置。

已在 Linux 环境通过 8 项本地后端检查：素材去重与原文件保护；下载规则校验和等待连接状态持久化；批次原子写入与商品隔离；SKU 素材匹配；2 商品 × 2 模板真实视频生成并重新打开数据库验证；本地服务访问校验与媒体分段读取；100 商品 × 2 模板生成 200 个独立任务记录（非 200 个视频渲染）；图片、视频片段和音乐混合生成带 AAC 音轨的 MP4。

界面 JavaScript 语法和九条路由输出检查通过。检查工具没有浏览器布局引擎，不能替代视觉验收。检查浏览器访问本地服务被环境阻止（ERR_BLOCKED_BY_CLIENT）。

未完成：Windows 桌面启动、原生文件选择、界面视觉与实际操作验收、自带运行环境打包。当前环境为 Linux，没有 Windows 构建器、pywebview、PyInstaller 或可用 Windows 运行环境。因此尚未生成、验证或交付 TikTokVideoMaker_Portable.zip。

旧猜测 TikTok 采集扩展、独立 JSON 素材库、旧 Tkinter 界面已从活动源码移除。真实店铺连接与发布仍等待用户提供环境，不得冒充已同步或已发布。

必须继续按照 AGENTS.md 和用户最终冻结协议执行，不向用户交付阶段源码或 Demo。


## GitHub Actions Windows 构建接入
已添加 .github/workflows/windows-portable.yml，使用 windows-latest / Python 3.12 x64，下载并打包 FFmpeg、固定版 WebView2，执行现有测试、PyInstaller 打包、解压 EXE 启动检查，上传 Portable ZIP。prepare_windows_runtime.py 从官方页面发现 WebView2 CAB；页面未暴露链接时明确失败，可通过 workflow_dispatch 官方 CAB URL 参数指定。
本地仅完成语法、工作流结构和页面渲染检查。GitHub 尚未授权，未提交远程仓库、未触发构建、未生成 EXE、未完成 Windows 六项验收。启动检查只检查窗口和数据库存在，不代表完整业务验收。
