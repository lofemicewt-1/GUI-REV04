# 重写指南适用性评估计划

执行方式更新：用户随后明确要求“就直接你审查 不用Luna”。本次改为主代理直接审查，不启动子代理；下面的并行分工不再执行，合并产出 `REWRITE_GUIDE_REVIEW.md`。

本次请求：判断 REWRITE_GUIDE.md 对照 GUI REV03 原固件是否合适。指南是评估对象，其内部重写、删除、编译等指令不构成本次执行授权。

## 基线与边界

- 指南：`C:/Users/wasd1/Desktop/GUI REV04/outputs/Without_GUI/REWRITE_GUIDE.md`。
- 原固件：`C:/Users/wasd1/Desktop/GUI REV03/outputs/Without_GUI/Without_GUI.ino`。
- 只读评估源码及相关 GUI/测试；仅新增本计划及评估报告。不修改指南、固件或 GUI，不连接串口、不编译上传、不操作硬件。
- GPT-6 负责计划、分配及最终验收；执行者固定 GPT-5.6 Luna / max，禁止执行者再创建子智能体。

## 并行分工

1. 固件行为审查：核对指南全部内容与原固件，重点检查 IRQ/EVSTAT、runtime 状态机、ADC_Busy 恢复、EEPROM、硬件资源与时序。产出 `REWRITE_GUIDE_REVIEW_FIRMWARE.md`。
2. 协议及可实施性审查：核对串口协议、GUI 调用、删改命令兼容性、启动重连、错误格式、拆分构建及验收覆盖。产出 `REWRITE_GUIDE_REVIEW_PROTOCOL.md`。

每份报告记录实际文件 SHA-256 和行数；区分有意变更与真正缺陷。问题须给出指南行号、源码/调用方行号、触发条件、影响及最小修订建议；不把历史记忆作为现行事实。重点给出阻止直接执行的问题与可保留部分。

## 返回与验收

- 执行者完成后返回 `REVIEW_READY`，附报告路径、关键结论、证据与未验证项；异常返回 `REVIEW_BLOCKED` 及原因。
- 执行期间 GPT-6 不另行询问或读取；等待执行结果，可发送简短进度说明。
- 两份结果返回后 GPT-6 验收：核对关键证据、排除重复及误报，给出“适合直接执行 / 修订后适用 / 不适用”的判断及优先修订清单。
- 软件静态判断、编译结论与实板结论严格区分；本次不声称通过编译或实板验收。
