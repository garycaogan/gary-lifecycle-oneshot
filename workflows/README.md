# H3 首尾帧节点图

`h3-ultimate-fl.json` 改自 RunningHub 上 YZ金鱼 发布的公开工作流「MiniMax H3 终极篇-文武双全」的 API 节点图（workflowId `2109907990638850050`，AI 应用 `2110357037929558018`，作者在 B 站的讲解视频 BV1LKJm6VELc）。版权归原作者，附在这里只是为了调用方便：提交任务时要带上整张图，任务按这个 workflowId 跑在 RunningHub 的 plus 机器上。

作者原图用的是参考图生视频节点（`MiniMaxH3ReferenceToVideo`），只会「参考」图，不会从图 1 开始、停在图 2。2026-11-06 用 API 取了最新版，里面没有首尾帧那一组，所以我们在原图上改了这几处：

- **换节点。** 节点 265 换成 ComfyUI 官方的首尾帧节点 `MiniMaxH3ImageToVideo`（源码在 Comfy-Org/ComfyUI 的 `comfy_extras/nodes_minimax_h3.py`）。它的输入是 clip、vae、prompt、width、height、length，加上可选的 `first_frame`、`last_frame`；输出是 positive 和 latent，和原来那个节点的输出位置一样，后面的采样器不用改。
- **接图。** 首帧接 LoadImage 节点 51，尾帧接 LoadImage 节点 49。
- **删节点。** 其余的参考图、参考音频 LoadImage / LoadAudio 节点删掉了。
- **其余不动。** UNET（`minimax_h3_hybrid_fl2va_ref2va_b25-49-int8`，同一个模型两种生成都能跑）、两个 LoRA、SelfLift 二次采样、稀疏注意力、24 fps 出片都照原图。

提示词、时长、画幅、种子和两张关键帧，每次提交时由 `scripts/runninghub.py` 填进去。

首尾帧节点的提示词有固定写法（MiniMax 官方的 FL2VA 格式）：

- 第一行对齐句：「Picture 1 (from Shot 1) aligns with the 0.00-second mark …; Picture 2 (from Shot 1) aligns with the S.SS-second mark …」，`S.SS` 是这段的实际时长。
- 空一行，再写 `integrated_multimodal_description`、`overall_soundscape`、`non_diegetic_music`。

`runninghub.fl_prompt` 自动拼。

关键帧会被拉伸或居中裁切到画布大小，所以工具先把每张关键帧裁成 928×1664 再上传，首帧和尾帧的取景就一模一样。

作者更新了工作流，或者你想换成自己的版本，在 RunningHub 上导出 API 格式的节点图，照上面几处改好替换这个文件。节点号变了的话，`scripts/runninghub.py` 开头的节点表也要跟着改，`oneshot.py doctor` 和 `h3 --dry-run` 会检查节点在不在。

模型本身是 MiniMax H3，遵守 [MiniMax H3 Community License](https://huggingface.co/MiniMaxAI/MiniMax-H3/blob/main/LICENSE)。
