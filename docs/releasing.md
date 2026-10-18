# 开源准备与发布

当前仅完成本地准备，没有创建 GitHub 仓库、上传文件或运行 GitHub Actions。

## 公开边界
旧本地仓库历史含个人配置记录，不能直接整体推送。先运行：
`python tools/check_public.py --history --report build/privacy-history.json`
工具仅输出位置与类别，不输出命中的值；不能代替人工检查。保留本地历史，不自动重写。

本次建议从审查后的当前源码快照建立全新的公开仓库历史。快照不含 .git、构建目录、密钥或任务状态。是否采用新历史和公开发布，需由仓库所有者在实际发布时决定；当前工具不会初始化、推送或公开远程仓库。

## 候选构建
1. 测试并运行当前源码隐私检查。
2. Windows 运行 build.ps1；NAS 包也可单独运行 package_deploy.py。
3. check_release.py 验证 NAS ZIP 的清单、哈希、无凭据/状态文件，并生成 SHA256SUMS.txt。
4. 检查实际发行文件与截图；不要上传旧完整包、源码目录中的 .env，或整个 dist。
5. 阅读 THIRD_PARTY_NOTICES.md 和包内 Python/Tcl/Tk 许可。
6. 用一次性测试目录在 NAS 验收，确认后才能称为已验证发布。

CI 配置为 Windows/Linux、Python 3.12/3.13 测试；手动构建工作流只生成并检查候选，不上传构建产物、不创建 Release。当前环境无法运行远端 Actions，首次发布前需查看其实际运行结果。
GitHub Actions 的外部动作版本应定期审查。

## 新用户与升级用户
新包不携带 .env。新用户在部署目录运行 init_config.py 或手动生成独立密钥。
升级包不包含有效区域配置、.env 或 state；保留管理员自己的配置与密钥。不要用公共模板覆盖现有挂载。
旧版已经生成的私人包只供本人本地使用，不要重新用于公开发布；本次不会删除旧包。

## 支持范围
当前主要运行环境：Windows 桌面客户端、Linux x86-64 NAS Docker 执行端。并非所有 NAS/文件系统已验证；ARM 执行端当前因保护层限制不受支持。
