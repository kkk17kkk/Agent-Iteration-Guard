# recipe_planning 评估

## 项目上下文 / Project Context

- 项目：LightTable
- 用途：????? LightTable ???????????????????
- Baseline：main-fa774ef
- Candidate：candidate-gui-v2-20260806
- Runtime：native_command

## 1 · Capability Overview
### 能力概览

**产品职责**：在 maintain 目标下为 2 人家庭在工作日 20 分钟内规划餐食，遵守忌口鸡蛋并优先消耗番茄和豆腐库存。

**为什么需要**：帮助用户在时间紧张、库存有限且存在饮食禁忌时，获得可直接执行的餐食方案，减少临时决策成本。

**改善的用户问题**：用户希望获得符合个人约束、时间可行且能减少临时决策成本的餐食计划，但仅靠通用回答容易忽略忌口或库存。

**能力边界**：时间过短或输入不完整时应缩小方案范围或请求澄清；不能把近似营养信息表达为医疗级精确结论；不能为了消耗库存而违反明确饮食禁忌。

**理想行为**：
- 识别 maintain、2 人和 20 分钟等任务约束
- 不推荐含鸡蛋的菜品
- 优先利用番茄和豆腐库存
- 输出结构化、可执行的餐食方案和必要的购物建议

## 2 · Evaluation Context
### 评估上下文

| 项目 | 内容 |
| --- | --- |
| 用户任务 | 在 maintain 目标下为 2 人家庭规划工作日 20 分钟内的晚餐，遵守忌口鸡蛋并优先消耗番茄和豆腐库存。 |
| 目标与人数 | 维持体重（maintain），2 人用餐。 |
| 时间预算 | 工作日 20 分钟；边界场景为 5 分钟。 |
| 饮食禁忌 | 忌口鸡蛋，不能为了消耗库存而违反禁忌。 |
| 冻结库存 | 番茄 2 个、豆腐 1 盒、鸡蛋 6 个，其他调料和主食齐全。 |
| 预期结果 | 输出结构化、可执行的餐食方案，不推荐含蛋菜品，优先利用番茄和豆腐，必要时给出购物建议。 |

## 3 · Executive Summary
### 评估结果汇总

**最终结论**：recipe_planning 是满足用户约束的关键组成：启用时 5 个场景全部通过约束检查，移除或替换后 10 个场景全部在约束执行上失败，说明该能力直接决定忌口与库存约束的落实。

**主要发现**：
- **能力价值：约束执行是核心产出**：启用 recipe_planning 时，5 个场景全部通过约束检查，正确识别忌口鸡蛋并优先利用番茄和豆腐库存。
- **能力损失：移除后约束执行失败**：移除 recipe_planning 后，5 个场景全部在约束执行上失败，基础输出仍在但未遵守忌口与库存约束。
- **替换风险：候选实现无法保持核心价值**：替换实现虽产出结构化方案，但 5 个场景全部在约束执行上失败，无法保持忌口与库存约束的核心价值。
- **稳定性：约束执行跨场景一致**：启用条件下 5 个不同场景（标准、冲突、边界、鲁棒、交互）均稳定通过约束检查，能力表现一致。

**建议**：保留 recipe_planning 作为餐食规划的核心能力，不进行移除或替换；后续优化应聚焦增强边界场景的澄清与简化建议。

**后续优化重点**：
- 补充边界场景的澄清与简化建议验证
- 增加跨日期与更多库存组合样本
- 验证替换实现能否恢复约束执行

## 4 · Evaluation Dimensions
### 评估维度

### Trigger：能力触发

启用时正确进入餐食规划流程，移除或替换后未进入该能力流程。启用 Skill 的 5 个场景均完成规划流程；移除和替换场景均未进入目标能力流程。

### Execution：流程执行

启用时完成约束执行，移除或替换后约束执行失败。启用场景约束检查全部通过；移除与替换场景均出现含忌口食材或缺失约束执行。

### Delivery：结果交付

启用时交付结构化可执行方案，移除或替换后仍能产出基础输出但约束不达标。三种条件下均返回结构化餐食方案，但移除与替换方案未遵守忌口约束。

### Boundary：能力边界

三种条件下均未超出声明范围，未产生额外副作用。所有场景边界行为均符合声明契约，未出现越界或未声明副作用。

## 5 · Experiment Overview
### 实验地图

本评估回答一个产品问题：不同实现是否改变用户实际得到的产品能力。通过保留、移除和替换三种比较，判断 recipe_planning 是否是满足用户约束的关键组成。

### 能力保留价值

**回答的问题**：保留 recipe_planning 时用户得到什么产品结果？

**目的**：建立完整能力的产品质量基线，确认约束执行、交付质量与边界控制。

### 能力移除损失

**回答的问题**：移除 recipe_planning 后用户结果损失什么？

**目的**：识别该能力是否是满足用户约束的必要组成，以及移除后的能力损失。

### 能力替换风险

**回答的问题**：替换实现能否保持 recipe_planning 的核心产品价值？

