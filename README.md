# PCL2 · Python Edition

参考 [PCL2（Plain Craft Launcher 2）](https://github.com/Meloong-Git/PCL) 的信息架构与交互习惯，使用 Python + PySide6 从零实现的跨平台 Minecraft 启动器。

> 本项目与原 PCL（VB.NET / WPF）没有任何代码继承关系，仅参考其页面划分、操作逻辑与功能列表，也未携带任何原项目资源文件。

## 功能范围（当前版本）

| 模块 | 能力 |
| --- | --- |
| 版本下载安装 | 官方版本清单（正式版 / 快照 / 远古版）、客户端 jar、依赖库、资源索引与资源对象；支持 Forge / NeoForge / Fabric / Quilt 合成可启动版本 |
| 实例管理 | 新建 / 复制 / 重命名 / 删除实例，版本隔离目录，导入已有 `.minecraft` |
| 离线启动 | 仅离线登录（离线 UUID 生成规则与 Java 版一致），Java 自动扫描，内存 / 窗口 / JVM 参数可配，实时日志与结束进程 |
| 资源管理 | Mod（fabric.mod.json / mods.toml / mcmod.info）、资源包、光影的扫描、启停、拖入安装与依赖冲突检查 |
| 整合包 | Modrinth(`.mrpack`)、CurseForge(zip)、HMCL、PCL 四种格式导入；导出为 HMCL / PCL 通用包 |
| 下载引擎 | 多线程并发、断点续传、sha1 与 size 双校验、原子落盘、镜像回退、进度与速度聚合、取消与重试 |
| 设置 | 启动 / 界面（主题色、深色模式、动画）/ 下载（源、并发、限速）/ 高级分组配置 |

## 运行环境

- Python 3.11+（开发环境锁定 3.12，见 `.python-version`）
- [uv](https://docs.astral.sh/uv/) 作为包与虚拟环境管理器

## 快速开始

```bash
uv venv --python 3.12     # 首次创建虚拟环境
uv sync                   # 安装依赖
uv run python -m pcl.app  # 启动界面
# 或
uv run pytest             # 运行测试
```

无显示环境（CI / SSH）下做界面冒烟测试：

```bash
QT_QPA_PLATFORM=offscreen uv run pytest -m gui
```

## 目录约定

```
~/.local/share/pcl2/            # 数据根目录（Windows: %APPDATA%\pcl2，可用 PCL2_HOME 覆盖）
├── config.json                 # 全局配置
├── accounts.json               # 离线账户
├── instances/<id>/             # 实例元数据与隔离目录
│   ├── instance.json
│   └── .minecraft/             # mods / resourcepacks / saves 等
├── .minecraft/                 # 共享游戏文件
│   ├── versions/<id>/<id>.json
│   ├── libraries/
│   └── assets/
├── cache/                      # 版本清单、任务缓存
└── logs/                       # 启动器日志（轮转）
```

## 免责声明

本项目不包含任何正版账号凭据，不支持微软正版登录（仅离线）；下载源默认走官方地址，可选 BMCLAPI 等第三方镜像。Minecraft 是 Mojang Studios 的商标，本启动器与其无隶属关系。
