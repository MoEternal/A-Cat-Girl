# Windows 服务器部署与更新设计

## 交付形式

正式部署使用 Windows 发布包，不制作单文件 EXE：

- `安装一只猫娘.cmd`：双击入口，以当前进程范围启动 PowerShell 安装脚本。
- `install.ps1`：检查管理员权限、安装或调用 `uv`、创建锁定的 Python 环境、写入服务配置并注册 WinSW 服务。
- 预构建的 `frontend/dist`：服务器不需要 Node.js 或 npm。
- `uv.lock`：服务器按确定版本重建 Python 环境，不复制开发机 `.venv`。
- WinSW：将程序注册为开机启动、异常自动重启且带日志轮转的 Windows 服务。

不优先使用 PyInstaller 或单文件 EXE。项目允许加载第三方 Python 插件，保留标准 Python 环境更利于插件兼容、依赖审计、故障定位和小步更新。

## 目录隔离

程序版本与持久数据必须分开：

```text
C:\Program Files\Catgirl\
|- current -> releases\<当前版本>
|- releases\
|- updater\
`- service\

C:\ProgramData\Catgirl\
|- data\
|- logs\
|- backups\
`- config\
```

数据库、`secret.key`、用户安装插件、QQ 收图和配置都位于 `C:\ProgramData\Catgirl`，更新时不得放入版本目录或被覆盖。

## 版本管理

项目不提供自动更新入口，发行版本和持久数据由用户自行管理。
