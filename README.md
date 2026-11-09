# MiniVectorDB 提测监控

监控 OceanBase 2026 大赛赛题一（MiniVectorDB）的题包更新与提交物状态。

## 页面

<https://yangjingyu722.github.io/mvd-monitor/>

页面是一个静态 HTML，从 `data/latest.json` 读取数据并每 60 秒刷新一次，
因此不需要服务端。

## 数据

`data/latest.json` 由 `scripts/refresh.py` 生成，内容包括：

| 字段 | 含义 |
| --- | --- |
| `upstream` | 上游 `oceanbase/seekdb` 的 `2026-competition-round2` 分支 HEAD、提交列表与公开题包内容 |
| `submission` | 提交仓库 `yangjingyu722/Myseekdb` 的 HEAD、三项交付物状态与本地测量结果 |
| `timeline` | 比赛起止时间与每日提测额度 |
| `alerts` | 根据上面两项自动推导出的告警 |

## 自动刷新

`.github/workflows/refresh.yml` 每 10 分钟运行一次 `scripts/refresh.py`，
有变化时提交并推送 `data/latest.json`。

本地手动刷新：

```bash
python scripts/refresh.py --dry-run   # 只看将要写入的内容
python scripts/refresh.py             # 写入并推送
```

脚本依赖 `gh` 命令行并已登录（需要 `repo` 读权限）。

## 告警规则

| 级别 | 触发条件 |
| --- | --- |
| critical | 题包已发布但公开用例尚未跑通 |
| warning | 评测驱动未公开，协议仍是推断 |
| info | 提交分支尚未推送到远端 |

## 仓库布局

```
index.html              监控页面
data/latest.json        数据文件（由脚本生成）
scripts/refresh.py      数据生成脚本
.github/workflows/      自动刷新工作流
```
