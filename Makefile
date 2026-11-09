PYTHON ?= python3
PDF    ?= ./book.pdf
PAGES  ?=

.PHONY: help batch selftest setup probe parse clean epub all check

help:
	@echo "make batch                     # 扫描 book/ 下全部 PDF，结果写回 book/"
	@echo "make selftest                  # 零依赖自测"
	@echo "make setup                     # 安装 PaddlePaddle + PaddleOCR-VL 依赖"
	@echo "make probe PDF=./book.pdf      # 只跑前 3 页，打印实际 block_label"
	@echo "make all   PDF=./book.pdf      # 解析 + 清洗 + EPUB 一条龙"
	@echo "make all   PDF=./book.pdf PAGES=1-20"

selftest:
	./run.sh selftest

setup:
	./run.sh setup

probe:
	./run.sh probe --pdf $(PDF) $(if $(PAGES),--pages $(PAGES),--pages 1-3)

parse:
	./run.sh parse --pdf $(PDF) $(if $(PAGES),--pages $(PAGES),)

clean:
	./run.sh clean

epub:
	./run.sh epub

all:
	./run.sh all --pdf $(PDF) $(if $(PAGES),--pages $(PAGES),)

check: selftest

batch:
	./run.sh batch
