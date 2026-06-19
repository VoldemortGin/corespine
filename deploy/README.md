# deploy —— 一键测试容器

一个多阶段、基于 uv 的可复现镜像:`docker build` 装好 `corespine` + dev 依赖,
`docker run` 默认跑整套 `pytest` 自检。无需本机装 Python / uv。

> 构建上下文是**仓库根**(`Dockerfile` 用显式 `COPY` 取 `pyproject.toml` / `README.md` /
> `src` / `tests` / `examples`),`-f` 指到本目录的 Dockerfile。

## 构建

```bash
# 从仓库根运行
docker build -f deploy/Dockerfile -t corespine-test .
```

## 运行

```bash
# 默认 CMD:跑整套测试(一键自检)
docker run --rm corespine-test

# 改跑离线快速上手 demo(期望末行 "corespine OK")
docker run --rm corespine-test python examples/quickstart.py
```

`docker build` + `docker run` 两条命令即完成"构建镜像 → 跑通测试套件"的一键闭环。

## 说明

- **多阶段**:builder 阶段在 `/opt/venv` 建独立 venv 并 `uv pip install -e ".[dev]"`;
  runtime 阶段只拷该 venv 与跑测试所需源码,镜像更小。
- 可编辑安装的链接指向 `/app/src`,故两个阶段都把源码放在同一绝对路径 `/app/src`。
- `.dockerignore` 收窄上下文(加速构建);因 Dockerfile 用显式 `COPY`,镜像内容不依赖它。
