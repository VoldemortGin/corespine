# corespine — 常用开发 / CI 命令。
#
# `make` 或 `make help` 列出全部目标。目标默认使用项目 venv(.venv);
# 覆盖解释器:  make test PYTHON=python3.12
#
# 始终从包根运行。

.DEFAULT_GOAL := help
PYTHON ?= .venv/bin/python
VENV   ?= .venv

# ---- 环境 ----------------------------------------------------------------------------

.PHONY: install
install: ## 建 .venv(uv)并以 dev extra 可编辑安装(常规开发装法)
	uv venv $(VENV)
	VIRTUAL_ENV="$(CURDIR)/$(VENV)" uv pip install -e ".[dev]"

# ---- 质量门 --------------------------------------------------------------------------

.PHONY: ci
ci: lint typecheck test ## 本地 CI 门:lint + 类型检查 + 测试(与 GitHub Actions 同形)

.PHONY: test
test: ## 跑测试套件
	$(PYTHON) -m pytest -q

.PHONY: lint
lint: ## ruff 静态检查(风格 + import 顺序 + 死代码)
	$(PYTHON) -m ruff check src tests

.PHONY: typecheck
typecheck: ## mypy --strict 类型检查(出货代码 src)
	$(PYTHON) -m mypy

.PHONY: fmt
fmt: ## ruff 自动格式化(src / tests / examples)
	$(PYTHON) -m ruff format src tests examples

# ---- demo ----------------------------------------------------------------------------

.PHONY: demo
demo: ## 跑离线快速上手示例(期望末行 "corespine OK")
	$(PYTHON) examples/quickstart.py

# ---- meta ----------------------------------------------------------------------------

.PHONY: help
help: ## 列出可用目标
	@grep -hE '^[a-zA-Z_-]+:.*?## ' $(MAKEFILE_LIST) \
		| sort \
		| awk 'BEGIN{FS=":.*?## "}{printf "  \033[36m%-12s\033[0m %s\n", $$1, $$2}'
