# 官方资料与采用范围

检索日期：2026-09-03。

本次研究没有把项目内已有计算文档当作公式来源。项目代码只用于提取当前字段和复核现有输出；保护原理、TAP、矩阵、Id/Ir 差异及试验边界来自以下厂家或标准机构的一手资料。

## 1. SEL：TAP、矩阵和补偿顺序

[Beyond the Nameplate – Selecting Transformer Compensation Settings for Secure Differential Protection](https://selinc.com/api/download/114458/id/116990/0/)

- PDF 第 2～4 页：额定电流、TAP、标幺化后再做相位/零序补偿；
- PDF 第 5 页：Matrix 1 的明确系数；
- PDF 第 7～8 页：正常穿越补偿后两侧等幅、相差 180°；铭牌钟点不足以单独确定矩阵；
- PDF 第 15 页 Appendix A：Matrix 0…12 完整表；
- PDF 第 17 页 Appendix C：实际向量组补偿示例。

[Stop the Epidemic! Transformer Protection Misoperations](https://selinc.com/api/download/135495/?lang=en)

- PDF 第 2 页：一种 `Iop` 和比例制动量定义；
- PDF 第 3 页：TAP 公式和 Dyn1 的 M1 示例；
- PDF 第 8、14～15 页：相序、极性、一次/二次注入边界及正常穿越关系。

[Power Transformer Protection Improvements With Numerical Relays](https://selinc.com/api/download/3184/?lang=en)

- 正文第 3 页：`IOP=|IW1+IW2|` 和多种 `IRT` 定义；
- 正文第 6～10 页：谐波制动、五次闭锁及独立/公共闭锁差异。

[Percentage Restrained Differential, Percentage of What?](https://selinc.com/api/download/8484/)

- PDF 第 8 页：制动量既可能是各侧幅值的缩放和，也可能取最大值；
- 用途：证明 Ir 不是跨厂家唯一公式。

[Single-Phase Testing of Transformer Differential Relays](https://na.eventscloud.com/file_uploads/aed63aa3aaa969796648402644f9ddfa_AG2010-09_20150622.pdf)

- 第 4 页：原始电流经 TAP 和补偿后再进入 IOP/IRT；
- 第 5～6 页：非零矩阵的单相注入会使其他保护元件得到电流，部分矩阵需要 $\sqrt3$ 或 1.5 系数；
- 第 7 页：平均制动公式下一组单相反求关系。

## 2. ABB：逐相差动和显式零序扣除

[ABB 615 Series Technical Manual, 1MRS756887 G](https://library.e.abb.com/public/389adf763fb4f46ac1257c7b00438810/RE_615_tech_756887_ENg.pdf)

- 正文第 334 页：电流正方向指向保护对象，`Id=|IW1+IW2|`、`Ib=|IW1-IW2|/2`；
- 正文第 336 页：逐相扣除三相平均值的零序投影；
- 用途：本库 `EquationConfig.abb_615()` 的官方示例来源。

## 3. GE：参考电流、幅值匹配和厂家特性差异

[GE Vernova P50 Agile P652 Technical Manual, Rev. C](https://www.gevernova.com/grid-solutions/products/manuals/P652-EN-M-C-GEV.pdf)

- Chapter 6 §2.3、§2.9.5：补偿后差流、幅值和型制动量及多折线特性；
- Chapter 6 §2.4～2.5、§2.9.2～2.9.4：Yd1/Yd11 方向、参考电流、匹配系数、相位及零序补偿；
- Chapter 6 §2.9.5.2～2.9.5.3：二/五次谐波和交叉闭锁属于 DUT 专有逻辑。

## 4. Siemens：参考基准、厂家矩阵和零序/REF

[SIPROTEC 4 7UT6x Manual](https://cache.industry.siemens.com/dl/files/415/109743415/att_903739/v1/7UT6x_Manual_A5_V040400_en.pdf)

- 正文第 101 页：按额定容量、电压和匹配系数统一各侧基准；
- 正文第 103 页：Yd5 的厂家矩阵示例；
- 正文第 106 页：无中性线测量时的零序消除及灵敏度影响；
- 用途：证明不同厂家矩阵编号、缩放和补偿分配不能混用。

[SIPROTEC 4 7UT613/63x Manual](https://cache.industry.siemens.com/dl/files/422/109743422/att_961594/v1/7UT613x_Manual_A2_V046004_en.pdf)

- 正文第 116 页：相电流残流和中性点 CT 的关系；
- 正文第 133 页：制动和谐波交叉闭锁示例。

[SIPROTEC 4 7UM62 Manual](https://cache.industry.siemens.com/dl/files/486/109743486/att_959435/v1/7UM62_Manual_AC_V991200_en.pdf)

- 正文第 125 页：接地差动 `I0-Diff`、`I0-Stab` 的一种定义；
- 用途：说明零序差动/REF 不是相差动的 `zeroSequenceCorrection` 布尔开关。

## 5. OMICRON：专业试验所需参数和 6I 边界

[Testing Transformer Differential Protection](https://www.omicronenergy.com/uploads/tx_downloader/65f90/2020-NA-Protection-Article-Testing_Transformer_Differential_Protection.pdf)

- 第 6～12 页：变比、接线组别、CT 比/星点、相位/零序补偿和厂家 bias 差异；
- 第 16～19 页：完整 Test Object 必须包含 MVA、kV、CT、参考侧、bias 和零序方法；
- 第 24 页：6I 是侧 1 A/B/C 加侧 2 A/B/C；
- 第 28～33 页：配置/外部故障校验和 A/B/C/AB/BC/CA/ABC fault type；理论 Id/Ib 必须来自 DUT 手册；
- 第 37～39 页：谐波试验还需要基波差流、百分比、次数、相别及前后状态。

## 6. 昂立：七种 6I 相别和模板边界

[ONLLY-A 系列用户手册下载页](https://www.anglielectric.com/sms/466.html)

[ONLLY-A V8.64 用户手册 PDF](https://www.anglielectric.com/Upload/download/1651479749.pdf)

- 正文约第 145 页：应按被测装置说明书选择动作/制动方程或型号模板，K、KP1、KP2 与所选方程和失配修正有关；
- 正文约第 148～150 页：扩展差动把 A/B/C、AB/BC/CA、ABC 列为七个 6I 相别；谐波还含 Id、百分比、2～20 次次数、角度、基频和施加侧；
- 手册没有公开可跨保护厂家复制的七组完整六路相量公式。

## 7. 工频变化量公开原理

[NREC: Deviation of Power Frequency Component](https://www.nrec.com/en/index.php/about/InnovationInfo/2.html)

- 说明故障后量可分解为故障前负荷分量和故障附加分量，DPFC 使用后者。

[NREC Protection Automation Control brochure](https://www.nrec.com/en/web/upload/2019/05/08/15572769074495czyjk.pdf)

- 第 13 页：DPFC 百分差动还包含动态充电补偿、自适应制动和 CT 饱和识别。

[NREC PCS-978 product page](https://www.nrec.com/en/index.php/product/productInfo/42.html)

- 说明实际装置同时包含常规相差动、带制动 DPFC 和 REF；公开材料不足以重建采样窗和记忆算法。

## 8. 标准范围

- [IEC 60255-187-1:2021](https://webstore.iec.ch/en/publication/32570)：变压器差动功能、性能和试验范围；
- [IEC 60152:2021](https://webstore.iec.ch/en/publication/66253)：钟点/相位差标识；
- [IEEE C37.91-2021](https://standards.ieee.org/ieee/C37.91/5904/)：电力变压器保护应用指南。

这些标准页面用于确认术语和适用范围；公开摘要没有给出本库使用的完整矩阵正文，所以没有把矩阵公式错误归因给标准。

