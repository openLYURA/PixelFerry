你现在要设计并实现一个名为 PixelFerry 的研究型离线代码仓库传输原型。

项目定位：
PixelFerry 是一个用于授权远程桌面环境的离线仓库搬运工具。它的目标是在云电脑和本地电脑无法通过网络互通、云电脑不能写入本地磁盘、剪贴板不适合长期占用的情况下，通过远程桌面画面建立一条单向视觉数据通道，把用户自己的代码仓库从云电脑传回本地电脑。

重要边界：
本项目仅用于合法授权环境下转移用户自己的代码仓库，用于备份、迁移和继续开发。不要设计恶意软件、隐蔽驻留、凭据收集、权限提升、绕过认证、绕过安全策略、逃避检测、未授权访问、未授权数据获取或任何攻击性功能。不要使用网络外连，不要上传到第三方服务，不要读取用户指定仓库之外的数据。所有输入路径必须由用户显式指定。

核心思想：
云电脑端将代码仓库整理成一个可还原的文本化仓库包，然后切分为多个分片。每个分片被编码为一帧 RGB 色块图，显示在云电脑屏幕上的一个小窗口中。本地端定时截图这个窗口区域，读取 RGB 色块，还原分片，校验通过后保存。云电脑端循环播放所有帧，本地端通过重复接收补齐丢失或损坏的帧。全部分片收齐后，本地端合并数据，校验总哈希，根据 manifest 清单重建完整代码仓库。

项目名称：
PixelFerry

中文名：
像素摆渡

推荐目录结构：
pixelferry/
README.md
pixelferry/
 **init** .py
manifest.py
package.py
framing.py
codec.py
sender.py
receiver.py
verify.py
utils.py
scripts/
make_package.py
play_frames.py
receive_frames.py
unpack_package.py
tests/
test_manifest.py
test_codec.py
test_framing.py
test_package_roundtrip.py

请优先实现一个最小可运行原型，不追求最高速度，优先保证稳定、可校验、可恢复。

一、仓库打包方案

云电脑端输入一个仓库目录 repo_path，输出一个文本化仓库包 package.pxf。

打包规则：

1. 只扫描用户指定目录内部。
2. 默认排除常见大目录和缓存目录：
   * .git
   * node_modules
   * .venv
   * venv
   * **pycache**
   * dist
   * build
   * .next
   * .cache
   * target
   * out
   * coverage
3. 支持用户配置额外排除规则。
4. 文本文件直接以 UTF-8 记录。
5. 如果文件不是 UTF-8 文本，或包含明显二进制内容，则使用 Base64 记录。
6. 每个文件记录 path、type、encoding、length、sha256、mode。
7. 路径必须使用相对路径，不能允许绝对路径或包含 .. 的路径。
8. 解包时必须防止路径穿越。

manifest 建议格式使用 JSON Lines 或清晰的文本块格式。第一版推荐 JSON Lines，因为容易解析。

package.pxf 建议结构：

PXFERRY_PACKAGE_V1
PACKAGE_SHA256=<最终正文 sha256 可在生成结束后写入独立 meta 文件，或在外层 frame header 中记录>
FILE_COUNT=<数量>

{"kind":"file","path":"src/main.py","type":"text","encoding":"utf-8","mode":"0644","length":1234,"sha256":"..."}
<原始 UTF-8 文本内容>
PXFERRY_FILE_END

{"kind":"file","path":"assets/logo.png","type":"binary","encoding":"base64","mode":"0644","length":34922,"sha256":"..."}
<Base64 内容>
PXFERRY_FILE_END

更稳的方案也可以拆成：

* manifest.json
* contents/ 里的逻辑内容
  但为了视觉传输，最终仍然需要合并成一份连续文本包。

请实现：

* build_manifest(repo_path) -> manifest entries
* build_package(repo_path, output_path) -> package file
* unpack_package(package_path, output_dir) -> reconstructed repo
* verify_reconstructed_repo(manifest, output_dir) -> bool

二、分片和帧结构

将 package.pxf 按固定 payload_size 切分成多个 chunk。第一版 payload_size 建议 12KB 到 16KB。

每一帧包含：

