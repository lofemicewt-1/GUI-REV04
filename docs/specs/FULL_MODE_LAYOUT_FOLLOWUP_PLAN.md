# AWP1018 / AWP1221 Full Mode 布局后续计划

前序 AWP1221 执行与独立 QA 均已结束，再启动本阶段，遵守用户“不打断目前子智能体工作，确认后看同因并一起解决”。Type-4 仍不处理。

已确认 AWP1221 根因：外层 VerticalScrollArea 没有把视口高度传给模式内容，Full ADC 列 weight=0 导致横向压缩。前序修复新增可选 propagate_height 并调整 AWP1221 列权重；截图最大化内容填充率 98.6%。

待检查：AWP1018 mode_scroll 使用同类容器；Full Mode 多列中的 propagate_width 行为，以及 tests.test_full_mode_adc_layout.VerticalScrollAreaWidthTests.test_propagate_width_keeps_wide_form_visible 报告失败（234px）。该测试与当前用户报告潜在相关，不能仅以“既有”排除。

执行者 Luna Max 禁止创建子代理，负责唯一产品写入：复现 AWP1018 Full Mode 最大化压缩，确认是否同因；如同因复用共享能力并正确启用，保持Normal/Diff模式切换及恢复小窗可达；检查并修复当前相关宽度问题，确保AWP1221 Button/Full不回退。只改布局，不动寄存器/协议/Type-4/硬件。测试不足时补充明确断言，不为迁就实现降低要求。

独立QA Luna Max 只改验证脚本和产物，与实现者错开可见窗口操作：真实state('zoomed')检查AWP1018 Full/Normal/Diff与AWP1221 Button/Full，恢复1000x700，截图和实际内部表单可达性/可用高度，重点不是只看外层尺寸。用短而有界专项，不重复全历史矩阵。

实现文档 FULL_MODE_LAYOUT_RESULT.md / 标志 FULL_MODE_LAYOUT_READY；QA文档 FULL_MODE_LAYOUT_VALIDATION.md / 标志 FULL_MODE_QA_READY。GPT6执行期间不读取或询问，收到完成结果后验收。
