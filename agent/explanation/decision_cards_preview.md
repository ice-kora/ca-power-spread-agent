# Decision Card 预览（test 窗口）

> 全部为真实日期/节点/小时；Evidence 全 UNCERTAIN；方向由 Rule Engine/交易员决定。


### CONTROLX_1_N001 · HE14 · 2026-06-30 (决策日 2026-06-29) · source=rule
Node CONTROLX · HE14 · Expected +185.9 · Confidence 0.77 · Decision SELL_DA
依据: 同节点HE14近30日Return偏正(+208.5)；负荷预测19333低于节点均值23481；近30日该小时波动±338(较高)；决策时spread_lag1处于历史21%分位；同节点HE14历史样本n=910
Evidence: 全部 UNCERTAIN（8 类数据源未接入：CAISO_MARKET_NOTICE, EXTREME_WEATHER, FUEL_PRICE, LOAD_FORECAST_REVISION, OTHER, OUTAGE_AND_CONSTRAINT, RENEWABLE_GENERATION, WILDFIRE）；未发现可核实的外部事件
RiskGate: PASS_WITH_WARNING(EXTREME_TAIL_NODE)
  RiskGate 说明: CONTROLX 为已知重尾节点（历史 ±900~3656）；R4 已验证无法用历史尾部事前识别具体单日，仅作警告。
风险: CONTROLX 为已知重尾节点，单笔尾部可达千级 $/MWh；CONTROLX SELL 存在 DA 崩塌尾（如 06-30 DA -1313），Risk Gate 不覆盖；近30日该小时波动较高(±338)
建议: 可考虑执行 SELL_DA，但需人工复核风险警告（EXTREME_TAIL_NODE）。
注意: 最终执行由交易员确认

### CONTROLX_1_N001 · HE8 · 2026-06-30 (决策日 2026-06-29) · source=rule
Node CONTROLX · HE8 · Expected +333.9 · Confidence 0.94 · Decision SELL_DA
依据: 同节点HE8近30日Return偏正(+271.0)；负荷预测23567接近节点均值23481；近30日该小时波动±401(很高)；决策时spread_lag1处于历史88%分位；同节点HE8历史样本n=910
Evidence: 全部 UNCERTAIN（8 类数据源未接入：CAISO_MARKET_NOTICE, EXTREME_WEATHER, FUEL_PRICE, LOAD_FORECAST_REVISION, OTHER, OUTAGE_AND_CONSTRAINT, RENEWABLE_GENERATION, WILDFIRE）；未发现可核实的外部事件
RiskGate: PASS_WITH_WARNING(EXTREME_TAIL_NODE)
  RiskGate 说明: CONTROLX 为已知重尾节点（历史 ±900~3656）；R4 已验证无法用历史尾部事前识别具体单日，仅作警告。
风险: CONTROLX 为已知重尾节点，单笔尾部可达千级 $/MWh；CONTROLX SELL 存在 DA 崩塌尾（如 06-30 DA -1313），Risk Gate 不覆盖；近30日该小时波动较高(±401)
建议: 可考虑执行 SELL_DA，但需人工复核风险警告（EXTREME_TAIL_NODE）。
注意: 最终执行由交易员确认

### CONTROLX_1_N001 · HE2 · 2026-07-09 (决策日 2026-07-08) · source=rule
Node CONTROLX · HE2 · Expected +180.3 · Confidence 0.82 · Decision SELL_DA
依据: 同节点HE2近30日Return偏正(+178.5)；负荷预测26177高于节点均值23481；近30日该小时波动±332(较高)；决策时spread_lag1处于历史61%分位；同节点HE2历史样本n=919
Evidence: 全部 UNCERTAIN（8 类数据源未接入：CAISO_MARKET_NOTICE, EXTREME_WEATHER, FUEL_PRICE, LOAD_FORECAST_REVISION, OTHER, OUTAGE_AND_CONSTRAINT, RENEWABLE_GENERATION, WILDFIRE）；未发现可核实的外部事件
RiskGate: PASS_WITH_WARNING(EXTREME_TAIL_NODE)
  RiskGate 说明: CONTROLX 为已知重尾节点（历史 ±900~3656）；R4 已验证无法用历史尾部事前识别具体单日，仅作警告。
