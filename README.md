# EmbyLinkStudio

为 Emby 批量创建名称兼容的硬链接入口，保留原始收藏名称。Windows 桌面预览，NAS Docker 本地执行。

**当前版本：v0.11.1 · 开源准备版**。在 v0.11 的基础上整理发布、安全文档与工程检查，不改变既有命名规则、任务格式和创建流程。公开历史从脱敏源码快照开始；远端测试结果请查看 Actions。

## 工作方式
1. 管理员在 NAS 配置允许使用的存储区域。
2. Windows 客户端浏览原件和硬链接目录，生成只读预览。
3. 检查待创建、冲突及跳过原因，确认修改操作后创建。
4. 用只读核验确认入口与原件是否共享文件身份。

支持任务保存、新增检测、下载保护、进度、安全停止、断线恢复查看、批量核验与 CSV。在线刮削暂缓，可继续使用其他成熟工具。

## 安全边界
- 不自动删除、重命名或覆盖原件；硬链接失败不改成复制。
- 硬链接与原件共用内容。改名互不影响，改写内容可能相互影响。
- 名称缩短不会额外保存一份视频；文件夹属性累计大小不能证明实际多占一份空间。
- 默认关闭 NFO 写入。外部刮削器也不应直接改写与原件共享的元数据。
- 原件与入口必须支持实际硬链接调用；Windows 盘符和容器路径不是同一种路径。
- 当前生产执行端要求 Linux x86-64，其他架构未支持；并非所有 NAS 都已验证。
- 默认 HTTP 仅用于可信局域网，不直接暴露公网。详见 [安全政策](SECURITY.md)。

## 下载
正式安装包在 [GitHub Releases](https://github.com/Dengjiancong/EmbyLinkStudio/releases)。
Windows 使用 EXE 便携程序；首次部署 NAS 使用完整包，已有用户使用 update 升级包。下载后可用 SHA256SUMS.txt 核对文件；保留自己的配置、密钥和状态目录。

## 安装与升级
阅读 [部署说明](docs/guides/部署说明.md)。新公开包不包含密钥；首次部署自行生成 .env。
已有用户升级保留原有 .env、state、自定义区域与挂载，不能用公共示例覆盖实际配置。
Windows 下载候选 EXE；开发运行入口为 `python docker_client.py`。新的空白设置默认指向本机地址，请填写自己的 NAS；已有保存的执行端地址不变。

## 目录
源码在 src/，测试在 tests/，当前指南在 docs/guides/；旧版指南、方案和报告分别归档。详见 [项目目录说明](docs/directory-layout.md)。

## 开发与发布
Python 3.12：
```text
python -m pip install -r requirements-dev.txt
python -m unittest discover -s tests -t . -p "test_*.py"
python tools/check_public.py
python package_deploy.py
python tools/check_release.py dist
```
Windows 完整构建：`./build.ps1`。NAS 执行端为标准库实现，requirements.txt 中 Paramiko 用于旧 SFTP 回归。
Linux GUI 测试需 Xvfb；参见 [贡献指南](CONTRIBUTING.md)。
测试全部使用临时目录；生产 NAS 验收须单独进行，不因 Windows 测试通过而省略。

- [架构与模块职责](docs/architecture.md)
- [开源与发布流程](docs/releasing.md)
- [第三方许可说明](THIRD_PARTY_NOTICES.md)
- [变更记录](CHANGELOG.md)
- [v0.11 安全检查报告](docs/reports/安全与边界检查报告.md)
- [后续升级方案](docs/plans/软件升级方案.md)

## 许可证
项目自有代码使用 [MIT](LICENSE)。运行库与依赖保留各自许可。

本项目是独立社区工具，与 Emby、NAS 厂商无隶属或官方支持关系。
