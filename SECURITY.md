# 安全说明

- key 只从环境变量、仓库根目录的 `.env`（已在 `.gitignore` 里），或者 `EVOLINK_KEY_FILE` 这类变量指向的文件里读，代码只读不写、不打印；`doctor` 只显示配没配，不显示 key 本身。RunningHub 返回的内容和报错里要是带着 key，会先换成 `[key]` 再往下传。
- 每次付费调用记在项目文件夹的 `gen/ledger.jsonl`，记的是任务号和花费，不记 key。
- 关键帧、首尾帧图片和配音会传到 Evolink 和 RunningHub，按它们自己的保存规则存放。别传你没有权利处理的素材。
- 发现安全问题（比如 key 出现在日志、账本或项目文件夹里），请在 GitHub 上用 [Security Advisory](https://github.com/garycaogan/lifecycle-oneshot/security/advisories/new) 私下报告，不要开公开 issue。
