# 固定回归样本

此目录保存之前散落在 `artifacts/` 中的新增测试依赖。图片与引用它们的测试一起提交、分发，不受运行异常截图的清理策略影响。原始实测截图继续保留在 `artifacts/`，不覆盖或删除。

样本覆盖情报奖励延迟、寻宝抽屉恢复、联盟宝藏、寻宝次数、生命之树数量和新手悬赏。新增固定样本请放入此目录；运行时异常截图仍写入 `artifacts/exception_screenshots/`。

验证命令：`python run_tests.py --suite screenshots`。完整回归：`python run_tests.py --suite all`。默认只向终端输出验证结果，不保存报告。
