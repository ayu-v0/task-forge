# PostgreSQL 启动方式

## 已安装信息
- 安装版本：PostgreSQL 18.3
- Windows 服务名：`postgresql-x64-18`
- 服务启动类型：`AUTO_START`
- 数据目录：`C:\Program Files\PostgreSQL\18\data`
- `psql` 路径：`C:\Program Files\PostgreSQL\18\bin\psql.exe`
- 超级用户：`postgres`
- 已设置密码：`postgres`
- 项目数据库：`task_forge`

## 当前状态
- 当前服务状态：`Running`

## 启动 PostgreSQL
优先使用 Windows 服务方式启动：

```powershell
Start-Service postgresql-x64-18
```

如果要用系统命令：

```powershell
sc.exe start postgresql-x64-18
```

## 停止 PostgreSQL
```powershell
Stop-Service postgresql-x64-18
```

或：

```powershell
sc.exe stop postgresql-x64-18
```

## 重启 PostgreSQL
```powershell
Restart-Service postgresql-x64-18
```

## 查看状态
```powershell
Get-Service postgresql-x64-18
```

## 使用 psql
由于 `psql` 目前不在系统 `PATH`，直接使用完整路径：

```powershell
& 'C:\Program Files\PostgreSQL\18\bin\psql.exe' --version
```

连接本机默认实例：

```powershell
& 'C:\Program Files\PostgreSQL\18\bin\psql.exe' -h localhost -p 5432 -U postgres -d postgres
```

连接项目数据库：

```powershell
& 'C:\Program Files\PostgreSQL\18\bin\psql.exe' -h localhost -p 5432 -U postgres -d task_forge
```

## 项目连接串
```text
postgresql+psycopg://postgres:postgres@localhost:5432/task_forge
```

## 备注
- PostgreSQL 已安装完成，并且服务已经处于运行状态。
- 服务默认开机自启，不需要每次手动启动。
