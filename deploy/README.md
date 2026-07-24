# 部署入口

部署文件集中在本目录，仓库根目录仍作为 Docker 构建上下文。

## 完整本地应用

```powershell
docker build -f deploy/Dockerfile -t openalphastack .
docker compose -f deploy/docker-compose.yml up -d
```

完整镜像包含本地 API、观察面板和模拟交易能力，默认映射到
`127.0.0.1:8800`。运行数据保存在根目录的 `data/`。

## 公网只读 MCP

```powershell
docker build -f deploy/Dockerfile.public -t openalphastack-public-mcp .
docker run --rm -p 8000:8000 openalphastack-public-mcp
```

公网镜像只包含无状态、只读工具，不包含本地运行、持仓、账本、文件访问或
模拟计划发布。详细边界与托管检查见
[公网 MCP 部署指南](../docs/public-mcp.md)。
