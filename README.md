# PCL2 · Python Edition

参考 [PCL2（Plain Craft Launcher 2）](https://github.com/Meloong-Git/PCL) 的信息架构与交互习惯，使用 **Python + PySide6** 从零实现的跨平台 Minecraft 启动器。

> 本项目与官方 PCL（VB.NET / WPF）**没有任何代码继承关系**：不含原项目源码或资源，仅参考其页面划分、操作逻辑与功能范围。界面、业务逻辑均由 Python 独立实现。

## 功能

| 模块 | 已实现能力 |
| --- | --- |
| 版本下载安装 | 官方版本清单（正式版 / 快照 / 远古版）、客户端 jar、依赖库、资源索引与资源对象；`sha1` + `size` 双校验、断点续传、原子落盘；支持 Forge / NeoForge / Fabric / Quilt 合成可启动版本 |
| 实例管理 | 新建 / 复制 / 重命名 / 删除实例、版本隔离目录、导入已有 `.minecraft`、导出整合包；`versions/` 下已安装版本会自动登记为实例（友好名称 + 加载器信息，删除后不再自动出现）；新建实例直接从已安装版本下拉选择，加载器与启动版本自动识别 |
| 一键装 Java | Adoptium Temurin JRE 自动下载解压到启动器目录，可指定 8/11/17/21/25 |
| 离线启动 | 仅离线登录（离线 UUID 与 Java 版 `UUID.nameUUIDFromBytes` 完全一致）、Java 自动扫描与按版本挑选、内存 / 窗口 / JVM / 游戏参数、`natives` 解压、实时日志与结束进程 |
| 资源管理 | Mod（`fabric.mod.json` / `META-INF/mods.toml` / `mcmod.info`）、资源包（`pack.mcmeta`）、光影（`shaders/`）扫描；`.disabled` 启停 / 批量启停与删除；拖入安装；基于元数据的依赖缺失 / 重复 / 版本不匹配检查；Modrinth 指纹比对显示可更新版本并一键替换 |
| 整合包 | Modrinth（`.mrpack`）、CurseForge（zip + `manifest.json`）、HMCL（`modpack.json`）、PCL（`PCL/Setup.ini`）导入；导出为 HMCL / PCL 包，Modrinth 导出为尽力而为模式 |
| 下载引擎 | 并发线程池、按目标路径去重、镜像回退（BMCLAPI）、限速、取消、失败重试、按字节聚合的进度与速度 |
| 社区资源 | Modrinth 搜索与下载 |
| 实例详情 | 概览 / 设置 / 内容 / 存档 / 服务器 |
| 崩溃分析 | 规则匹配 crash-report 与日志 |
| 配置备份 | 设置页导出 / 导入 `config.json`，恢复启动、界面、下载与高级选项 |
| 界面 | 无边框窗口 + 自绘标题栏、左侧图标导航、卡片式布局、页面淡入动画、亮 / 暗主题与 8 种主题色、圆角与阴影、轻提示与冲突横幅 |

## 运行环境

- Python 3.11+（开发环境使用 3.12，见 `.python-version`）
- [uv](https://docs.astral.sh/uv/) 管理虚拟环境与依赖（`pyproject.toml`）

```bash
uv sync                        # 创建 .venv 并安装依赖
uv run python -m pcl           # 启动界面
uv run python main.py          # 等价入口
uv run pytest                  # 运行测试（46 项）
```

无显示环境（CI / SSH）下做界面冒烟：

```bash
QT_QPA_PLATFORM=offscreen uv run pytest -m gui
```

## 目录约定

数据根目录默认 `~/.local/share/pcl2`（Windows 为 `%APPDATA%\pcl2`，macOS 为 `~/Library/Application Support/pcl2`），可用环境变量 `PCL2_HOME` 覆盖：

```
<数据根目录>/
├── config.json                  # 全局配置（设置页修改即时保存）
├── accounts.json                # 离线账户
├── instances/<id>/
│   ├── instance.json            # 实例元数据
│   └── .minecraft/              # mods / resourcepacks / shaderpacks / saves / config
├── .minecraft/                  # 共享游戏文件
│   ├── versions/<id>/<id>.json
│   ├── libraries/
│   └── assets/
├── cache/                       # 版本清单、安装器、natives 解压
└── logs/                        # launcher.log / latest.log
```

## 代码结构

```
src/pcl/
├── core/        # 路径与平台差异、配置、日志、事件总线、异常
├── net/         # HTTP、镜像映射、并发下载引擎
├── version/     # 版本清单、rules 求值、inheritsFrom 合并、资源索引、安装编排
│   └── loaders/ # Fabric / Quilt profile 安装，Forge / NeoForge processors 链
├── launch/      # Java 扫描、参数拼装、离线账户、进程运行
├── instance/    # 实例模型与 CRUD
├── content/     # Mod / 资源包 / 光影解析、冲突检查与 Modrinth 更新检测
├── modpack/     # 四种整合包解析与导出
├── ui/          # 主窗口、页面、自绘控件、弹层、workers
└── resources/   # QSS 模板与 SVG 图标
```

分层原则：`core → net → domain（version/launch/instance/content/modpack）→ ui`。领域层零 Qt 依赖，可脱离界面测试；UI 的所有 IO 都通过 `ui/workers.py` 的线程池执行，仅通过 Qt 信号回到主线程。

## 实现要点

- **rules 求值**：`allow` / `disallow`、`os.name` / `os.arch` / `os.version` 正则、`features`（`has_custom_resolution` 等），第一条命中的规则决定是否启用该库。
- **启动参数**：同时支持新版 `arguments` 对象与旧版 `minecraftArguments` 字符串；classpath = 各库 artifact + 客户端 jar（沿 `inheritsFrom` 查找）；占位符全部替换；离线 UUID 去掉连字符传入 `--uuid`。
- **加载器安装**：Fabric / Quilt 直接落盘 profile JSON；Forge / NeoForge 解包安装器、释放内嵌 maven 工件、按 `processors` 链执行 Java 处理器（仅 client 侧）并校验输出。
- **镜像策略**：将官方域名重写为 BMCLAPI 路径，`auto` 模式镜像优先、失败或校验不通过自动回退官方源；错误页不会污染缓存。
- **启停方案**：Mod / 资源包 / 光影统一使用 `.disabled` 后缀，与 HMCL / PCL 行为一致，不移动文件。

## 已知限制

- 仅支持离线账户，**不包含**微软正版登录与外置登录（authlib-injector）。
- CurseForge 整合包的文件下载地址未随包分发，需要在「设置 → 高级」填写 API Key；未填写时只还原覆盖文件并提示手动安装。
- Modrinth 导出无法还原原始下载地址（本地文件仅能计算哈希），重新安装时依赖包内 `overrides` 文件。
- 下载源镜像的可用性依赖第三方服务；界面与协议均按官方格式实现，但未在全部老旧版本上逐一实测。

## 免责声明

Minecraft 是 Mojang Studios 的商标，本项目与其无隶属关系。请仅用于个人学习与合法的游戏管理用途。
