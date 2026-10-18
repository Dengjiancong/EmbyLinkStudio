# 参与开发

当前目标是可靠的批量硬链接创建与只读核验。在线刮削暂缓，优先保持已确认的行为。

## 开发
使用 Python 3.12。旧 SFTP 回归依赖安装命令：
`python -m pip install -r requirements-dev.txt`
运行：`python -m unittest discover -s tests -t . -p "test_*.py"`
Linux GUI 测试需 tkinter、Xvfb 和显示环境；可用 `xvfb-run -a python -m unittest discover -s tests -t . -p "test_*.py"`。Linux x86-64 才支持实际 seccomp 启动；Windows 中跳过的测试不能冒充通过。

## 修改原则
- 单独提交行为变化，避免将目录重构与命名规则修改混合。
- 仅用临时目录和合成文件测试；不连接他人 NAS，不使用真实收藏、密钥或路径作测试数据。
- 不取消预览失效、修改确认、原件身份校验和跨文件系统失败保护。
- 不能仅按设备号不同预先拒绝硬链接；部分 NAS 虚拟文件系统需要实际调用判断。
- 已有入口不自动删除或重命名，下载器接入保持只读。
- 新安全边界须有回归测试，既有纯文案修改无需镜像式测试。
- 不捕获异常后虚报成功；可能已写入时保留现场并说明部分完成。

## 提交前
Git 提交信息使用中文，说明修改目的。Release 标题与正文也使用中文；不重写已有提交历史。
运行测试、`python tools/check_public.py`、`git diff --check`。
Windows 构建运行 `./build.ps1`；公开包验证运行 `python tools/check_release.py dist`。
更新 CHANGELOG 和用户说明。版本号只在 src/link_rules.py 的 VERSION 修改。

项目采用 MIT；提交贡献表示你有权提供该内容，并同意以 MIT 纳入项目。第三方依赖继续遵守自身许可。
