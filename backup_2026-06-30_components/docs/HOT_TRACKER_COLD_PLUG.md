# Hot Tracker 冷插拔

生产部署初期建议关闭热点模块：

```yaml
hot_tracker:
  enabled: false
  required: false
```

行为：

- `enabled=false`：跳过热点关键词收集、热点搜索和热点板块渲染。
- `enabled=true`：运行热点模块。
- `required=false`：热点失败只记录 warning，日报继续。
- `required=true`：热点失败会让 pipeline 标记失败。

验收命令：

```bash
python -m src.pipeline.runner --scrape --force
python -m src.pipeline.runner --hot-only
python -m src.pipeline.runner --brief-only
```

关闭时预期：

- 全日报不跑 Hot Topic Search。
- `--hot-only` 直接输出 skip。
- 日报卡片不包含热点追踪板块。
- 新游、市场、排名仍正常生成。