风险: CONTROLX 为已知重尾节点，单笔尾部可达千级 $/MWh；CONTROLX SELL 存在 DA 崩塌尾（如 06-30 DA -1313），Risk Gate 不覆盖；近30日该小时波动较高(±332)
建议: 可考虑执行 SELL_DA，但需人工复核风险警告（EXTREME_TAIL_NODE）。
注意: 最终执行由交易员确认

### SNLNDRO_1_N001 · HE19 · 2026-06-02 (决策日 2026-06-01) · source=rule
Node SNLNDRO · HE19 · Expected +4.2 · Confidence 0.78 · Decision SELL_DA
依据: 同节点HE19近30日Return中性(+3.2)；负荷预测29900高于节点均值23481；近30日该小时波动±12(较低)；决策时spread_lag1处于历史58%分位；同节点HE19历史样本n=882
Evidence: 全部 UNCERTAIN（8 类数据源未接入：CAISO_MARKET_NOTICE, EXTREME_WEATHER, FUEL_PRICE, LOAD_FORECAST_REVISION, OTHER, OUTAGE_AND_CONSTRAINT, RENEWABLE_GENERATION, WILDFIRE）；未发现可核实的外部事件
RiskGate: PASS(NONE)
建议: 可按模型建议执行 SELL_DA（Risk Gate PASS）。
注意: 最终执行由交易员确认

### SNLNDRO_1_N001 · HE7 · 2026-06-02 (决策日 2026-06-01) · source=rule
Node SNLNDRO · HE7 · Expected +6.4 · Confidence 0.65 · Decision SELL_DA
依据: 同节点HE7近30日Return中性(+6.7)；负荷预测23217接近节点均值23481；近30日该小时波动±12(较低)；决策时spread_lag1处于历史62%分位；同节点HE7历史样本n=882
Evidence: 全部 UNCERTAIN（8 类数据源未接入：CAISO_MARKET_NOTICE, EXTREME_WEATHER, FUEL_PRICE, LOAD_FORECAST_REVISION, OTHER, OUTAGE_AND_CONSTRAINT, RENEWABLE_GENERATION, WILDFIRE）；未发现可核实的外部事件
RiskGate: PASS(NONE)
建议: 可按模型建议执行 SELL_DA（Risk Gate PASS）。
注意: 最终执行由交易员确认

### ELCAJNGT_7_N001 · HE20 · 2026-06-05 (决策日 2026-06-04) · source=rule
Node ELCAJNGT_7_N001 · HE20 · Expected +9.4 · Confidence 1.00 · Decision SELL_DA
依据: 同节点HE20近30日Return中性(+3.2)；负荷预测32154高于节点均值21686；近30日该小时波动±13(较低)；决策时spread_lag1处于历史79%分位；同节点HE20历史样本n=92
Evidence: 全部 UNCERTAIN（8 类数据源未接入：CAISO_MARKET_NOTICE, EXTREME_WEATHER, FUEL_PRICE, LOAD_FORECAST_REVISION, OTHER, OUTAGE_AND_CONSTRAINT, RENEWABLE_GENERATION, WILDFIRE）；未发现可核实的外部事件
RiskGate: REJECT(LOW_SAMPLE_SUPPORT)
  RiskGate 说明: 同节点HE20历史样本仅 92（<200，cold-start），统计不可靠。
风险: ELCA 历史样本少（n<200，cold-start），统计不可靠；Risk Gate 判定 REJECT，建议不执行该方向
建议: 不建议执行 SELL_DA（Risk Gate REJECT: LOW_SAMPLE_SUPPORT）。若必须交易，需人工复核并显著降仓。
注意: 最终执行由交易员确认

