# 固定回归样本

此目录保存之前散落在 `artifacts/` 中的新增测试依赖。图片与引用它们的测试一起提交、分发，不受运行异常截图的清理策略影响。原始实测截图继续保留在 `artifacts/`，不覆盖或删除。

样本覆盖情报奖励延迟、寻宝抽屉恢复、联盟宝藏、寻宝次数、生命之树数量和新手悬赏。新增固定样本请放入此目录；运行时异常截图仍写入 `artifacts/exception_screenshots/`。

`phone-*-1176.png` 为 USB 真机的 1176×2400 原始截图，覆盖主城、联盟、互助、探险及收益/奖励弹窗。`test_screen_geometry.py` 用这些真实样本验证缩放、不同区域的锚点、输入坐标映射和加载画面的状态保持。

`phone-hunt-search-1016.png` 验证真机搜索分类文字锐化后的精确识别；`phone-alliance-treasure-1016.png` 和 `phone-alliance-reward-1016.png` 验证联盟宝藏居中弹窗及奖励退出提示的完整识别。

验证命令：`python run_tests.py --suite screenshots`。完整回归：`python run_tests.py --suite all`。默认只向终端输出验证结果，不保存报告。

匹配素材此后统一从夜神模拟器采集。使用 `scripts/capture_nox_samples.py 名称 --device 夜神序列号 --crop 素材名:左,上,右,下` 采集指定裁剪，或用 `scripts/collect_nox_tour.py --device 夜神序列号 --tasks 任务名` 在实测任务中收集。原始和逻辑画布截图保存在 `nox/`，`assets/nox-samples.json` 记录实际裁剪来源；未登记的旧素材仍待迁移，不代表已验证。未解锁、冷却中或活动未开放应按实测输出区分，任务返回主城不等于动作执行成功。
