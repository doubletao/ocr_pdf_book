# 扫描版人文书 → 阅读版 Markdown / EPUB（本地 harness）

面向：**几乎纯文字、带章节标题、偶有表格但可丢、插图不重要**的人文类扫描书。
目标是可重排、可调字号、能搜索做笔记的阅读版，不是像素级复刻原书。

底层用 PaddleOCR-VL-1.6 产线（版面分析 + 阅读顺序 + 0.9B VLM 逐块识别），
本 harness 负责它不管的那一段：**逐页结果归一、剔除页眉页码、重建章节标题、
拼接扫描硬换行、合成一本、打 EPUB**。

---

## 1. 命令（可直接粘进本地 harness）

```bash
# Windows（PowerShell / CMD）用 run.cmd；POSIX 用 run.sh / make
cd ocr_pdf_book

# ① 零依赖自测：不需要 GPU、不需要装 Paddle，先验证全链路代码是通的
run.cmd selftest          # 或 ./run.sh selftest

# ② 装环境（config.json 里 device=auto：有 NVIDIA 卡装 GPU 版，否则 CPU 版；
#    需要 Python 3.9–3.13，推荐先建 venv：py -3.12 -m venv .venv）
run.cmd setup
# 只打印不执行：run.cmd setup --dry-run

# ③ 只跑前 3 页，拿到真实 block_label 分布 —— 必做，别跳过
run.cmd probe --pdf .\book.pdf --pages 1-3
type dist\probe_labels.json

# ④ 按 ③ 的结果把 config.json 的 drop_labels / title_level 校准一遍

# ⑤ 整本一条龙
run.cmd all --pdf .\book.pdf
# 限页试跑：run.cmd all --pdf .\book.pdf --pages 1-20
# 指定书名：run.cmd all --pdf .\book.pdf --title "书名"
# 指定设备（覆盖 config）：--device auto|cpu|gpu，默认取 config.json 的 device

# ⑥ 图形界面：多本书排队批量转换（进度 / 日志 / 断点续跑）
run.cmd gui
```

GUI 说明：

- 「添加 PDF…」可多选；「开始」后按队列**串行**处理（CPU 模式并行只会互相抢核）；
- 「设备」下拉框选 自动 / CPU / GPU（对应 `--device auto|cpu|gpu`），默认读 `config.json`；
- 每本书独立 `work/<key>/`、`dist/<key>/` 目录，多本书互不覆盖；
- 表格列显示总页数 / 逐页进度 / 状态 / 结果，底部日志窗口只显示关键行，
  完整输出在 `work/<key>/console.log`；
- 「停止当前」或直接关窗口，进度已落盘；「重新入队」（或双击该行）续跑，
  不会重跑已完成的页。

**设备选择**（`config.json` 的 `device`，或任意命令的 `--device` 覆盖）：

| 值 | 行为 |
|---|---|
| `auto`（默认） | 优先 GPU：paddle 是 CUDA 编译版且探测到可用卡才用 `gpu`，否则回落 `cpu`；`setup` 阶段用 `nvidia-smi` 探测硬件决定装哪个 wheel |
| `gpu` | 强制 GPU；paddle 是 CPU 版时**立即报错**提示改用 GPU 版或 `--device cpu` |
| `cpu` | 强制 CPU |

日志会打印 `[parse] device=cpu (requested=auto)` 与
`[device] auto -> cpu（CUDA 编译版但看不到可用设备）` 说明回落原因。
注意：GPU 推理要求装的是 `paddlepaddle-gpu` wheel（换机器跑 GPU 时重新
`run.cmd setup` 即可，代码不变）。GPU 版 wheel 在**没有 NVIDIA 驱动**的
机器上也能跑（自动回落 CPU）——`parse.py` 的 `_patch_cuda_probe` 把
paddlex 的「查 GPU 算力」包成失败返回 None，否则它不看驱动直接查 CUDA
会抛 `CUDA error(35)`；有卡的机器探测成功，GPU 行为不受影响。

**断点续跑**：`parse`/`probe`/`all` 每识别完一页立即原子写入 `work/pages.json`，
进程中断（Ctrl+C、停止按钮、断电）后重跑同一命令即自动跳过已处理页，
只补缺失的页（只认目标范围开头的连续历史，中间的洞一律重跑以保证页序）。


