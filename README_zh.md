<div align="center">

<img src="https://img.shields.io/badge/%E2%96%87%20PixelFerry-%E8%A7%86%E8%A7%89%E6%95%B0%E6%8D%AE%E9%80%9A%E9%81%93-blueviolet?style=for-the-badge&labelColor=1a1a2e" alt="PixelFerry"/>

<br/>

<table><tr>
<td><code>#FF0000</code></td>
<td><code>#00FF00</code></td>
<td><code>#0000FF</code></td>
<td><code>#FFFF00</code></td>
</tr></table>

**基于 RGB 色块编码的视觉数据传输**

[English](README.md) | 中文

[![License](https://img.shields.io/badge/License-Apache%202.0-blue.svg)](LICENSE)
[![Python](https://img.shields.io/badge/Python-3.9+-yellow.svg)](https://python.org)

</div>

---

## 简介

PixelFerry 将二进制数据编码为屏幕上的 RGB 色块。接收端捕获画面、检测四角标记、透视校正后解码还原 —— 全程无需网络、剪贴板或共享存储。

每个像素块的 R/G/B 通道各编码 **4 bit**，使用 16 档离散颜色值：

```
档位:    0    1    2    3   ...   12   13   14   15
数值:    8   24   40   56   ...  200  216  232  248

每块 3 nibble = 12 bit = 1.5 字节
1200 x 800 窗口 -> 46 x 29 网格 -> 每帧 ~9 KB
```

## 工作原理

```
发送端                              接收端
------                              ------

 代码仓库                              屏幕截图
     |                                      |
 打包 .pxf                               定位角点
     |                                      |
 分片分帧                               解码色块
     |                                      |
 编码为帧                       视觉     SHA-256 校验
     |                           通道         |
 显示窗口  ------>  截屏          解包还原
 (循环播放)                           |
                                 输出数据
```

1. **发送端**将仓库打包为 `.pxf`，分片编码为 RGB 帧，在 tkinter 窗口中显示
2. **接收端**捕获屏幕，检测四角定位标记（黄/红/绿/蓝），透视变换校正后解码色块
3. **二维码**携带会话元数据（会话 ID、帧数、SHA-256、仓库名），实现零配置配对

## 快速开始

```bash
# 安装
pip install -e .

# 发送仓库
pixelferry send /path/to/repo

# 接收（在另一台机器上，对准发送端窗口）
pixelferry receive
```

### 使用别名

```bash
pixelferry config set myproject /home/user/myproject
pixelferry send myproject --fps 5
```

## 技术参考

<table>
<tr>
<td>

**帧布局**

| 参数 | 值 |
|---|---|
| 窗口大小 | 1200 × 800 px |
| 色块大小 | 24 × 24 px |
| 网格 | 46 列 × 29 行 |
| 帧头 | 128 字节 |
| 每帧载荷 | ~9 KB |

</td>
<td>

**颜色编码**

| Nibble | 通道值 |
|---|---|
| `0x0` | 8 |
| `0x1` | 24 |
| `0x2` | 40 |
| `⋮` | `⋮` |
| `0xE` | 232 |
| `0xF` | 248 |

</td>
</tr>
</table>

### 四角定位标记

窗口四角的彩色标记用于自动透视校正：

```
  黄色                           红色
  +-----+--------------------+-----+
  |#####|                    |#####|
  +-----+                    +-----+
  |                             |
  |          数据区域           |
  |                             |
  +-----+                    +-----+
  |#####|                    |#####|
  +-----+--------------------+-----+
  绿色                           蓝色
```

| 位置 | 颜色 | RGB |
|---|---|---|
| 左上角 | 黄色 | `(255, 255, 0)` |
| 右上角 | 红色 | `(255, 0, 0)` |
| 左下角 | 绿色 | `(0, 255, 0)` |
| 右下角 | 蓝色 | `(0, 0, 255)` |

### 帧头结构（128 字节）

```
偏移:   0         4   5   7         23        31        35       67       99    128
        +---------+---+---+---------+---------+---------+--------+--------+-----+
        |  PXF1   |v|hln| 会话ID (16字节)     |帧序号    |总帧数   |载荷    | 包  |
        |  magic  |e |   |         |          |         |_长度   |_sha256 | sha |
        +---------+---+---+---------+---------+---------+--------+--------+-----+
```

## 安全说明

```
 ✓  仅读取用户指定的目录
 ✓  默认排除 .git、node_modules、.venv 等大目录
 ✓  防止路径穿越攻击（拒绝 .. 和绝对路径）
 ✓  每帧 + 每包 SHA-256 完整性校验
 ✗  无网络连接
 ✗  无第三方服务
 ✗  不访问剪贴板
```

## 平台支持

| 平台 | 模式 | 说明 |
|---|---|---|
| **Windows** | 实时捕获 | 通过 PrintWindow API 完整支持屏幕捕获 |
| **macOS / Linux** | PNG 流水线 | 生成帧图片 → 传输 → 解码 |

## 项目结构

```
pixelferry/
├── codec.py           # RGB nibble 编码/解码
├── framing.py         # 帧头构建与校验
├── manifest.py        # 仓库清单构建与解析
├── package.py         # .pxf 包构建与解包
├── sender.py          # 帧生成与显示窗口
├── receiver.py        # 接收循环与帧收集
├── corner_detect.py   # 角点检测与透视变换
├── qr_detect.py       # 二维码检测与会话初始化
├── capture.py         # 屏幕捕获与窗口定位
├── config.py          # 路径别名配置
├── verify.py          # 重建完整性校验
└── utils.py           # 哈希、路径安全、文件检测
```

## 许可证

[Apache 2.0](LICENSE)
