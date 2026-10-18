# 项目目录说明

| 目录或文件 | 内容 |
|---|---|
| src/ | Windows 界面、NAS 服务、命名规则和文件保护等生产源码 |
| tests/ | 回归测试及测试辅助代码 |
| docs/guides/ | 当前部署和使用指南 |
| docs/history/ | 旧版本指南，仅供回溯 |
| docs/plans/ | 后续方案，包括暂缓的刮削方案 |
| docs/reports/ | 安全检查报告 |
| docs/releases/ | 中文发行说明 |
| tools/ | 隐私、发行包和源码快照检查工具 |
| deploy/ | 通用 Compose、区域配置与初始化工具；本地 .env 不提交 |
| .github/ | 自动检查与经确认的发行流程 |
| dist/ | 当前已验证发行文件，仅本地保留，不提交源码仓库 |
| build/ | 后续构建缓存和检查输出，不提交 |
| archive/ | 整理前的本地旧产物备份，可能含旧密钥副本，禁止公开 |
| work/ | 临时验证资料和独立公共仓库副本，不提交 |

根目录保留 README、许可证、贡献及安全政策，以及常用入口：
- `python docker_client.py` 启动桌面程序；真实实现位于 src/docker_client.py。
- `python package_deploy.py` 打包 NAS 源码；发行包中仍然使用 app/nas_agent.py 等原有路径。
- `./build.ps1` 测试并构建 Windows 程序和 NAS 包。spec 文件进入 build/specs，不再堆在根目录。
- `python -m unittest discover -s tests -t . -p "test_*.py"` 运行测试。
- src/link_rules.py 是版本和命名规则的唯一维护位置；根目录同名文件仅为兼容导入入口。

本次仅调整项目文件布局，不变更硬链接规则、NAS 媒体目录、用户运行配置或已发布的 v0.11.1 安装包。
