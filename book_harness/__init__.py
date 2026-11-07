"""扫描版人文书 -> 阅读版 Markdown / EPUB 的本地 harness。

零第三方依赖即可运行：
    ./run.sh selftest     # 用合成数据验证除模型推理外的全链路
    ./run.sh probe --pdf book.pdf --pages 1-3
    ./run.sh all    --pdf book.pdf

stdout 只输出一行 JSON 报告，日志全部走 stderr，便于外层 harness 捕获断言。
"""