* magic：固定值，例如 PXF1
* version：1
* session_id：本次传输随机 ID，8 到 16 字节
* frame_index：当前帧编号，从 0 开始
* total_frames：总帧数
* payload_len：本帧真实 payload 长度
* payload_sha256：本帧 payload 的 sha256
* package_sha256：完整 package.pxf 的 sha256
* payload：分片内容
* padding：补齐固定帧容量

建议二进制 header 结构：
magic: 4 bytes
version: 1 byte
header_len: 2 bytes
session_id: 16 bytes
frame_index: 4 bytes unsigned int
total_frames: 4 bytes unsigned int
payload_len: 4 bytes unsigned int
payload_sha256: 32 bytes
package_sha256: 32 bytes
reserved: 16 bytes

header 总长度可以固定为 115 bytes 左右，也可以对齐到 128 bytes。

帧字节流：
FRAME_BYTES = HEADER + PAYLOAD + PADDING

注意：

1. 真实数据长度由 payload_len 决定。
2. 不要依赖终止标记判断 payload 结束。
3. start marker 和 end marker 只用于视觉同步，不负责真实长度。
4. 接收端必须用 payload_sha256 验证本帧是否有效。
5. 接收端必须用 package_sha256 验证最终合并结果。

请实现：

* split_package(package_bytes, payload_size) -> chunks
* build_frame(session_id, index, total, chunk, package_sha256, frame_capacity) -> frame_bytes
* parse_frame(frame_bytes) -> frame object
* validate_frame(frame) -> bool

三、RGB 色块编码方案

第一版使用稳健编码，不使用裸 24-bit RGB。

推荐编码：

* 使用 16 档 nibble 编码。
* 每个字节拆成高 4 位和低 4 位。
* 每个 4-bit nibble 映射到一个稳定颜色档位。
* 颜色档位建议：
  0 -> 8
  1 -> 24
  2 -> 40
  3 -> 56
  4 -> 72
  5 -> 88
  6 -> 104
  7 -> 120
  8 -> 136
  9 -> 152
  A -> 168
  B -> 184
  C -> 200
  D -> 216
  E -> 232
  F -> 248

每个 RGB 色块可以承载 3 个 nibble，即 12 bit，也就是 1.5 字节。
编码时将 frame_bytes 转为 nibble 流，然后每 3 个 nibble 填入一个色块的 R、G、B。

示例：
byte = 0xAB
高 nibble = A
低 nibble = B
映射为颜色值 168 和 184

色块编码：
block_0.R = nibble_0 对应颜色
block_0.G = nibble_1 对应颜色
block_0.B = nibble_2 对应颜色

解码时：
读取色块中心区域颜色平均值，把每个通道映射到最近的 16 档颜色，再还原 nibble 流，最后每两个 nibble 合成一个字节。

请实现：

* bytes_to_nibbles(data: bytes) -> list[int]
* nibbles_to_bytes(nibbles: list[int], expected_len: int) -> bytes
* nibble_to_color(n: int) -> int
* color_to_nibble(v: int) -> int
* encode_frame_to_image(frame_bytes, width, height, block_size, layout) -> image
* decode_image_to_frame(image, layout) -> frame_bytes

四、窗口和图像布局

第一版默认参数：

* 总窗口大小：640×360
* 数据区域：608×320
* 色块大小：4×4 像素
* 数据区色块数量：152×80 = 12160 blocks
* 每个色块承载 3 nibbles = 1.5 bytes
* 理论容量约 18240 bytes
* 扣除 header 和标记后，payload_size 建议 12KB 到 16KB

推荐布局：

* 外层背景：纯黑
* 四角定位块：高对比固定图案
* 顶部状态栏：显示 session、frame_index、total_frames、progress，可选，不参与解码
* 中间数据区：608×320
* start marker：放在数据区开头的若干色块
* end marker：放在数据区结尾的若干色块
* 数据内容：固定长度区域

四角定位块建议：
左上：白白白白 / 白黑黑白 / 白黑黑白 / 白白白白
右上：红色定位块
左下：绿色定位块
右下：蓝色定位块
这样本地端可以判断方向和裁剪区域。

