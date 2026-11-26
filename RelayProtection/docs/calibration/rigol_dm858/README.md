# RIGOL DM858 官方手册

本目录保存本次校准方案核对所使用的官方 PDF。DM858 是数字万用表，不是示波器。本次没有连接仪表、改变仪表设置或驱动设备输出。

| 本地文件 | 官方下载地址 | PDF 页数 | 文件字节数 |
| --- | --- | ---: | ---: |
| [DM858 中文用户手册](DM858_UserGuide_CN.pdf) | [RIGOL 官方用户手册](https://www.rigol.com/dam/global/downloads/brochures/zh/user-manual/multimeters/DM858_UserGuide_CN.pdf) | 137 | 12082644 |
| [DM858 中文编程手册](DM858_ProgrammingGuide_CN.pdf) | [RIGOL 官方编程手册](https://download.rigol.com/cn/Manual/Multimeters/DM858/DM858_ProgrammingGuide_CN.pdf) | 148 | 2797057 |
| [DM858 中文数据手册](DM858_DataSheet_CN.pdf) | [RIGOL 官方数据手册](https://www.rigol.com/dam/global/downloads/brochures/zh/data-sheet/multimeters/DM858_DataSheet_CN.pdf) | 19 | 8898762 |

官方产品入口：[RIGOL DM858 产品页](https://www.rigol.com/zh_CN/products/multimeter/DM858.html)。

## 本次使用的章节

下面的页码是手册正文印刷页码，不是 PDF 阅读器的文件页序号。

- 编程手册：3.6、3.7，读数与采样；3.10，测量功能/量程；3.13，通用命令；3.17.18、3.17.63，直流积分时间；3.17.12、3.17.21、3.17.57，交流辅助频率测量；3.19.15，仪器错误队列；3.20，触发控制。
- 用户手册：第 9 章，正文 113～117 页，预热条件、交流精度、频率范围和建立时间。
- 数据手册：交流特性及电流输入限制，用于交叉核对用户手册。

具体代码审查结果和改造建议见项目根目录的[校准逻辑审查与多频点幅值校准改造方案](../../../../校准逻辑审查与多频点幅值校准改造方案.md)。手册描述的是仪表能力；当前连接设备的型号、序列号、固件和实际命令响应仍需要实施时读取确认。

## 文件校验

SHA-256 仅用于识别本次下载文件，不是厂商提供的数字签名。

```text
DM858_UserGuide_CN.pdf
f9dec6de6c3bca0d8594799f0d6c7e8766558b92e3f8a7f1336cac7e0430a0a1

DM858_ProgrammingGuide_CN.pdf
71fe672a6dc34fc19db6c760b91c8a0e29ea2898518b694286603dd69d7c97d6

DM858_DataSheet_CN.pdf
7f753ff653c64481491a180b89bc13465b34c8f459a94cdeb6a56d1a5509d838
```
