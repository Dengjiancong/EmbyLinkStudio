# 第三方组件

EmbyLinkStudio 自有代码按根目录 LICENSE 的 MIT 许可证提供。下列项目保留各自许可证；本项目不改变这些许可，也不代表第三方背书。

- Python / CPython：PSF License；https://docs.python.org/3/license.html
- Tcl/Tk / tkinter 运行库：Tcl/Tk 相关许可证；Windows PyInstaller 构建可能包含运行库与许可证文件。https://www.tcl-lang.org/software/tcltk/license.html
- PyInstaller（构建工具与引导程序）：GPL 加分发例外；https://pyinstaller.org/en/stable/license.html
- Paramiko（旧 SFTP 客户端及其测试依赖）：LGPL-2.1；https://www.paramiko.org/
- Paramiko 的加密依赖具有各自许可，发布旧客户端时须另行检查其完整依赖。

当前 Docker 客户端入口为 docker_client.py，执行端为 nas_agent.py；它们不导入旧 SFTP 客户端。构建时应保留 PyInstaller 收集的运行库许可，不可将整份二进制笼统描述为全部仅受 MIT 许可。

NAS 模板引用官方 Python 容器镜像，镜像包含 Debian 等组件；其许可信息由对应镜像发行方提供。NAS 代码包不附带该镜像。
