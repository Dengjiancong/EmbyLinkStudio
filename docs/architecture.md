# 架构与维护约定

生产源码归入 src/，回归测试归入 tests/。根目录保留启动和版本兼容入口；不改变 HTTP 协议、任务格式、Docker 包内 app 路径或用户运行状态。

## 现有边界
| 层 | 文件 | 职责 |
|---|---|---|
| Windows 界面 | docker_client.py、advanced_ui.py、directory_picker.py | 呈现、确认、只读目录选择 |
| 客户端状态与通信 | client_api.py、client_models.py、credentials.py、recovery_store.py | HTTP、任务偏好、密钥文件读取、恢复记录 |
| 区域与任务服务 | region_service.py、advanced_service.py | 管理员允许范围、任务、只读核验 |
| 文件执行 | nas_agent.py、safe_fs.py、delete_guard.py | 创建、身份检查、目录句柄、防删除层 |
| 规则和可选功能 | link_rules.py、media_features.py、qbit_reader.py、nfo_editor.py | 命名、下载保护、可选元数据 |
| 发布与检查 | package_deploy.py、tools/ | 公共包清单、隐私检查、包校验 |
| 历史客户端 | hardlink_studio.py | 旧 SFTP 回归；不作为当前入口 |

版本号以 link_rules.VERSION 为唯一来源。当前 Python 3.12 为交付基线，CI 另配置 3.13 回归，未经运行的 CI 不能宣称已验证。

## 固定契约
预览到创建之间重新检查文件身份；修改参数作废预览；重复创建跳过已有入口；不因失败改成复制；停止仅在安全边界结束；断线不等于后台任务停止；命名兼容旧入口。
部署文件只提供示例，不代表现有管理员配置会自动迁移。

## 后续拆分顺序
先用现有测试固定这些契约，再逐步将 nas_agent.py 的协议与执行部分、Studio 的视图与任务控制拆分到子模块；每一步保持入口和导入兼容。之后评估 SQLite、协议版本协商和性能基准。这些不包含在本次行为稳定版中。
