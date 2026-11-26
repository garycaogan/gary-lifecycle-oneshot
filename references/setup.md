# 安装、自检和费用

照这页装，十几分钟能装好，不算第一次配音时自动下载的转写模型（约 1.6 GB）和字幕字体（约 73 MB）。装完先跑一遍 `doctor` 自检，全是 ✓ 再开工。Apple 芯片的 Mac 上从头跑通过；Windows、Linux 上转写换成 faster-whisper，代码支持，但还没实测。

## 01 放好 skill

从 GitHub 克隆到放 skill 的地方：DeepSeek Harness（dsh）放 `~/.dsh/skills/`，Claude Code 放 `~/.claude/skills/`（只给一个项目用就放项目里的 `.dsh/skills/` 或 `.claude/skills/`），Codex 放 `.agents/skills/`。下面以 dsh 为例，用别的就换掉路径：

```bash
git clone https://github.com/garycaogan/gary-lifecycle-oneshot ~/.dsh/skills/lifecycle-oneshot
```

后面的命令都在这个文件夹里执行。

## 02 装 Python 依赖

要 Python 3.11 或更新。建一个只给它用的虚拟环境，再装依赖：

```bash
python3 -m venv .venv
```

```bash
.venv/bin/pip install -r requirements.txt
```

Mac 自带的 Python 常是 3.9，不够的话装 uv，把第一条换成 `uv venv --seed --python 3.12 .venv`。Windows 上第二条换成 `.venv\Scripts\pip install -r requirements.txt`，后面的 `.venv/bin/python` 都换成 `.venv\Scripts\python`。

requirements.txt 里装的东西：

| 包 | 用来做什么 |
| --- | --- |
| httpx | 调接口、下载字体 |
| pillow、numpy | 画字幕、处理帧、算 SSIM |
| imageio-ffmpeg | 带一份现成的 ffmpeg，有 libx264、PNG、xfade、loudnorm |
| mlx-whisper / faster-whisper | 中文转写。Apple 芯片的 Mac 装 mlx-whisper，其他电脑装 faster-whisper，pip 会按电脑自动挑 |

国内连不上 Hugging Face 下载转写模型时，在 `.env` 里写 `HF_ENDPOINT=https://hf-mirror.com`。

## 03 字幕字体

字幕只用开源可商用的字体，都是 SIL Open Font License 1.1，清单、来源、大小和 sha256 在 `fonts/fonts.json`，授权文件跟着字体放在 `fonts/`：

| 字体 | 用在 | 怎么来 |
| --- | --- | --- |
| 马善政毛笔楷书 | 墨韵标题 | 随包带 |
| 志莽行书 | 金书标题 | 下载 |
| 霞鹜文楷 | 墨韵、宋韵古诗 | 下载 |
| 思源黑体 | 旁白字幕、拼音 | 下载 |
| 思源宋体 | 出处、金书古诗、宋韵标题 | 下载 |

下载的那几个体积大，不打进包，第一次跑：

```bash
.venv/bin/python scripts/oneshot.py doctor --fetch-fonts
```

它从 Google Fonts 官方仓库和霞鹜文楷作者的 GitHub 发布页下载，核对大小和 sha256，对不上就不保存。下载不动的话，照 `fonts/fonts.json` 里的地址手动下好，按里面的文件名放进 `fonts/`，再跑一次 `doctor` 核对。

## 04 配 key

| key | 用来做什么 | 去哪拿 |
| --- | --- | --- |
| `EVOLINK_API_KEY` | 豆包配音、GPT Image 2.5 关键帧、Gemini 审片 | evolink.ai 控制台的 API Keys |
| `RUNNINGHUB_API_KEY` | MiniMax H3 首尾帧出片（YZ金鱼 的终极篇工作流，plus 机器） | runninghub.cn 个人中心的「API 调用」 |
| `APIMART_API_KEY` | Suno 配乐；有现成配乐的可以不配 | apimart.ai 控制台的令牌管理 |

把 `.env.example` 复制成 `.env`，三把 key 填在等号后面。`.env` 只留在自己电脑上。也可以设同名的系统环境变量，环境变量优先。

## 05 自检

```bash
.venv/bin/python scripts/oneshot.py doctor --fetch-model
```

它不花钱，第一次加 `--fetch-model` 顺便把转写模型（约 1.6 GB）下好。在 DeepSeek Harness、Codex 这类把命令关在工作区沙盒里的 Agent 里用，一定先在自己的终端里跑这一步：沙盒写不进 `~/.cache`，不先下的话第一次配音会把模型改下到项目旁边的 `.oneshot-models`，每个工作区一份。它查这些东西，不会显示 key 本身：

- Python 版本、依赖、转写和转写模型、ffmpeg 的几个功能
- 字体在不在、对不对
- 首尾帧节点图
- 三把 key 配了没有（RunningHub 那行顺便显示余额）

## 06 开工

一条片子一个项目文件夹，放在 skill 文件夹外面，里面先写一份 `film.json`（照 `references/film-format.md`，范例 `examples/bamboo/film.json`）。然后按顺序跑：

```bash
.venv/bin/python scripts/oneshot.py write --project ~/oneshot/bamboo
.venv/bin/python scripts/oneshot.py voice --project ~/oneshot/bamboo
.venv/bin/python scripts/oneshot.py keyframes --project ~/oneshot/bamboo
.venv/bin/python scripts/oneshot.py plan --project ~/oneshot/bamboo
.venv/bin/python scripts/oneshot.py h3 --project ~/oneshot/bamboo --dry-run
.venv/bin/python scripts/oneshot.py h3 --project ~/oneshot/bamboo
.venv/bin/python scripts/oneshot.py music --project ~/oneshot/bamboo
.venv/bin/python scripts/oneshot.py captions --project ~/oneshot/bamboo
.venv/bin/python scripts/oneshot.py cut --project ~/oneshot/bamboo
.venv/bin/python scripts/oneshot.py check --project ~/oneshot/bamboo
```

`voice`、`keyframes`、`music` 互不依赖；`plan` 要等配音出来，`h3` 要等 `plan` 和关键帧，`cut` 要等 `h3`、`captions`，有配乐就混进去。

## 07 一条片子大概花多少

竹子那条（2026-11-06，114 秒、14 段）按账本算，走通一遍的第三方花费：

| 项 | 花费 |
| --- | --- |
| 关键帧 | 15 张 6.37 元 |
| H3 | 14 段 9.18 元，每段 0.4–0.9 元。按 RH 币 399 元 18 万个折算，你的 RH 币单价不一样的话，在 `.env` 里写 `RUNNINGHUB_COIN_CNY` |
| 配音 | 整条一遍 2.40 元。豆包按音频长度收费，只重读一句几分钱 |
| 配乐和审片 | 0.61 元 |
| 合计 | 18.56 元 |

我们第一次做另外多花了 15.73 元踩坑，主要是配音整条重读了好几遍，现在的做法是读错哪句只重读那一句。

每段 H3 跑 3–7 分钟，各段同时提交，你的 RunningHub 账号能同时跑几个任务，决定一共要等多久。队列满了工具会自己等；个别 plus 机器缺模型文件，几秒就报错，工具会自动换机器重提。