产物：

| 文件 | 内容 |
|---|---|
| `work/pages.json` | 归一后的逐页块，**每页完成即落盘**，断点续跑的依据 |
| `work/raw/page_XXXX/` | 官方产线原始逐页 JSON / Markdown，留作对照 |
| `dist/probe_labels.json` | 前几页的真实标签分布 + 样例 |
| `dist/阅读版.md` | 正文阅读版 |
| `dist/阅读版.epub` | 带目录的 EPUB |
| `dist/report.json` | 统计报告 |

**stdout 只有一行纯 JSON**，外层 harness 直接 `json.loads`
并断言 `ok == true` / `pages` / `chars` 即可；失败退出码为 1。

**进度走 stderr**：解析按页发 `[progress] {"stage":"parse","page":N,"total":M,...}`
（续跑时首个事件带 `resumed:true`），清洗/打包完成时发 `stage=clean` / `stage=epub`
（行首统一是 `[progress] ` 前缀），GUI 和外层 harness 靠它算进度；其余日志也在 stderr。


---

## 2. 安装命令原文（setup 实际执行的）

```bash
# device=auto：setup 用 nvidia-smi 探测，有 NVIDIA 卡装 GPU 版，否则 CPU 版
# CPU 版（无 N 卡）：
python -m pip install paddlepaddle==3.2.1 -i https://www.paddlepaddle.org.cn/packages/stable/cpu/
python -m pip install -U "paddleocr[doc-parser]>=3.6.0"
# Windows 额外一步（setup 自动执行）：PaddleOCR-VL 1.6 配套 safetensors
python -m pip install https://xly-devops.cdn.bcebos.com/safetensors-nightly/safetensors-0.6.2.dev0-cp38-abi3-win_amd64.whl

# NVIDIA GPU + 驱动支持 CUDA 12.6（把 config.json device 改成 gpu）：
python -m pip install paddlepaddle-gpu==3.2.1 -i https://www.paddlepaddle.org.cn/packages/stable/cu126/
python -m pip install -U "paddleocr[doc-parser]>=3.6.0"
```

setup 在 Windows 上还会自动打一个补丁：把 paddlex 里
`paddleocr_vl/_config.py` 的 `self.fuse_rms_norm = True` 改成 `False`
——官方 wheel 缺 `fused_rms_norm_ext` kernel，不关掉会直接崩。
setup 还会强制 `pip install -U "protobuf>=7.36.1"`——sentencepiece 0.2.3 的
pb2 用 protobuf 7.36 gencode 生成，运行时留在 6.x 会报
`incompatible Protobuf Gencode/Runtime versions`。

注意：不要同时装 CPU 版和 GPU 版 PaddlePaddle；**Python 必须 3.9–3.13**
（PaddlePaddle 没有 cp314 wheel，本机 3.14 装不上，用 `py -3.12 -m venv .venv`
单独建环境）；1.6 模型卡提示需配套版本的 `safetensors`；
macOS / 内网机器走官方 Docker（`paddleocr-vl:latest-nvidia-gpu` 或 `-offline` 镜像）。

### 打包 Windows exe（build_exe.bat）

```bash
# 先确认 venv 里装的是 GPU 版 wheel（决定 exe 能否用 GPU）：
run.cmd setup --device gpu     # 有卡/无卡机器都能装；装完 is_compiled_with_cuda=True
build_exe.bat                  # PyInstaller onedir，约 6 分钟
```