### CONTROLX_1_N001 · HE1 · 2026-07-01 (决策日 2026-06-30) · source=rule
Node CONTROLX · HE1 · Expected +116.4 · Confidence 0.25 · Decision NO_TRADE
依据: 同节点HE1近30日Return偏正(+193.3)；负荷预测24763高于节点均值23481；近30日该小时波动±349(较高)；决策时spread_lag1处于历史8%分位；同节点HE1历史样本n=911
Evidence: 全部 UNCERTAIN（8 类数据源未接入：CAISO_MARKET_NOTICE, EXTREME_WEATHER, FUEL_PRICE, LOAD_FORECAST_REVISION, OTHER, OUTAGE_AND_CONSTRAINT, RENEWABLE_GENERATION, WILDFIRE）；未发现可核实的外部事件
RiskGate: PASS_WITH_WARNING(EXTREME_TAIL_NODE)
  RiskGate 说明: CONTROLX 为已知重尾节点（历史 ±900~3656）；R4 已验证无法用历史尾部事前识别具体单日，仅作警告。
风险: CONTROLX 为已知重尾节点，单笔尾部可达千级 $/MWh；近30日该小时波动较高(±349)
建议: 可考虑执行 NO_TRADE，但需人工复核风险警告（EXTREME_TAIL_NODE）。
注意: 最终执行由交易员确认

### SNLNDRO_1_N001 · HE14 · 2026-08-03 (决策日 2026-08-02) · source=rule
Node SNLNDRO · HE14 · Expected -4.3 · Confidence 0.66 · Decision BUY_DA
依据: 同节点HE14近30日Return中性(-5.4)；负荷预测35910高于节点均值23481；近30日该小时波动±33(较低)；决策时spread_lag1处于历史56%分位；同节点HE14历史样本n=944
Evidence: 全部 UNCERTAIN（8 类数据源未接入：CAISO_MARKET_NOTICE, EXTREME_WEATHER, FUEL_PRICE, LOAD_FORECAST_REVISION, OTHER, OUTAGE_AND_CONSTRAINT, RENEWABLE_GENERATION, WILDFIRE）；未发现可核实的外部事件
RiskGate: PASS(NONE)
建议: 可按模型建议执行 BUY_DA（Risk Gate PASS）。
注意: 最终执行由交易员确认

### CONTROLX_1_N001 · HE3 · 2026-07-17 (决策日 2026-07-16) · source=interpretable
Node CONTROLX · HE3 · Expected -67.8 · Confidence 0.56 · Decision BUY_DA
依据: 同节点HE3近30日Return偏正(+125.0)；负荷预测26521高于节点均值23481；近30日该小时波动±387(较高)；决策时spread_lag1处于历史11%分位；同节点HE3历史样本n=924；来源模型=interpretable
Evidence: 全部 UNCERTAIN（8 类数据源未接入：CAISO_MARKET_NOTICE, EXTREME_WEATHER, FUEL_PRICE, LOAD_FORECAST_REVISION, OTHER, OUTAGE_AND_CONSTRAINT, RENEWABLE_GENERATION, WILDFIRE）；未发现可核实的外部事件
RiskGate: REJECT(BUY_ON_POSITIVE_DRIFT_NODE)
  RiskGate 说明: CONTROLX 无条件漂移 +9.68，BUY 逆漂移且尾极深（train+val 验证，见 risk_gate_design R7a）。
风险: CONTROLX 为已知重尾节点，单笔尾部可达千级 $/MWh；CONTROLX BUY 逆 +84 漂移，train+val 无条件负期望；近30日该小时波动较高(±387)；Risk Gate 判定 REJECT，建议不执行该方向
建议: 不建议执行 BUY_DA（Risk Gate REJECT: BUY_ON_POSITIVE_DRIFT_NODE）。若必须交易，需人工复核并显著降仓。
注意: 最终执行由交易员确认

