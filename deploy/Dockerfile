# corespine 可复现测试镜像 —— 多阶段、基于 uv。
#
# 一键容器:`docker build` 装好包 + dev,`docker run` 默认跑整套 pytest 自检。
# 构建 / 运行命令见 deploy/README.md(构建上下文为仓库根)。

# ---- builder:在独立 venv 里装入包 + dev ----------------------------------------------
FROM python:3.12-slim AS builder

# 从官方镜像拷入 uv 静态二进制(免装 pip,构建可复现)。
COPY --from=ghcr.io/astral-sh/uv:latest /uv /usr/local/bin/uv

ENV UV_LINK_MODE=copy \
    VIRTUAL_ENV=/opt/venv \
    PATH="/opt/venv/bin:$PATH"

WORKDIR /app

# 与宿主隔离的独立 venv。
RUN uv venv /opt/venv

# 先拷打包元数据 + 源码(可编辑安装需源码在场),再装 —— 与 `make install` 同形。
COPY pyproject.toml README.md ./
COPY src ./src
RUN uv pip install -e ".[dev]"

# ---- runtime:只带 venv + 跑测试 / demo 所需的源码 ------------------------------------
FROM python:3.12-slim AS runtime

ENV VIRTUAL_ENV=/opt/venv \
    PATH="/opt/venv/bin:$PATH" \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

WORKDIR /app

COPY --from=builder /opt/venv /opt/venv
# 可编辑安装的 .pth 指向 /app/src,故运行阶段须保持同一绝对路径。
COPY pyproject.toml README.md ./
COPY src ./src
COPY tests ./tests
COPY examples ./examples

# 默认 CMD:跑测试套件(一键自检)。
# 改跑离线 demo:`docker run --rm corespine-test python examples/quickstart.py`
CMD ["python", "-m", "pytest", "-q"]