start marker 和 end marker 不要用单色，使用多颜色序列：
START_MARKER_NIBBLES = [15, 0, 15, 0, 10, 5, 10, 5, 12, 3, 12, 3]
END_MARKER_NIBBLES = [0, 15, 0, 15, 5, 10, 5, 10, 3, 12, 3, 12]

接收端校验：

1. 找到定位块。
2. 裁剪数据区。
3. 按 block_size 读取色块。
4. 解码 nibble 流。
5. 检查 start marker。
6. 读取固定容量 frame bytes。
7. 检查 end marker。
8. parse header。
9. 校验 payload_sha256。

五、发送端设计

云电脑端发送端职责：

1. 接收 repo_path。
2. 生成 package.pxf。
3. 计算 package_sha256。
4. 切分 chunks。
5. 构建 frames。
6. 将每个 frame 编码为 RGB 图像。
7. 在一个小窗口中循环播放图像帧。
8. 帧率默认 3 到 5 FPS。
9. 显示传输状态，但状态文字不要覆盖数据区。
10. 支持暂停、继续、退出。
11. 支持从已有 package.pxf 直接播放，便于调试。

发送端伪代码：

repo_path = user_input
package = build_package(repo_path)
package_hash = sha256(package)
chunks = split(package, payload_size)
session_id = random_16_bytes()
frames = []

for index, chunk in enumerate(chunks):
frame_bytes = build_frame(
session_id=session_id,
index=index,
total=len(chunks),
payload=chunk,
package_sha256=package_hash
)
image = encode_frame_to_image(frame_bytes)
frames.append(image)

while running:
for image in frames:
show_image_in_window(image)
sleep(1 / fps)

六、接收端设计

本地端接收端职责：

1. 定时截图指定屏幕区域。
2. 从截图中定位 PixelFerry 数据窗口。
3. 解码 RGB 色块。
4. 解析 frame header。
5. 校验 frame。
6. 按 session_id 建立接收任务。
7. 保存 frame_index 对应 payload。
8. 忽略重复帧。
9. 记录缺失帧。
10. 全部收齐后合并。
11. 校验 package_sha256。
12. 解包 package.pxf。
13. 重建仓库。
14. 输出接收报告。

接收端伪代码：

received = {}

while not complete:
screenshot = capture_screen_region()
image = locate_or_crop_pixelferry_region(screenshot)
frame_bytes = decode_image_to_frame(image)

```
frame = parse_frame(frame_bytes)
if not frame.valid_magic:
    continue

if sha256(frame.payload) != frame.payload_sha256:
    continue

if frame.index not in received:
    received[frame.index] = frame.payload
    print_progress(len(received), frame.total_frames)

if len(received) == frame.total_frames:
    package = b''.join(received[i] for i in range(frame.total_frames))
    if sha256(package) == frame.package_sha256:
        write package.pxf
        unpack_package(package.pxf, output_dir)
        break
    else:
        report error and keep receiving
```

七、截图与解码稳健性

必须考虑这些问题：

1. 截图区域偏移：
   * 第一版允许用户手动指定截图区域。
   * 第二版再做自动定位四角标记。
   * 如果定位失败，跳过该帧。
2. DPI 缩放：
   * 尽量使用固定窗口大小。
   * 接收端允许配置 scale_factor。
   * 解码时读取每个色块中心区域平均值，不读边缘。
3. 颜色漂移：
   * 使用 16 档 nibble 编码。
   * 解码时映射到最近颜色档位。
   * 如果颜色距离最近档位超过阈值，认为该色块不可靠。
   * 不可靠色块导致该帧丢弃，等待下一轮。
4. 远程桌面压缩：
   * 避免 1×1 单像素编码。
   * 使用 4×4 色块。
   * 如果坏帧率高，支持切换到 6×6 或 8×8 色块。
   * 支持降低 FPS。
5. 丢帧：
   * 不需要 ACK。
   * 发送端循环播放。
   * 接收端根据 frame_index 补齐。
6. 重复帧：
   * 接收端忽略已经收到的 frame_index。
7. 错误帧：
   * payload_sha256 不匹配就丢弃。
   * package_sha256 最终不匹配就不解包。

八、安全与边界要求

