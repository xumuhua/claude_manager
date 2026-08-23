# quant（量化专家）经验沉淀

- 远端 115.190.14.181（claude 用户）；环境缺 ensurepip 时他用 `python3 -m venv --without-pip` + get-pip.py 自助解决——环境类小障碍可直接让他自己处理。
- 每日流水线 holding.py 一条链成熟稳定；派发一次性专题走"scp 提示词+headless 后台+GitHub 回传"模式（见 [[quant-oneoff-task-pattern]]）。
- headless 任务提示词禁引用 PDF（kimi 网关 400），样例一律转纯文本 .md。
