# 当前测试版部署到 Windows 服务器

## 生成发布包

在开发机项目目录运行：

```powershell
& '.\scripts\package-server.ps1'
```

输出位于 `artifacts\A-Cat-Girl-v<版本>-web-windows-x64.zip`。发布包不包含开发机的数据库、API Key 密钥、`.env`、`.venv` 或 Node.js 依赖。

## 上传与初始化

1. 把 ZIP 上传到 Windows 服务器并解压到本地磁盘，例如 `D:\Apps\Catgirl`。不要直接在映射盘或压缩包内运行。
2. 只在服务器本机或远程桌面浏览器使用时，双击 `deploy\windows\初始化服务器环境.cmd`。
3. 需要从可信局域网电脑访问时，双击 `deploy\windows\初始化为局域网访问.cmd`，并在 Windows 防火墙中仅允许可信来源访问 TCP 8732。
4. 初始化会从官方地址安装 `uv` 和 Python 3.12，再根据 `uv.lock` 创建 `.venv`，因此服务器首次安装需要访问互联网。
5. 双击 `deploy\windows\启动一只猫娘.cmd`。保持窗口运行，浏览器访问 `http://127.0.0.1:8732`；局域网访问则使用服务器内网 IP。

当前管理界面没有登录鉴权，禁止把 8732 端口直接暴露到公网。

## 是否迁移开发机配置

新服务器从空数据库开始最稳妥。若确实要复制当前配置，需要停止本地和服务器程序，并把整个 `data` 目录一起安全传输；`catgirl.db`、WAL/SHM 和 `secret.key` 必须成套，不能只复制数据库。该目录包含 API Key 等敏感信息，不得放入普通发布 ZIP 或公开网盘。

## 当前限制

这是临时测试部署，启动窗口关闭后程序会停止，尚未注册 Windows 服务。正式的 WinSW 服务、一键更新和自动回退脚本仍按 `WINDOWS_DEPLOYMENT.md` 实现；当前项目还没有远程 Git 更新源，因此暂时不能执行真正的增量更新。