1. 只允许读取用户指定 repo_path。
2. 默认排除 .git、node_modules、.venv 等目录。
3. 不要读取系统目录。
4. 不要读取浏览器、SSH、密钥、凭据、环境变量等敏感位置。
5. 不要实现隐藏窗口、后台驻留、自启动、规避监控、绕过权限等功能。
6. 发送窗口必须可见，并明确显示 PixelFerry 正在传输用户指定仓库。
7. 接收端只处理 PixelFerry 格式的数据帧。
8. 解包时必须防止路径穿越。
9. 输出目录由用户显式指定。
10. 如果输出文件已存在，默认不要覆盖，除非用户显式开启 overwrite。
11. README 中明确说明本工具仅用于授权环境中的自有仓库搬运。

九、第一版实现建议

优先实现以下功能：

1. package.py：把仓库打包成文本化 package.pxf。
2. manifest.py：生成和解析 manifest。
3. framing.py：分片、frame header 构建、解析和校验。
4. codec.py：bytes <-> nibble <-> RGB image。
5. sender.py：把 frames 保存为 PNG 序列，后续再做播放窗口。
6. receiver.py：从 PNG 序列解码并重建 package。
7. tests：做完整 roundtrip 测试。

第一版可以先不做实时截图窗口，先做离线 PNG 序列验证：

* sender 生成 frame_000001.png、frame_000002.png。
* receiver 从这些 PNG 读取并还原。
* roundtrip 成功后，再接入屏幕播放和截图。

十、最小命令设计

打包：
python -m pixelferry.package --repo /path/to/repo --out package.pxf

生成帧图片：
python -m pixelferry.sender --package package.pxf --out frames/ --width 640 --height 360 --block 4

从帧图片还原：
python -m pixelferry.receiver --frames frames/ --out received_package.pxf

解包：
python -m pixelferry.unpack --package received_package.pxf --out restored_repo/

完整发送端播放：
python -m pixelferry.play --repo /path/to/repo --fps 5 --window 640x360

完整接收端截图：
python -m pixelferry.capture --region x,y,w,h --out restored_repo/

十一、测试要求

请写测试覆盖：

1. 空仓库。
2. 单个文本文件。
3. 多层目录。
4. 英文代码文件。
5. 换行符保持。
6. 文件名含空格。
7. 二进制文件 Base64 还原。
8. frame header roundtrip。
9. RGB 编码解码 roundtrip。
10. 缺失帧后通过重复帧补齐。
11. 坏帧被 sha256 拒绝。
12. 路径穿越被拒绝。
13. package 总 sha256 校验失败时不解包。

十二、README 需要说明

README 需要包含：

* PixelFerry 是什么。
* 使用场景。
* 不适合的场景。
* 安全边界。
* 原理图。
* 快速开始。
* 参数说明。
* 常见问题。
* 性能预估。
* 如何调整窗口大小、色块大小、FPS。
* 如何处理颜色漂移和坏帧率高的问题。

性能预估写法：
在 640×360 窗口、608×320 数据区、4×4 色块、16 档 nibble 编码下，每帧 payload 大约 12KB 到 16KB。以 5 FPS 计算，理论有效吞吐约 60KB/s 到 80KB/s。实际速度取决于远程桌面压缩、截图质量和坏帧率。

十三、设计取舍

请明确说明为什么不用二维码：
二维码适合短文本和链接，但不适合持续传输代码仓库。它容量低、分片多、识别慢，且在远程桌面缩放和截图压缩下容易影响识别效率。PixelFerry 使用 RGB 色块直接编码数据，不需要二维码识别流程，画面有效载荷更高，更适合仓库级传输。

请明确说明为什么不用剪贴板：
剪贴板虽然可行，但会干扰用户正常复制粘贴。PixelFerry 不占用剪贴板，只使用可见小窗口和本地截图，适合后台低干扰接收。

请明确说明为什么不用网络：
目标环境下网络不互通或受白名单限制，因此不依赖 HTTP、Git、rsync、网盘或任何第三方服务。

十四、输出要求

请先输出设计文档，再输出实现计划，最后给出第一批代码文件。
代码要尽量简单，优先可读性和可测试性。
不要引入复杂依赖。
如需图像读写，允许使用 Pillow。
如需截图，后续可以使用 mss 或平台截图 API，但第一版先用 PNG 序列完成闭环验证。