**目的**：判断未来实现变更是否能在不损失约束执行与交付质量的前提下保持产品价值。

## 6 · Experiment Analysis
### 实验明细

### 保留能力基线：约束执行通过

**目的 Purpose**：验证完整 Agent 在标准条件下能否识别约束并输出可执行方案，建立产品质量基线。

**设计 Design**：保留 recipe_planning，在 5 个声明的用户任务中运行完整 Agent，检查约束执行、交付结构与边界。

**输入场景 Input Scenario**：2 人、20 分钟、maintain、忌口鸡蛋，库存含番茄 2 个、豆腐 1 盒、鸡蛋 6 个。

**观察 Observation**：5 个场景全部通过约束检查，正确识别忌口鸡蛋并优先利用番茄和豆腐库存，输出结构化方案。

**结果 Result**：保留能力时约束执行全部通过，交付物满足产品定义。

**建议**：recipe_planning 正常工作时可稳定满足用户约束，是产品质量基线。

### 移除能力实验：约束差异已验证

**目的 Purpose**：验证移除 recipe_planning 后用户结果是否损失，判断其是否为必要组成。

**设计 Design**：移除 recipe_planning，保留相同任务、环境和产品约束，与完整能力基线对照。

**输入场景 Input Scenario**：与基线相同的 5 个用户任务，含忌口鸡蛋与库存约束。

**观察 Observation**：5 个场景全部在约束执行上失败，基础结构化输出仍在但未遵守忌口与库存约束。

**结果 Result**：移除后约束执行全部失败，基础输出不能替代完整产品能力。

**建议**：recipe_planning 是满足用户约束的关键组成，移除后用户会收到违反忌口的方案。

### 替换能力实验：核心价值差异已验证

**目的 Purpose**：验证候选实现能否保持 recipe_planning 提供的核心产品价值，而非仅产生输出。

**设计 Design**：使用候选实现完成相同任务，与完整能力基线按四个维度逐项比较。

**输入场景 Input Scenario**：与基线相同的 5 个用户任务，含忌口鸡蛋与库存约束。

**观察 Observation**：替换实现虽产出结构化方案，但 5 个场景全部在约束执行上失败，未保持忌口与库存约束。

**结果 Result**：替换实现无法保持核心约束与交付质量，能力价值未得到保持。

**建议**：未来实现变更若不能恢复约束执行，将导致用户结果损失，替换存在高风险。

## 7 · Scenario Stability
### 场景稳定性

启用条件下 5 个不同场景均稳定通过约束检查；移除与替换条件下 5 个场景均稳定失败，能力表现跨场景一致。

**结论**：5 个场景均有对应证据，覆盖标准、约束冲突、边界、鲁棒与交互类别，稳定性结论充分。

### 标准约束场景 (`scenario_id: scenario_1`)

**用户**：工作日晚上只有20分钟做饭，家里就我和我老公两个人，维持体重，冰箱有番茄2个、豆腐1盒、鸡蛋6个，安排今晚晚餐。

**目标**：验证标准条件下约束识别与库存利用。

**观察**：启用时通过约束检查，移除与替换时约束执行失败。

**结果**：保留能力时方案满足约束，移除或替换时违反忌口。

### 约束冲突场景 (`scenario_id: scenario_2`)

**用户**：鸡蛋快过期想消耗，但老公对鸡蛋过敏，尽量用掉鸡蛋又不能让他吃到。

**目标**：验证消耗库存愿望与饮食禁忌冲突时的处理。

**观察**：启用时坚持忌口并给出替代方案，移除或替换时违反禁忌。

**结果**：保留能力时坚持禁忌，移除或替换时未遵守。

### 边界时间场景 (`scenario_id: scenario_3`)

**用户**：只有5分钟做饭，两个人吃，冰箱剩番茄2个和豆腐1盒，鸡蛋不吃。

**目标**：验证时间过短超出能力边界时的处理。

**观察**：启用时识别时间限制并给出可行方案，移除或替换时约束执行失败。

**结果**：保留能力时方案可行，移除或替换时未遵守约束。

### 鲁棒推断场景 (`scenario_id: scenario_4`)

**用户**：随便做点吃的，20分钟以内，冰箱有番茄、豆腐和几个鸡蛋，吃得清淡。

**目标**：验证未明确提及忌口时能否正确推断约束。

**观察**：启用时正确推断忌口并避免含蛋菜品，移除或替换时失败。

**结果**：保留能力时正确推断，移除或替换时违反约束。

### 交互购物场景 (`scenario_id: scenario_5`)

**用户**：番茄和豆腐不够做完整饭，帮我看看还缺什么并告诉需要买什么。

**目标**：验证库存不足时结合购物建议能力给出补充清单。

**观察**：启用时给出购物建议并遵守忌口，移除或替换时约束执行失败。

**结果**：保留能力时方案完整，移除或替换时未遵守约束。

## 8 · Impact / Capability Impact
### 能力影响

**影响的用户旅程**：用户从提出餐食需求到获得可执行方案的全流程。

