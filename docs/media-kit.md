# OpenAlphaStack 宣传素材

本目录中的宣传素材均来自当前 OpenAlphaStack 静态官网或本地模拟盘
Dashboard，不包含虚构界面、真实账户数据或收益承诺。

## 素材清单

| 素材 | 用途 | 规格 |
| --- | --- | --- |
| [Social Preview](assets/openalphastack-social-preview.png) | GitHub Social Preview、文章头图、社交分享卡片 | 1280 × 640 PNG |
| [实机演示](assets/openalphastack-demo.gif) | README、CSDN/知乎正文、产品演示 | 960 × 600 GIF |
| [静态官网首屏](assets/website-home.png) | 网站发布说明、项目介绍文章 | 1440 × 1000 PNG |
| [股票搜索](assets/dashboard-search-results.png) | 中文名称/代码/拼音搜索功能说明 | 1600 × 1000 PNG |
| [股票工作台](assets/dashboard-stock-search.png) | K 线与本地模拟盘界面说明 | 1600 × 1000 PNG |
| [三阶段工作流](assets/dashboard-workflow.png) | Research → Execution → Evaluation 架构说明 | 1600 × 1000 PNG |

![OpenAlphaStack 静态官网首屏](assets/website-home.png)

## 对外表述

推荐：

> OpenAlphaStack 是面向 A 股研究、回测与模拟交易的开源 Codex 插件。
> 它用 Skills 复用研究方法，用本地 MCP 暴露有类型的工具，并把 T+1、费用、
> 状态与模拟执行留给确定性 Python 代码。

必须同时说明：

- 仅用于研究、回测与模拟交易；
- 不连接真实券商下单；
- 不承诺投资收益；
- Dashboard 默认只监听本机，公开网址是静态介绍站。

不要使用“稳赚”“自动赚钱”“AI 选股神器”或类似无法验证的表达。

## 重新生成

Dashboard 截图更新后，安装 Pillow 并在仓库根目录运行：

```powershell
python scripts/generate_marketing_assets.py
```

脚本会根据真实截图重新生成演示 GIF 与 Social Preview。静态官网截图应在
本地组装 Pages 产物后重新采集，避免把开发服务器或本地账户信息放进图片。
