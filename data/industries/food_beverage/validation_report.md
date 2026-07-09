# 食品饮料行业 图谱校验报告

- 状态：pass
- 节点数：128（目标 60-100，硬上限 150）
- 关系数：137
- contains 最大深度：4（目标至少存在若干 L4/L5 核心链条）
- error：0
- warning：0

## 问题列表

未发现阻断性问题。

## 格式修复 Agent

- 状态：skipped
- 总结：硬规则校验通过，未调用百炼格式修复。
- 最小修改数：0
- 格式复核项：0

## 阶段质量评估意见

- fused_optimized_graph：status=pass，initial=pass，score=92，revision=not_revised。融合 graph.json 与 codex-graph.json 后的食品饮料产业链图谱，主结构为树状 contains，节点命名偏投研展示口径，关系边已去除跨分支和传递冗余。
  - 节点数量由原 68/71 提升至 128，覆盖上游原料、配料、包装、食品制造、饮料、酒类、设备、冷链、渠道。
  - 关系以唯一父子 contains 为主，仅保留同一分支内相邻工序 upstream_downstream。
  - 深度控制到 L4，避免原图局部分支过深和 codex 图整体过浅的问题。
  - 节点命名采用稳定产业、品类、工艺和设备名词，避免公司、品牌、市场规模、财务指标等不适合作为产业链节点的内容。