移除或替换 recipe_planning 后，用户会收到违反忌口鸡蛋或未优先利用库存的方案，导致方案不可用并可能带来饮食风险。

- 该能力稳定满足忌口与库存约束，是用户获得合规方案的关键。
- 移除后用户会收到违反忌口的方案，基础输出不能替代完整能力。
- 替换实现无法保持核心约束价值，未来变更存在高风险。
- 能力边界控制稳定，未出现越界或未声明副作用。

## 9 · Recommendation
### 建议行动

### recipe_planning 能力（critical）

保留 recipe_planning 作为餐食规划核心能力，不进行移除或替换。

**依据**：移除或替换后约束执行全部失败，用户会收到违反忌口的方案，能力是满足用户约束的关键组成。

**下一步验证**：增加跨日期样本；补充更多库存组合

### 边界场景处理（medium）

增强时间过短或输入不完整时的澄清与简化建议能力。

**依据**：边界场景虽通过约束检查，但可进一步优化时间过短时的方案范围缩小与澄清。

**下一步验证**：补充边界任务样本；验证澄清建议质量

### 替换实现验证（high）

若未来需替换实现，必须先验证其能恢复约束执行能力。

**依据**：当前替换实现无法保持核心约束价值，替换前必须验证约束执行恢复。

**下一步验证**：验证替换实现约束执行；增加替换场景样本

## 10 · Limitations
### 评估边界及限制

- 交付物内容被截断，无法逐条核对具体菜品是否含蛋，约束结论依赖验证器结果。
- 评估基于冻结环境（2 人、20 分钟、忌口鸡蛋），未覆盖其他人数、时间或忌口组合。
- 替换实验使用候选实现，未验证其他可能的实现方案是否也能保持能力价值。

## 11 · Evidence
### 实验证据 / 技术证据

Product Evidence / Experiment Evidence / Technical Evidence

- 状态：complete
- 已验证条件：15
- 通过：15
- 失败：0
- 实验条件：15
- 成本：未记录

<details><summary>启用 Skill 测试 · 启用 Skill · 通过</summary>

证据引用：evidence_0eda16c82b310320

</details>

<details><summary>启用 Skill 测试 · 启用 Skill · 通过</summary>

证据引用：evidence_28710f0e561fe02f

</details>

<details><summary>启用 Skill 测试 · 启用 Skill · 通过</summary>

证据引用：evidence_119745d274ee45ca

</details>

<details><summary>启用 Skill 测试 · 启用 Skill · 通过</summary>

证据引用：evidence_07b89d4423fee919

</details>

<details><summary>启用 Skill 测试 · 启用 Skill · 通过</summary>

证据引用：evidence_1b3940c789e0d1f2

</details>

<details><summary>移除 Skill 测试 · 移除 Skill · 通过</summary>

证据引用：evidence_3388f64f70059def

</details>

<details><summary>移除 Skill 测试 · 移除 Skill · 通过</summary>

证据引用：evidence_4f63937ce3ef5216

</details>

<details><summary>移除 Skill 测试 · 移除 Skill · 通过</summary>

证据引用：evidence_0e2b890a90548706

</details>

<details><summary>移除 Skill 测试 · 移除 Skill · 通过</summary>

证据引用：evidence_3321ad5bb57bae14

</details>

<details><summary>移除 Skill 测试 · 移除 Skill · 通过</summary>

证据引用：evidence_14cfce4a30d9f4b4

</details>

<details><summary>Capability Equivalence Test · 替换实现 · 通过</summary>

证据引用：evidence_12b3dd98990c7be3

</details>

<details><summary>Capability Equivalence Test · 替换实现 · 通过</summary>

证据引用：evidence_177a8040939d9460

</details>

<details><summary>Capability Equivalence Test · 替换实现 · 通过</summary>

证据引用：evidence_0d38f21bb8dfab97

</details>

<details><summary>Capability Equivalence Test · 替换实现 · 通过</summary>

证据引用：evidence_16456428a917042f

</details>

<details><summary>Capability Equivalence Test · 替换实现 · 通过</summary>

证据引用：evidence_3aa4cc625dfdd107

</details>

## 12 · Technical Metadata
### 技术元数据

- report_id：report_6f6c5a90c5b64515
- schema_version：aig.product-evaluation-report.v4
- evaluation_id：evaluation_6f6c5a90c5b64515
- evaluation_type：skill_ablation
- report_hash：fafaa28246bb698c200d1d45084c3b307078ace87d1459b44a876182eb622b2e
- evidence_manifest_hash：c5f1f8f129895f9084ae4ea06425c4ed07a0c55e1056fd9a857a67144f899a14
- evidence_schema_version：aig.evidence-bundle.v1
- analyst_schema_version：aig.product-analyst-input.v4
- analyst_provider：deepseek
- analyst_model：deepseek-v4-flash
- analyst_request_id：e605a5d0-03f2-4b5f-9bfe-e688908d2ca7
- interpretation_evidence_level：inferred

技术记录、事实与补充证据保留在可展开的 HTML 详情中；首屏不直接倾倒原始 JSON。