- 产物 `dist\BookScan2EPUB\`（~3.5 GB，含 paddlepaddle-gpu + nvidia CUDA
  运行库 19 个 DLL），**拷整个文件夹**到目标机器即可；
- **同一个 exe 两用**：有 NVIDIA 驱动的机器 `auto` 自动走 GPU（4060Ti 实测
  路径），无卡机器回落 CPU；`--device gpu` 遇无卡立即报错；
- exe 内 paddle 版本 = **打包时** venv 里的 wheel：CPU wheel 打出的包永远
  用不了 GPU，要 GPU 就先 `run.cmd setup --device gpu` 再重打包；
- 无参数启动 GUI，带参数透传 CLI（`BookScan2EPUB.exe probe --pdf ...`）；
- 模型不进包：首次运行联网下载到 `%USERPROFILE%\.paddlex`（~1.9 GB），
  离线机可先在有网机下载后整目录拷贝，或设 `PADDLE_PDX_CACHE_HOME` 指向。

---

## 3. 为什么这么设计

- **不调 `restructure_pages()`**。人文书几乎没跨页表格，官方那个方法默认
  `concatenate_pages=False`，且不同版本 API 不一定都有；本 harness 自己按页序
  拼成一本，少一层依赖。
- **逐页渲染再推理**：实测 paddlex 3.7.2 的 `predict()` 收到 PDF 或多图目录会
  **整批推理完才一次性 yield**，中途 kill 一页都留不下、进度条最后才动。
  因此全程固定为「渲染单页 PNG → predict 这一张 → 落盘」——逐页进度、
  断点续跑、probe 只跑指定页三件事同时成立；复用同一 pipeline 对象逐页调用
  没有额外初始化开销（单页 304s vs 批量 606s/2 页，等价）。

- **三层兜底解析**：先读 `save_to_json` 落盘的 `parsing_res_list`，失败扒结果
  对象属性，再失败从逐页 Markdown 按空行切块。换 PaddleOCR 小版本不至于全崩。
- **页眉不用硬编码**：除 `drop_labels` 外，还会统计「跨页重复出现的短文本」
  （出现率 ≥ 60%、少于 40 字、至少 5 页才启用），自动识别书名/章名页眉；
  纯数字/罗马数字/中文数字的独立短块当页码剔除。
- **footnote 默认保留**。人文书的注释常就是正文的一部分，别当页脚误杀。
- **表格默认丢弃**（`drop_tables: true`），符合"表格不重要"的口径；想要就改 false。
- **硬换行只在 block 内部拼**，绝不跨 block 合并 —— 版面分析的 block 通常已是
  段落级，跨块拼接会把两个自然段黏成一段。
- **EPUB 是纯 Python 生成的**（zipfile + 最小 EPUB2），不依赖 pandoc，
  因此 selftest 在裸机器上也能验证导出链路。想要更成熟的排版可以把
  `export.py` 换成 `pandoc 阅读版.md -o 阅读版.epub --toc`。

---

## 4. 校准 config.json 的方式

`probe` 之后看 `dist/probe_labels.json`：

```json
{
  "labels": {"text": 28, "doc_title": 1, "page_number": 3, "header": 3},
  "samples": {"page_number": "142", "header": "人文丛书·第三辑"}
}
```

- 把确认是页眉/页脚/页码的**标签名**加进 `drop_labels`；
- 若书里有独立成块的年份、卷次编号（如 `1900`、`卷三`）被误当页码删掉，
  把 `drop_standalone_numbers` 改成 `false`，只靠标签和重复检测来剔页眉页码；
- 把确认是标题的标签按层级加进 `title_level`（1 → `#`，2 → `##`）；
- 章节正则 `chapter_pattern` 负责把 `label=text` 但内容是"第一章 …"的短行提成标题；
- 扫描版如果每页行数切得太碎，可把 `merge_lines_within_block` 保持 true。

改完直接 `run.cmd clean` 再 `run.cmd epub`（POSIX：`./run.sh clean && ./run.sh epub`），
**不用重跑模型**（读的是 `work/pages.json`）。

---

## 5. 边界

- 输出是**语义结构排版**（标题/段落/脚注/阅读顺序），不保留字体字号、页边距、
  插图原位。要"翻起来跟原书一样"得另做双层 PDF 文字层，本 harness 不做。
- 错别字：PaddleOCR-VL 是生成式识别，**不输出 diff、不给逐字置信度、不提供
  原文/修订对照**。原方案里那个"可审计的本地小模型校对层"本期没接，
  如果人文书里的人名、年号、引文不能容忍静默误改，再在 `clean` 之后挂一层。
- `selftest` 验证的是**除模型推理外的全链路**；模型侧（`setup` / `probe`）必须在
  你本地有真实 PDF 的环境下首次验证。