### CONTROLX_1_N001 · HE19 · 2026-06-30 (决策日 2026-06-29) · source=interpretable
Node CONTROLX · HE19 · Expected -136.3 · Confidence 0.95 · Decision BUY_DA
依据: 同节点HE19近30日Return偏正(+124.7)；负荷预测28645高于节点均值23481；近30日该小时波动±309(较高)；决策时spread_lag1处于历史4%分位；同节点HE19历史样本n=910；来源模型=interpretable
Evidence: 全部 UNCERTAIN（8 类数据源未接入：CAISO_MARKET_NOTICE, EXTREME_WEATHER, FUEL_PRICE, LOAD_FORECAST_REVISION, OTHER, OUTAGE_AND_CONSTRAINT, RENEWABLE_GENERATION, WILDFIRE）；未发现可核实的外部事件
RiskGate: REJECT(BUY_ON_POSITIVE_DRIFT_NODE)
  RiskGate 说明: CONTROLX 无条件漂移 +9.68，BUY 逆漂移且尾极深（train+val 验证，见 risk_gate_design R7a）。
风险: CONTROLX 为已知重尾节点，单笔尾部可达千级 $/MWh；CONTROLX BUY 逆 +84 漂移，train+val 无条件负期望；近30日该小时波动较高(±309)；决策时 spread 处于历史极值分位(4%)；Risk Gate 判定 REJECT，建议不执行该方向
建议: 不建议执行 BUY_DA（Risk Gate REJECT: BUY_ON_POSITIVE_DRIFT_NODE）。若必须交易，需人工复核并显著降仓。
注意: 最终执行由交易员确认

### CONTROLX_1_N001 · HE5 · 2026-06-26 (决策日 2026-06-25) · source=interpretable
Node CONTROLX · HE5 · Expected -114.8 · Confidence 0.97 · Decision BUY_DA
依据: 同节点HE5近30日Return偏正(+45.2)；负荷预测22386低于节点均值23481；近30日该小时波动±273(较高)；决策时spread_lag1处于历史20%分位；同节点HE5历史样本n=906；来源模型=interpretable
Evidence: 全部 UNCERTAIN（8 类数据源未接入：CAISO_MARKET_NOTICE, EXTREME_WEATHER, FUEL_PRICE, LOAD_FORECAST_REVISION, OTHER, OUTAGE_AND_CONSTRAINT, RENEWABLE_GENERATION, WILDFIRE）；未发现可核实的外部事件
RiskGate: REJECT(BUY_ON_POSITIVE_DRIFT_NODE)
  RiskGate 说明: CONTROLX 无条件漂移 +9.68，BUY 逆漂移且尾极深（train+val 验证，见 risk_gate_design R7a）。
风险: CONTROLX 为已知重尾节点，单笔尾部可达千级 $/MWh；CONTROLX BUY 逆 +84 漂移，train+val 无条件负期望；近30日该小时波动较高(±273)；Risk Gate 判定 REJECT，建议不执行该方向
建议: 不建议执行 BUY_DA（Risk Gate REJECT: BUY_ON_POSITIVE_DRIFT_NODE）。若必须交易，需人工复核并显著降仓。
注意: 最终执行由交易员确认

### ELCAJNGT_7_N001 · HE20 · 2026-07-24 (决策日 2026-07-23) · source=interpretable
Node ELCAJNGT_7_N001 · HE20 · Expected +57.5 · Confidence 0.97 · Decision SELL_DA
依据: 同节点HE20近30日Return偏负(-50.4)；负荷预测40537高于节点均值21686；近30日该小时波动±156(中等)；决策时spread_lag1处于历史0%分位；同节点HE20历史样本n=141；来源模型=interpretable
Evidence: 全部 UNCERTAIN（8 类数据源未接入：CAISO_MARKET_NOTICE, EXTREME_WEATHER, FUEL_PRICE, LOAD_FORECAST_REVISION, OTHER, OUTAGE_AND_CONSTRAINT, RENEWABLE_GENERATION, WILDFIRE）；未发现可核实的外部事件
RiskGate: REJECT(LOW_SAMPLE_SUPPORT)
  RiskGate 说明: 同节点HE20历史样本仅 141（<200，cold-start），统计不可靠。
风险: ELCA 历史样本少（n<200，cold-start），统计不可靠；决策时 spread 处于历史极值分位(0%)；Risk Gate 判定 REJECT，建议不执行该方向
建议: 不建议执行 SELL_DA（Risk Gate REJECT: LOW_SAMPLE_SUPPORT）。若必须交易，需人工复核并显著降仓。
注意: 最终执行由交易员确认
